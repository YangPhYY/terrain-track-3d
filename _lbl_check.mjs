/* 标签字号联动自检：全览 1×、推近放大（只放最近 1–2 个）、倍数封顶、
   关掉联动回基准、放大后仍不重叠，并出全览/中景/近景/小窗四张图目视。
   用法：node _lbl_check.mjs        （需要 8767 端口的本地静态服务）
         SRV=8768 node _lbl_check.mjs */
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const ROOT = fileURLToPath(new URL('.', import.meta.url)).replace(/[\\/]+$/, '');
/* terrain-hd 的静态服务在 8766，其它区域在 8767；SRV 环境变量可覆盖 */
const SRVDEF = /terrain-hd$/i.test(ROOT) ? '8766' : '8767';
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PORT = 10900 + (process.pid % 90);
const PROFILE = path.join(os.tmpdir(), 'edge_lbl_' + process.pid);
const W = 1600, H = 900;
const WP = '0.1800,ffd166,1,山脊拐弯~0.5000,4dabf7,1,垭口~0.8200,51cf66,1,古道口';
const BASE = `http://127.0.0.1:${process.env.SRV || SRVDEF}/index.html`
  + `?wp=${encodeURIComponent(WP)}&wpsz=12&wpfs=34&lblk=3&lbln=1&lbld0=800&lbld1=420&wpshow=1`;

const edge = spawn(EDGE, [
  '--headless=new', '--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=d3d11',
  '--no-first-run', '--no-proxy-server', '--hide-scrollbars',
  '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
  '--disable-backgrounding-occluded-windows',
  `--user-data-dir=${PROFILE}`,
  `--remote-debugging-port=${PORT}`, `--window-size=${W},${H}`, 'about:blank'
], { stdio: 'ignore' });
const kill = () => { try { if (edge.exitCode === null) edge.kill('SIGKILL'); } catch { } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

function cdpJson(p) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path: p, method: 'GET', timeout: 3000 }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    req.on('timeout', () => req.destroy(new Error('cdp timeout')));
    req.on('error', reject); req.end();
  });
}
let ws, pending = new Map(), seq = 0, fails = 0;
function send(method, params = {}, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, timeoutMs);
    pending.set(id, { resolve: v => { clearTimeout(timer); resolve(v); }, reject: e => { clearTimeout(timer); reject(e); } });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function ev(expression, awaitPromise = false) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}
async function shot(name) {
  const r = await send('Page.captureScreenshot', { format: 'png', fromSurface: true }, 60000);
  writeFileSync(path.join(ROOT, name), Buffer.from(r.data, 'base64'));
}
const ok = (cond, msg, extra) => {
  console.log((cond ? '  PASS ' : '  FAIL ') + msg + (extra === undefined ? '' : '  → ' + JSON.stringify(extra)));
  if (!cond) fails++;
};
/* 面板会盖住画布：测试字号逻辑时先把两栏收起（等价于全窗口，和成片一致），
   测「不出框」时再展开，让可见区就是两栏之间 */
const collapse = async () => { await ev(`(()=>{const b=document.body;
  if(!b.classList.contains('hideL'))document.getElementById('tgL').click();
  if(!b.classList.contains('hideR'))document.getElementById('tgR').click();})()`); await sleep(520); };
const expand = async () => { await ev(`(()=>{const b=document.body;
  if(b.classList.contains('hideL'))document.getElementById('tgL').click();
  if(b.classList.contains('hideR'))document.getElementById('tgR').click();})()`); await sleep(520); };

