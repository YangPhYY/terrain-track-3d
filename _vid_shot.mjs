/* 直接从 video.html（逐帧页）取一帧截图，用来核对「成片构图 = 页面预览」。
   用法：VW=1080 VH=1920 T=9.98 node _vid_shot.mjs  → _vid_frame.png
        或者 FRAME=599 指定帧号（默认按 T×fps 算） */
import { spawn } from 'node:child_process';
import { writeFileSync, readFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 12100 + (process.pid % 60);
const PROFILE = path.join(os.tmpdir(), 'edge_vid_' + process.pid);
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SRV = process.env.SRV || '8771';
const VW = +(process.env.VW || 1920), VH = +(process.env.VH || 1080);
const FPS = +(process.env.FPS || 60);
let STYLE = '';
try {
  const raw = JSON.parse(readFileSync(path.join(ROOT, 'track_style.json'), 'utf8'));
  STYLE = typeof raw === 'string' ? raw : new URLSearchParams(raw).toString();
} catch (e) { }

const edge = spawn(EDGE, ['--headless=new', '--ignore-gpu-blocklist', '--use-angle=d3d11',
  '--no-first-run', '--no-proxy-server', `--user-data-dir=${PROFILE}`,
  `--remote-debugging-port=${PORT}`, `--window-size=${VW},${VH}`, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const cdpJson = p => new Promise((res, rej) => {
  const q = http.request({ host: '127.0.0.1', port: PORT, path: p, timeout: 3000 }, r => {
    let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
  });
  q.on('timeout', () => q.destroy()); q.on('error', rej); q.end();
});
let ws, pend = new Map(), seq = 0;
const send = (m, p = {}, t = 60000) => new Promise((res, rej) => {
  const id = ++seq, tm = setTimeout(() => { pend.delete(id); rej(new Error('timeout ' + m)); }, t);
  pend.set(id, { res: v => { clearTimeout(tm); res(v); }, rej: e => { clearTimeout(tm); rej(e); } });
  ws.send(JSON.stringify({ id, method: m, params: p }));
});
const ev = async (e, t = 60000) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true }, t);
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
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
    }
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: VW, height: VH, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `http://127.0.0.1:${SRV}/video.html?${STYLE}&fps=${FPS}&w=${VW}&h=${VH}` });
  for (let i = 0; i < 100; i++) {
    const r = await ev('!!window.__ready').catch(() => false);
    if (r) break;
    await sleep(400);
  }
  await sleep(600);
  const info = await ev('window.__videoInfo');
  console.log('probe ' + JSON.stringify(await ev('(()=>{const p=__camProbe();return {asp:p.asp,last:p.shots[p.shots.length-1],cam:p.cam,dur:p.dur,shots:p.shots.length};})()')));
  const n = process.env.FRAME !== undefined ? +process.env.FRAME
    : Math.round((+(process.env.T || info.duration)) * FPS) - 1;
  await ev(`__setFrame(${n})`);
  await sleep(400);
  const shot = await send('Page.captureScreenshot', { format: 'png' }, 60000);
  const out = path.join(ROOT, process.env.OUT || '_vid_frame.png');
  writeFileSync(out, Buffer.from(shot.data, 'base64'));
  console.log(out + ' 帧 ' + n + ' / ' + info.shots + ' 镜头 · ' + info.duration + ' s · ' + VW + '×' + VH);
  try { edge.kill('SIGKILL'); } catch { }
  setTimeout(() => process.exit(0), 250);
})().catch(e => { console.log('ERROR ' + e.message); try { edge.kill('SIGKILL'); } catch { } setTimeout(() => process.exit(1), 250); });
