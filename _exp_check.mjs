/* 网页直出视频自检：在真实 Edge 里跑一次短导出，把 MP4 落盘后交给 ffprobe 验规格。
 *
 *   node _exp_check.mjs                 640x360 / 48 帧 / 24fps（约 2 s 素材）
 *   node _exp_check.mjs 1280 720 60     自定义尺寸与帧数
 *   W=1920 H=1080 F=120 node ...        环境变量优先
 *
 * 验的是：EXP 面板存在、WebCodecs 可用、编码器选到 H.264、mp4 头合法、
 * ffprobe 能读出 h264 / 正确尺寸 / 帧数（说明 muxer 封装没写坏）。
 */
import { spawn, spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, statSync, rmSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));   /* 脚本所在目录，换区域不用改 */
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 11200 + (process.pid % 80);
const PROFILE = path.join(os.tmpdir(), 'edge_exp_' + process.pid);
const W = +(process.env.W || process.argv[2] || 640);
const H = +(process.env.H || process.argv[3] || 360);
const FRAMES = +(process.env.F || process.argv[4] || 48);
const FPSN = +(process.env.FPS || process.argv[5] || 24);
const DL = path.join(ROOT, '_expdl_' + process.pid);   /* 每次新目录，避开 Chrome 的连续自动下载拦截 */
let STYLE = '';
try {
  const raw = JSON.parse(readFileSync(path.join(ROOT, 'track_style.json'), 'utf8'));
  STYLE = typeof raw === 'string' ? raw : new URLSearchParams(raw).toString();
} catch (e) { }
const URL0 = process.env.URL0 || `http://127.0.0.1:${process.env.SRV || '8771'}/${process.env.PAGE || 'index.html'}?${STYLE}${process.env.Q || ''}`;
const HEADFUL = !!process.env.HEADFUL;   /* HEADFUL=1 用真实窗口跑，最接近用户环境 */
const edge = spawn(EDGE, [...(HEADFUL ? [] : ['--headless=new']), '--ignore-gpu-blocklist', '--use-angle=d3d11',
  '--no-first-run', '--no-proxy-server', '--hide-scrollbars', `--user-data-dir=${PROFILE}`,
  `--remote-debugging-port=${PORT}`, `--window-size=${W + 40},${H + 40}`, 'about:blank'],
  { stdio: 'ignore' });
