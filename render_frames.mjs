/* 逐帧渲染 three.js 地形并合成 MP4（零额外依赖，Node 22 自带 WebSocket 走 CDP）
 *
 *   node render_frames.mjs                       默认 720x1280 / 24fps / 10s
 *   node render_frames.mjs "" 1080 1920 30 12   自定义尺寸 / 帧率 / 时长
 *   TEST_FRAMES=3 node render_frames.mjs         只出前 3 帧自检
 *   FRAME_LIST=0,72,144,204,239 node ...         只出指定关键帧做构图预览
 *   TRACK_STYLE="trkw=8&trkc=ffcc00" node ...     覆盖轨迹粗细/颜色（也可用 track_style.json）
 *
 * 轨迹样式：调参台右侧「复制剪辑参数」得到的串，可直接放进 track_style.json，
 * 或作为 TRACK_STYLE 环境变量传入；两者都给出时以环境变量为准。
 *
 * 前置：python3 -m http.server 8766 --directory D:\agent\svg3d\terrain-hd
 * 进度实时写入 render.log（即使控制台被缓冲也能查看）。
 */
import { spawn, spawnSync } from 'node:child_process';
import { writeFileSync, appendFileSync, mkdirSync, existsSync, statSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import os from 'node:os';

const ROOT = fileURLToPath(new URL('.', import.meta.url)).replace(/[\\/]+$/, '');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 9400 + (process.pid % 300);
const URL_BASE = process.argv[2] || 'http://127.0.0.1:8766/video.html';
const W = +(process.argv[3] || 720);
const H = +(process.argv[4] || 1280);
const FPS = +(process.argv[5] || 24);
let SECONDS = +(process.argv[6] || 10);
/* 页面「镜头控制」里改过分镜时长时，未显式传第 6 个参数就按它渲染，
   否则只渲前 10 秒会丢掉后面的关键帧。显式传参始终优先。
   上限与页面「总时长」一致（3–60 s），避免页面加长后这里仍按旧上限截断。 */
if (process.argv[6] === undefined) {
  let dur = null;
  const m = /(?:^|&)dur=([0-9.]+)(?:&|$)/.exec(process.env.TRACK_STYLE || '');
  if (m) dur = +m[1];
  if (dur === null) {
    try {
      const sf0 = path.join(ROOT, 'track_style.json');
      if (existsSync(sf0)) {
        const o0 = JSON.parse(readFileSync(sf0, 'utf8'));
        dur = +(typeof o0 === 'string'
          ? ((/(?:^|&)dur=([0-9.]+)(?:&|$)/.exec(o0) || [])[1])
          : o0.dur);
      }
    } catch (e) { }
  }
  if (isFinite(dur) && dur >= 3 && dur <= 60) SECONDS = dur;
}
const TEST = +(process.env.TEST_FRAMES || 0);
const LIST = (process.env.FRAME_LIST || '').split(',').filter(s => s.trim() !== '').map(s => +s.trim());
const TOTAL = TEST || Math.round(FPS * SECONDS);
const INDICES = LIST.length ? LIST : Array.from({ length: TOTAL }, (_, i) => i);
const PREVIEW = LIST.length > 0;
const FRAMES = path.join(ROOT, `frames_${W}x${H}`);
const OUT = path.join(ROOT, `terrain_flythrough_${W}x${H}.mp4`);
// 用户配置目录放在系统临时区，避免每次清理触发批量删除保护
const PROFILE = path.join(os.tmpdir(), 'edge_video_' + process.pid);
const LOG = path.join(ROOT, 'render.log');

writeFileSync(LOG, '');
function log(s) {
  const line = `[${new Date().toISOString().slice(11, 19)}] ${s}\n`;
  appendFileSync(LOG, line);
  console.log(s);
}

if (!existsSync(EDGE)) throw new Error('Edge not found: ' + EDGE);
// 轨迹外观：优先环境变量，其次同目录 track_style.json（页面「复制剪辑参数」的内容）
let STYLE = process.env.TRACK_STYLE || '';
if (!STYLE) {
  const sf = path.join(ROOT, 'track_style.json');
  if (existsSync(sf)) {
    try {
      const raw = JSON.parse(readFileSync(sf, 'utf8'));
      STYLE = typeof raw === 'string' ? raw : new URLSearchParams(raw).toString();
    } catch (e) { log('track_style.json ignored: ' + e.message); }
  }
}
// 不做批量删除：同一分辨率的帧目录按帧号覆盖写入，ffmpeg 用 -frames:v 严格截取
mkdirSync(FRAMES, { recursive: true });

/* 渲染后端：默认走 D3D11 硬件光栅（约 0.5 s/帧）；
   RENDER_GPU=0 时退回 SwiftShader 软件渲染（约 2 s/帧，但完全不依赖显卡驱动）。
   软件渲染长跑（>100 帧）会因显存/内存膨胀把标签页拖死，硬件后端没有这个问题。 */
const GPU_BACKEND = process.env.RENDER_GPU !== '0';
const edge = spawn(EDGE, [
  '--headless=new',
  ...(GPU_BACKEND
    ? ['--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=d3d11']
    : ['--disable-gpu', '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader']),
  '--no-first-run', '--no-default-browser-check', '--no-proxy-server',
  '--disable-background-timer-throttling', '--hide-scrollbars',
  `--user-data-dir=${PROFILE}`, `--remote-debugging-port=${PORT}`,
  `--window-size=${W},${H}`, 'about:blank'
], { stdio: 'ignore' });
log(`spawn Edge pid=${edge.pid} port=${PORT} backend=${GPU_BACKEND ? 'd3d11' : 'swiftshader'}`);
const killEdge = () => {
  spawnSync('taskkill', ['/PID', String(edge.pid), '/T', '/F'], { stdio: 'ignore' });
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
/* 看门狗只负责兜底“卡死”，不能当作总时长上限：长片（如 1800 帧）按帧数放宽，
   每帧预算 20 s，最少 25 min；也可用 RENDER_WATCH_MIN 显式覆盖。 */
const WATCH_MIN = +(process.env.RENDER_WATCH_MIN ||
  Math.max(25, Math.ceil(TOTAL * 20 / 60)));
const watchdog = setTimeout(() => {
  log('WATCHDOG timeout after ' + WATCH_MIN + ' min');
  killEdge(); process.exit(2);
}, WATCH_MIN * 60 * 1000);
watchdog.unref();

// 用 node:http 直连，避开 fetch/系统代理导致的挂起
function cdpJson(p) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'GET', timeout: 3000 }, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    req.on('timeout', () => req.destroy(new Error('cdp http timeout')));
    req.on('error', reject);
    req.end();
  });
}

