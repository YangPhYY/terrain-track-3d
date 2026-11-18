/* 量一下当前画面里「沙盘 8 角点」和「标签锚点」的 NDC 范围：
   判断全览取景到底是宽还是高被顶住，以及标签有没有超出 0.9。 */
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 12000 + (process.pid % 60);
const PROFILE = path.join(os.tmpdir(), 'edge_ndc_' + process.pid);
const SRV = process.env.SRV || '8771';
const ROOT = path.dirname(fileURLToPath(import.meta.url));
let STYLE = '';
try {
  const raw = JSON.parse(readFileSync(path.join(ROOT, 'track_style.json'), 'utf8'));
  STYLE = typeof raw === 'string' ? raw : new URLSearchParams(raw).toString();
} catch (e) { }

const edge = spawn(EDGE, ['--headless=new', '--ignore-gpu-blocklist', '--use-angle=d3d11',
  '--no-first-run', '--no-proxy-server', `--user-data-dir=${PROFILE}`,
  `--remote-debugging-port=${PORT}`, '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const cdpJson = p => new Promise((res, rej) => {
  const q = http.request({ host: '127.0.0.1', port: PORT, path: p, timeout: 3000 }, r => {
    let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
  });
  q.on('timeout', () => q.destroy()); q.on('error', rej); q.end();
});
let ws, pend = new Map(), seq = 0;
const send = (m, p = {}, t = 40000) => new Promise((res, rej) => {
  const id = ++seq, tm = setTimeout(() => { pend.delete(id); rej(new Error('timeout ' + m)); }, t);
  pend.set(id, { res: v => { clearTimeout(tm); res(v); }, rej: e => { clearTimeout(tm); rej(e); } });
  ws.send(JSON.stringify({ id, method: m, params: p }));
});
const ev = async (e, t = 40000) => {
  const r = await send('Runtime.evaluate', { expression: e, returnByValue: true }, t);
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
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `http://127.0.0.1:${SRV}/index.html?${STYLE}&_=${Date.now()}` });
  for (let i = 0; i < 80; i++) {
    if (await ev('!!document.querySelector("canvas") && typeof __camAudit==="function"')) break;
    await sleep(400);
  }
  await sleep(1200);
  if (process.env.ASP) {
    await ev(`document.querySelector('.camAsp button[data-a="${process.env.ASP}"]').click()`);
    await sleep(700);
  }
  const T = +(process.env.T || 9.9);
  await ev(`__previewAt(${T})`);
  await sleep(500);
  const r = await ev('__ndcProbe()');
  r.shot = await ev(`(()=>{const p=__camProbe();return {cam:p.cam,last:p.shots[p.shots.length-1],n:p.shots.length,dur:p.dur};})()`);
  r.size = await ev(`(()=>{const b=new THREE.Box3().setFromObject(group);return [b.min.x,b.max.x,b.min.y,b.max.y,b.min.z,b.max.z].map(v=>Math.round(v));})()`).catch(e=>'n/a');
  console.log(JSON.stringify(r, null, 1));
  try { edge.kill('SIGKILL'); } catch { }
  setTimeout(() => process.exit(0), 250);
})().catch(e => { console.log('ERROR ' + e.message); try { edge.kill('SIGKILL'); } catch { } setTimeout(() => process.exit(1), 250); });