const kill = () => { try { if (edge.exitCode === null) edge.kill('SIGKILL'); } catch { } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const cdpJson = p => new Promise((res, rej) => {
  const q = http.request({ host: '127.0.0.1', port: PORT, path: p, timeout: 3000 }, r => {
    let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
  }); q.on('timeout', () => q.destroy()); q.on('error', rej); q.end();
});
let ws, pend = new Map(), seq = 0;
function send(m, p = {}, t = 30000) {
  return new Promise((res, rej) => {
    const id = ++seq, tm = setTimeout(() => { pend.delete(id); rej(new Error('timeout ' + m)); }, t);
    pend.set(id, { res: v => { clearTimeout(tm); res(v); }, rej: e => { clearTimeout(tm); rej(e); } });
    ws.send(JSON.stringify({ id, method: m, params: p }));
  });
}
const logs = [];
const ev = async (e, t = 30000, awaitPromise = false) => {
  const r = await send('Runtime.evaluate',
    { expression: e, returnByValue: true, awaitPromise }, t);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600));
  return r.result.value;
};
(async () => {
  for (let i = 0; i < 80; i++) { try { if (await cdpJson('/json/version')) break; } catch { } await sleep(250); }
  const t = (await cdpJson('/json/list')).find(x => x.type === 'page');
  ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  ws.onmessage = m => {
    const d = JSON.parse(m.data);
    if (d.id && pend.has(d.id)) {
      const p = pend.get(d.id); pend.delete(d.id);
      d.error ? p.rej(new Error(JSON.stringify(d.error))) : p.res(d.result);
    } else if (d.method === 'Runtime.exceptionThrown') {
      logs.push('EXC ' + JSON.stringify(d.params.exceptionDetails).slice(0, 300));
    } else if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'log') {
      const line = d.params.args.map(a => a.value || a.description || '').join(' ');
      if (/^\[exp\]/.test(line)) { console.log('  ' + line); if (logs.length < 8) logs.push(line); }
    } else if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') {
      logs.push('ERR ' + d.params.args.map(a => a.value || a.description || '').join(' ').slice(0, 300));
    }
  };
  await send('Page.enable'); await send('Runtime.enable');
  try { mkdirSync(DL, { recursive: true }); } catch (e) { }
  const before = new Set(readdirSync(DL));
  await send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: DL, eventsEnabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: W + 40, height: H + 40, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: URL0 });
  for (let i = 0; i < 100; i++) { if (await ev('typeof __exportProbe==="function"')) break; await sleep(300); }
  console.log('UI  : expVideo=' + await ev('!!document.getElementById("expVideo")')
    + ' hint=' + JSON.stringify(await ev('(document.getElementById("expHint")||{}).textContent')));
  console.log('PROBE ' + JSON.stringify(await ev('__exportProbe()')));
  if (process.env.ASP) {
    await ev(`document.querySelector('.camAsp button[data-a="${process.env.ASP}"]').click()`);
    await sleep(300);
    console.log('切画幅 ' + process.env.ASP + ' → ' + JSON.stringify(await ev('__exportProbe()')));
  }
  if (process.env.TPL) {
    await ev(`document.getElementById('${process.env.TPL}').click()`);
    await sleep(600);
    console.log('模板 ' + process.env.TPL + ' → ' + JSON.stringify(await ev('window.__shotsProbe()')));
  }
  if (process.env.HINT) {
    await ev(`(()=>{const e=document.getElementById('camDur');e.value='6';e.dispatchEvent(new Event('change'));return 1})()`);
    await sleep(400);
    console.log('总时长改 6 s → ' + await ev(`document.getElementById('expHint').textContent`));
    await ev(`document.querySelector('.camAsp button[data-a="916"]').click()`);
    await sleep(400);
    console.log('切 9:16 → ' + await ev(`document.getElementById('expHint').textContent`));
    await ev(`(()=>{const e=document.getElementById('camDur');e.value='10';e.dispatchEvent(new Event('change'));
      document.querySelector('.camAsp button[data-a="169"]').click();return 1})()`);
    await sleep(300);
  }
  const t0 = Date.now();
  /* 不直接 await：同时轮询遮罩上的进度文字，卡住时能看出停在第几帧 */
  let settled = null, failed = null;
  const pRun = ev(`__exportRun({W:${W},H:${H},fps:${FPSN},frames:${FRAMES},dump:false,noDownload:false,debug:${process.env.DBG ? 'true' : 'false'}})`, 600000, true);
  pRun.then(v => { settled = v; }, e => { failed = e; });
  const report = [];
  for (let i = 0; i < 60 && !settled && !failed; i++) {
    await sleep(1000);
    let st = '', title = '';
    try {
      st = await ev('(document.getElementById("expStat")||{}).textContent||""', 8000);
      title = await ev('(document.getElementById("expTitle")||{}).textContent||""', 8000);
    } catch (e) { title = 'POLL-SKIP'; }
    if (i % 3 === 0 || /完成|失败|取消/.test(title)) {
      const line = ((Date.now() - t0) / 1000).toFixed(0) + 's ' + title + ' | ' + st;
      report.push(line);
      console.log('  ' + line);
    }
    if (/导出完成|导出失败|已取消/.test(title) && !settled) { await sleep(300); }
  }
  if (failed) throw failed;
  const r = settled;
  if (report.length) console.log('进度:\n  ' + report.join('\n  '));  console.log('RUN  ' + JSON.stringify({ ...r, b64: r && r.b64 ? r.b64.length + 'B(base64)' : null }));
  console.log('外圈耗时 ' + ((Date.now() - t0) / 1000).toFixed(1) + ' s');
  if (logs.length) console.log('页面日志:\n  ' + logs.join('\n  '));
  if (r && r.ok) {
    let file = null, sz = -1;
    for (let i = 0; i < 40; i++) {
      const cand = readdirSync(DL).filter(f => /\.mp4$/i.test(f) && !before.has(f))
        .map(f => path.join(DL, f)).sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
      if (cand.length) {
        const n = statSync(cand[0]).size;
        if (n > 0 && n === sz) { file = cand[0]; break; }
        sz = n;
      }
      await sleep(500);
    }
    if (!file) { console.log('没等到下载文件'); kill(); setTimeout(() => process.exit(1), 250); return; }
    console.log('下载文件 ' + path.basename(file) + ' ' + (statSync(file).size / 1024).toFixed(0) + ' KB');
    const head = readFileSync(file).subarray(0, 16).toString('latin1');
    console.log('文件头 ' + JSON.stringify(head.slice(4, 12)) + '（应为 ftyp / isom）');
    const OUT = file;
    if (existsSync('C:\\ffmpeg\\bin\\ffprobe.exe') || spawnSync('ffprobe', ['-version']).status === 0) {
      const p = spawnSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=codec_name,width,height,nb_read_frames,r_frame_rate,pix_fmt',
        '-show_entries', 'format=duration,size,format_name', '-of', 'json', OUT], { encoding: 'utf8' });
      console.log('FFPROBE ' + (p.stdout || p.stderr || '').replace(/\s+/g, ' ').slice(0, 700));
    }
  }
  kill(); await sleep(250); process.exit(0);
})().catch(e => { console.log('ERROR ' + e.message); kill(); setTimeout(() => process.exit(1), 250); });