let ws, pending = new Map(), seq = 0;
function send(method, params = {}, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => {
      pending.delete(id); reject(new Error('CDP timeout: ' + method));
    }, timeoutMs);
    pending.set(id, {
      resolve: v => { clearTimeout(timer); resolve(v); },
      reject: e => { clearTimeout(timer); reject(e); }
    });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
// 等一帧合成帧；页面里带 250ms 兜底，绝不会因为 rAF 停摆而挂住
const NEXT_FRAME = 'new Promise(r=>{let d=false;requestAnimationFrame(()=>{if(!d){d=true;r(1)}});'
  + 'setTimeout(()=>{if(!d){d=true;r(0)}},250)})';

async function main() {
  let ver = null;
  for (let i = 0; i < 80; i++) {
    try { ver = await cdpJson('/json/version'); if (ver && ver.Browser) break; } catch { /* not ready */ }
    await sleep(250);
  }
  if (!ver || !ver.Browser) throw new Error('Edge CDP port not reachable');
  log('Edge ' + ver.Browser);

  const tabs = await cdpJson('/json/list');
  const page = tabs.find(t => t.type === 'page');
  if (!page) throw new Error('no page target');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  };
  log('CDP connected');

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const SEP = URL_BASE.includes('?') ? '&' : '?';
  const NAV = `${URL_BASE}${SEP}fps=${FPS}&w=${W}&h=${H}${STYLE ? '&' + STYLE : ''}`;
  await send('Page.navigate', { url: NAV });
  log('page navigating ' + NAV);

  let ready = false;
  for (let i = 0; i < 120; i++) {
    const r = await send('Runtime.evaluate',
      { expression: '!!(window.__ready && window.__setFrame)', returnByValue: true });
    if (r.result && r.result.value === true) { ready = true; break; }
    await sleep(250);
  }
  if (!ready) throw new Error('page not ready (texture or module failed)');
  const info = await send('Runtime.evaluate', { expression: 'window.__videoInfo', returnByValue: true });
  const sty = await send('Runtime.evaluate', { expression: 'window.__trackStyle', returnByValue: true });
  log(`ready: ${JSON.stringify(info.result.value)} | ${W}x${H} ${FPS}fps ${INDICES.length} frames`);
  log('track style: ' + JSON.stringify(sty.result.value));

  const t0 = Date.now();
  let small = 0, skipped = 0, retries = 0;
  for (let k = 0; k < INDICES.length; k++) {
    const n = INDICES[k];
    const fp = path.join(FRAMES, String(n).padStart(4, '0') + '.png');
    // 断点续渲：已存在且非空白的帧直接跳过（大分辨率渲染中断后可续跑）
    if (existsSync(fp) && statSync(fp).size > 20000) { skipped++; continue; }
    await send('Runtime.evaluate', { expression: `__setFrame(${n})`, returnByValue: true });
    await send('Runtime.evaluate', { expression: NEXT_FRAME, awaitPromise: true });
    let shot = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        shot = await send('Page.captureScreenshot',
          { format: 'png', fromSurface: true, captureBeyondViewport: false }, 60000);
        break;
      } catch (e) {
        retries++;
        log(`  retry frame ${n} attempt ${attempt + 1}: ${e.message}`);
        await sleep(1200);
        await send('Runtime.evaluate', { expression: `__setFrame(${n})`, returnByValue: true });
      }
    }
    if (!shot) throw new Error(`captureScreenshot failed at frame ${n} after 3 attempts`);
    const buf = Buffer.from(shot.data, 'base64');
    if (buf.length < 20000 && ++small >= 3)
      throw new Error(`blank screenshot at frame ${n} (${buf.length} B)`);
    if (buf.length >= 20000) small = 0;
    writeFileSync(fp, buf);
    if (k % 24 === 0 || k === INDICES.length - 1)
      log(`  frame ${n} (${k + 1}/${INDICES.length}) ${((Date.now() - t0) / 1000).toFixed(1)}s ${(buf.length / 1024) | 0}KB`);
  }
  log(`frames done in ${((Date.now() - t0) / 1000).toFixed(1)}s (skipped ${skipped}, retries ${retries})`);

  try { ws.close(); } catch { /* ignore */ }
  killEdge();
  if (TEST || PREVIEW) { log('preview/test: skip ffmpeg, exit'); process.exit(0); }

  const ff = spawnSync('ffmpeg', [
    '-y', '-framerate', String(FPS), '-i', path.join(FRAMES, '%04d.png'),
    '-frames:v', String(INDICES.length),
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', OUT
  ], { encoding: 'utf8' });
  if (ff.status !== 0) throw new Error('ffmpeg failed:\n' + ff.stderr.slice(-1500));
  log('MP4: ' + OUT);
  process.exit(0);
}

main().catch(e => {
  log('FAILED: ' + e.message);
  try { ws && ws.close(); } catch { /* ignore */ }
  killEdge();
  process.exit(1);
});