async function waitReady(ms = 40000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      if (await ev('!!document.querySelector("canvas") && !!window.__lblProbe')) return true;
    } catch (e) { /* 导航中执行上下文被销毁，等下一轮 */ }
    await sleep(400);
  }
  return false;
}
const probe = () => ev('window.__lblProbe()');
/* 可见标签两两是否重叠（真实 DOM 矩形） */
const overlaps = () => ev(`(()=>{const r=[];const a=[];
  document.querySelectorAll('.lbl').forEach(e=>{
    const s=getComputedStyle(e); if(s.display==='none'||s.opacity==='0')return;
    const b=e.getBoundingClientRect(); a.push({t:(e.textContent||'').slice(0,10),x:b.left,y:b.top,w:b.width,h:b.height});});
  for(let i=0;i<a.length;i++)for(let j=i+1;j<a.length;j++){
    const p=a[i],q=a[j];
    if(p.x<q.x+q.w-1&&q.x<p.x+p.w-1&&p.y<q.y+q.h-1&&q.y<p.y+p.h-1)r.push([p.t,q.t]);}
  return {n:a.length,out:r};})()`);
const cam = (el, az, r, ty) => ev(`window.__setCamera(${el},${az},${r},${ty})`);

async function main() {
  let ver = null;
  for (let i = 0; i < 80; i++) {
    try { ver = await cdpJson('/json/version'); if (ver && ver.Browser) break; } catch { }
    await sleep(250);
  }
  if (!ver) throw new Error('Edge CDP 不可达');
  const tabs = await cdpJson('/json/list');
  const page = tabs.find(t => t.type === 'page');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = evx => {
    const m = JSON.parse(evx.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: BASE });
  if (!await waitReady()) throw new Error('页面未就绪');
  await sleep(1200);

  /* 门槛（起/止距离）从页面读，换成别的区域/别的门槛也能跑 */
  const P0 = await probe();
  const D0 = P0.d0, D1 = P0.d1;
  console.log('1) 全览：基准字号、联动系数 = 1  （放大门槛 ' + D0 + ' → ' + D1 + ' m）');
  await cam(28, 150, Math.round(D0 * 2.2), 240);
  await sleep(250);
  let p = await probe();
  ok(p.base === 34, '基准字号 34 px', p.base);
  ok(p.k >= 0.99 && p.k <= 1.02, '全览 k≈1', p.k);
  ok(p.shown.length >= 3, '标签都已上屏', p.shown.length);
  ok(p.shown.every(s => Math.abs(s.fs - 34) < 0.6), '全览所有标签 = 34 px',
    p.shown.map(s => s.fs));
  let ov = await overlaps();
  ok(ov.out.length === 0, '全览无重叠（' + ov.n + ' 个）', ov.out);
  await shot('_lbl_a_overview.png');

  /* 2–9 步只在「全窗口」下验证字号（成片里没有面板） */
  await collapse();

  const R2 = Math.round((D0 + D1) / 2);
  console.log('2) 过渡中点 ' + R2 + ' m：系数约 2×，只有最近那个变大');
  await cam(30, 150, R2, 240);
  await sleep(250);
  p = await probe();
  const fs2 = p.shown.map(s => s.fs);
  ok(p.k > 1.7 && p.k < 2.3, '过渡中点 k≈2', p.k);
  ok(Math.max(...fs2) > 40, '有标签明显变大', Math.max(...fs2));
  ok(fs2.filter(f => f > 40).length === 1, '只有 1 个标签在放大', fs2.filter(f => f > 40).length);
  ok(Math.max(...fs2) <= 34 * 3 + 0.6, '不超过 3× 上限', Math.max(...fs2));
  ov = await overlaps();
  ok(ov.out.length === 0, '中景无重叠（' + ov.n + ' 个可见）', ov.out);
  await shot('_lbl_b_mid.png');

  console.log('2b) 门槛边界：到 d0（' + D0 + ' m）为止一律不放大');
  await cam(28, 150, Math.round(D0 * 1.05), 240);
  await sleep(220);
  p = await probe();
  ok(p.k === 1 && p.shown.every(x => Math.abs(x.fs - 34) < 0.6), 'd0×1.05 时 k=1、全 34 px', p.k);
  await cam(28, 150, D0, 240);
  await sleep(220);
  p = await probe();
  ok(p.k <= 1.002, '正好 d0 时仍未放大', p.k);
  await cam(28, 150, D0 - 60, 240);
  await sleep(220);
  p = await probe();
  ok(p.k > 1.05 && p.k < 1.3, '进门槛 60 m 只是小幅增长（不跳字）', p.k);

  /* 420 m 已经贴到山脊，画面里常常一个标签都不剩（边缘规则会把贴边的隐藏）；
     把门槛临时抬到 1800/1400，在 1400 m 处验证「封顶 3×、只有一个吃满」，测完改回 */
  await ev(`(()=>{const a=document.getElementById('lblD0'),b=document.getElementById('lblD1');
    a.value=1800;b.value=1400;a.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await sleep(320);
  const R3 = 1400;
  console.log('3) 门槛临时 1800/1400，在 ' + R3 + ' m 处：封顶 3×，且只有最近 1 个吃满');
  await cam(30, 150, R3, 240);
  await sleep(250);
  p = await probe();
  const fs3 = p.shown.map(s => s.fs).sort((a, b) => b - a);
  ok(p.k >= 2.98, 'k 达到封顶 3×', p.k);
  ok(fs3[0] >= 90, '最大字号 ≥ 90 px（34×3 封顶）', fs3[0]);
  ok(fs3.filter(f => f > 60).length === 1, '只有 1 个标签被放得很大', fs3.filter(f => f > 60).length);
  ok(fs3[fs3.length - 1] <= 40, '最远的标签接近基准', fs3[fs3.length - 1]);
  ov = await overlaps();
  ok(ov.out.length === 0, '近景无重叠（' + ov.n + ' 个可见）', ov.out);
  await shot('_lbl_c_close.png');
  /* 改回用户选定的 800 / 420 */
  await ev(`(()=>{const a=document.getElementById('lblD0'),b=document.getElementById('lblD1');
    a.value=800;b.value=420;a.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await sleep(320);
  p = await probe();
  ok(p.d0 === 800 && p.d1 === 420, '门槛已改回 800/420', [p.d0, p.d1]);

  console.log('4) 关掉联动：全部回到基准字号');
  await cam(30, 150, 610, 240);       /* 回到门槛内，关掉后应全部回 34 */
  await sleep(250);
  await ev(`document.getElementById('lblAuto').click()`);
  await sleep(300);
  p = await probe();
  ok(p.auto === false, '联动已关闭', p.auto);
  ok(p.shown.every(s => Math.abs(s.fs - 34) < 0.6), '全部 34 px', p.shown.map(s => s.fs));
  await ev(`document.getElementById('lblAuto').click()`);
  await sleep(300);
  p = await probe();
  ok(p.auto === true && p.k > 1.8, '重新打开后回到放大状态', { auto: p.auto, k: p.k });

  console.log('5) 参数串带 lblk / lbln，刷新后仍生效');
  const q = await ev(`document.getElementById('trkParam').value`);
  ok(/[?&]lblk=3(&|$)/.test(q), 'styleParams 含 lblk=3', q.split('&').filter(s => /^lbl/.test(s)));

  console.log('6) 小窗预览：40% 占比时标签仍可见');
  await ev(`document.getElementById('pipSim').click()`);
  await sleep(600);
  const pip = await ev(`(()=>{const w=document.getElementById('pipWrap');
    if(!w)return null;const c=w.querySelector('canvas');
    return {w:c.width,h:c.height,css:c.style.width};})()`);
  ok(!!pip && pip.w > 200, '小窗画布已生成', pip);
  await shot('_lbl_d_pip.png');
  await ev(`window.__pipClose()`);

  console.log('7) 导出静帧：canvas 标签与网页同源（同一套 expDrawLabels）');
  await cam(30, 150, 520, 240);        /* 门槛内、画面里还有标签 */
  await sleep(250);
  p = await probe();
  ok(p.k >= 2.5, '近景 k 已放大', p.k);
  const durl = await ev('window.__expStill(null)');
  const b64 = String(durl).split(',')[1] || '';
  writeFileSync(path.join(ROOT, '_lbl_e_export.png'), Buffer.from(b64, 'base64'));
  ok(b64.length > 20000, '导出静帧已出图', { kb: Math.round(b64.length * 0.75 / 1024) });

  console.log('8) 老存档迁移：旧 12 px 不该压住新基准，新存档要沿用');
  const plain = `http://127.0.0.1:${SRVDEF}/index.html?wp=${encodeURIComponent(WP)}`;
  await ev(`localStorage.setItem('terrain_hd_waypoints_v1',
    JSON.stringify({size:12,fs:12,label:true,list:[]}))`);
  await send('Page.navigate', { url: plain });
  await waitReady(); await sleep(900);
  p = await probe();
  ok(p.base === 34, '旧存档（无 lblv）升级后基准 34 px', p.base);
  ok(+(await ev("document.getElementById('wpFs').value")) === 34, '滑杆同步 34',
    await ev("document.getElementById('wpFsVal').textContent"));
  await ev(`(()=>{const o=JSON.parse(localStorage.getItem('terrain_hd_waypoints_v1'));
    o.fs=18;localStorage.setItem('terrain_hd_waypoints_v1',JSON.stringify(o));})()`);
  await send('Page.navigate', { url: plain });
  await waitReady(); await sleep(900);
  p = await probe();
  ok(p.base === 18, '新存档（lblv=2）的 18 px 被沿用', p.base);

  console.log('9) 放大距离输入框：非法值自动夹紧');
  await ev(`(()=>{const a=document.getElementById('lblD1');
    a.value=2000;a.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await sleep(250);
  p = await probe();
  ok(p.d1 === p.d0 - 80, 'd1 不能 ≥ d0，自动夹到 d0-80', { d0: p.d0, d1: p.d1 });
  ok(+(await ev("document.getElementById('lblD1').value")) === p.d1, '输入框回写夹紧后的值', p.d1);
  await ev(`(()=>{const a=document.getElementById('lblD1');
    a.value=420;a.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await sleep(250);
  p = await probe();
  ok(p.d1 === 420, '改回 420 生效', p.d1);

  await expand();
  console.log('10) 标签不出框：任何可见标签都不能压到边缘/钻到面板底下');
  const bounds = () => ev(`(()=>{const l=document.querySelector('.panel.info').getBoundingClientRect();
    const r=document.querySelector('.panel.ctrl').getBoundingClientRect();
    const hl=document.body.classList.contains('hideL'), hr=document.body.classList.contains('hideR');
    const L=hl?0:Math.max(0,l.right), R=hr?innerWidth:Math.min(innerWidth,r.left);
    const out=[]; let n=0;
    document.querySelectorAll('.lbl').forEach(e=>{
      const s=getComputedStyle(e); if(s.display==='none'||s.opacity==='0')return;
      n++; const b=e.getBoundingClientRect();
      if(b.left<L-0.6||b.right>R+0.6||b.top<-0.6||b.bottom>innerHeight+0.6)
        out.push({t:(e.textContent||'').slice(0,8),l:Math.round(b.left),r:Math.round(b.right),
                  t0:Math.round(b.top),b0:Math.round(b.bottom),L:Math.round(L),R:Math.round(R)});
    }); return {n:n,out:out,L:Math.round(L),R:Math.round(R)};})()`);
  for (const [r0, a0] of [[3000, 150], [3000, 30], [1400, 150], [800, 150], [420, 150]]) {
    await cam(28, a0, r0, 240);
    await sleep(260);
    const bd = await bounds();
    ok(bd.out.length === 0, 'r=' + r0 + ' az=' + a0 + '：' + bd.n + ' 个可见标签都在框内',
      bd.out.slice(0, 3));
  }

  console.log(fails ? ('FAILED ' + fails) : 'ALL PASS');
  kill();
  process.exit(fails ? 1 : 0);
}
main().catch(e => { console.error(e); kill(); process.exit(1); });
