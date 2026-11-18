/* 镜头模板自检：16:9 与 9:16 两种画幅下逐个点模板，
   用页面里的 __camAudit 逐帧投影，检查是否 NaN / 出画 / 看不到轨迹。
   用法：node _cam_tpl_check.mjs            （默认 http://127.0.0.1:8771/index.html）
        SRV=8772 node _cam_tpl_check.mjs
*/
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 11900 + (process.pid % 60);
const PROFILE = path.join(os.tmpdir(), 'edge_camtpl_' + process.pid);
const SRV = process.env.SRV || '8771';
const ROOT = path.dirname(fileURLToPath(import.meta.url));   /* 脚本所在目录，换区域不用改 */

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
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
};

const TPL = [
  ['camTour', '途径点巡礼', 'tour'],
  ['camFollow', '沿线跟拍', 'follow'],
  ['camSpin', '环绕展示', 'orbit'],
  ['camRide', '高程跟随', 'follow'],
  ['camDive', '俯冲进场', 'dive'],
  ['camPull', '拉升退场', 'pull'],
  ['camTop', '俯视平面', 'top'],
  ['camPoi', '单点环绕', 'poi'],
];

let fails = 0;
const chk = (ok, msg, extra) => {
  console.log((ok ? '  ok   ' : '  FAIL ') + msg + (extra === undefined ? '' : '  ' + JSON.stringify(extra)));
  if (!ok) fails++;
};

async function setAsp(a) {
  await ev(`document.querySelector('.camAsp button[data-a="${a}"]').click()`);
  await sleep(600);
}

async function audit(id) {
  await ev(`document.getElementById('${id}').click()`);
  await sleep(700);
  return await ev('__camAudit(0.1)');
}

(async () => {
  for (let i = 0; i < 90; i++) { try { if (await cdpJson('/json/version')) break; } catch { } await sleep(250); }
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
  await sleep(1500);

  /* 途径点为空时巡礼/单点环绕只会弹提示，先补 4 个点（只在本页生效，不写存档） */
  const nwp = await ev(`(()=>{const a=[0.18,0.42,0.66,0.88];
    return a.map(function(t,i){return __addWaypoint(t,String.fromCharCode(65+i)+'点');})
            .filter(Boolean).length;})()`);
  console.log('途径点 ' + nwp + ' 个\n');

  for (const asp of ['169', '916']) {
    await setAsp(asp);
    const pr = await ev('__camProbe()');
    console.log(`=== 画幅 ${asp === '169' ? '16:9' : '9:16'} · 取景框 ${pr.aspSize.join(' × ')} ===`);
    for (const [id, name, kind] of TPL) {
      const a = await audit(id);
      if (a.error) { chk(false, name + ' ' + a.error); continue; }
      const head = `${name.padEnd(6)} dur=${a.dur}s 帧=${a.shots} r=${a.rMin}~${a.rMax}` +
        ` corner=${a.minCorner} trk=${a.minTrk}~${a.maxTrk}`;
      let ok = true; const why = [];
      if (a.nan) { ok = false; why.push('NaN 帧 ' + a.nan); }
      if (kind === 'orbit' || kind === 'top') {
        if (a.minCorner < 8) { ok = false; why.push('沙盘出画 corner=' + a.minCorner); }
      }
      if (kind === 'follow') {
        if (a.minTrk < 1) { ok = false; why.push('有些帧看不到轨迹'); }
        if (a.maxTrk < 3) { ok = false; why.push('轨迹始终太少 ' + a.maxTrk); }
      }
      if (kind === 'tour' || kind === 'poi') {
        if (a.minTrk < 1) { ok = false; why.push('有些帧看不到轨迹'); }
      }
      if (kind === 'dive') {
        const first = a.rows[0], last = a.rows[a.rows.length - 1];
        if (first.corner < 8) { ok = false; why.push('开场沙盘出画 ' + first.corner); }
        if (last.trk < 1) { ok = false; why.push('落点看不到轨迹'); }
      }
      if (kind === 'pull') {
        const last = a.rows[a.rows.length - 1];
        if (last.corner < 8) { ok = false; why.push('结尾沙盘出画 ' + last.corner); }
        if (a.rows[0].trk < 1) { ok = false; why.push('起手看不到轨迹'); }
      }
      chk(ok, head + (ok ? '' : '  ← ' + why.join('; ')));
      if (!ok) {
        const bad = a.rows.filter(r => r.corner < 8 && (kind === 'orbit' || kind === 'top'))
          .slice(0, 4).map(r => `t=${r.t} r=${r.r} az=${r.az} corner=${r.corner}`);
        if (bad.length) console.log('         ' + bad.join(' | '));
      }
    }
    console.log('');
  }

  /* 画幅切换是否真的改了相机比例与机位 */
  await setAsp('169');
  const p169 = await ev('__camProbe()');
  await setAsp('916');
  const p916 = await ev('__camProbe()');
  const arOf = p => {
    const [w, h] = p.aspSize.map(v => parseFloat(v));
    return +(w / h).toFixed(4);
  };
  console.log('=== 画幅切换 ===');
  chk(Math.abs(arOf(p169) - 16 / 9) < 0.01, '16:9 取景框比例 ' + arOf(p169));
  chk(Math.abs(arOf(p916) - 9 / 16) < 0.01, '9:16 取景框比例 ' + arOf(p916));
  const arAudit = (await ev('__camAudit(0.5)')).ar;
  chk(Math.abs(arAudit - 9 / 16) < 0.01, '自检相机比例跟随画幅 ' + arAudit);
  const r169 = p169.cam.r, r916 = p916.cam.r;
  chk(r916 > r169 * 1.6, `切竖屏后机位自动拉远（r ${Math.round(r169)} → ${Math.round(r916)}）`);

  console.log('\n' + (fails ? `${fails} 项失败` : '全部通过'));
  try { edge.kill('SIGKILL'); } catch { }
  setTimeout(() => process.exit(fails ? 1 : 0), 250);
})().catch(e => {
  console.log('ERROR ' + e.message);
  try { edge.kill('SIGKILL'); } catch { }
  setTimeout(() => process.exit(1), 250);
});
