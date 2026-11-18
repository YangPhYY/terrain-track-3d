# 山地 3D 沙盘 + 轨迹叠加 · 制作规则

> 用途：换一片区域时按本文重做，不用重新推导。全部结论来自 `terrain-hd` 的实战（新密小顶山区域）。
> 配套技能：`~/.workbuddy/skills/terrain-track-3d/SKILL.md`（下次说「做另一个区域的 3D 模型」会自动加载）。

---

## 0. 一句话流程

```
准备 GeoJSON（区域轮廓） + KML（轨迹）
   → bootstrap_bbox.py  只从 KML 算外接框，写占位 track.json（新目录必需）
   → 问用户建模范围（贴轨迹 / 包山 / 按轮廓）→ build.py --dry-run 确认窗口
   → build.py           建地形（DEM + 卫星 + 网格）
   → track.py           贴轨迹 + 注入页面 + 出单文件版
   → video.py（可选）
   → 页面调参 → 面板顶部「⬇ 导出 MP4」网页直出（秒级）
   →（需要极高画质 / 无人值守时才走）复制参数 → render_frames.mjs
```

为什么第一步不能省：`build.py` 要读 `track.json` 的经纬度来定窗口，`track.py` 又要读 `mesh.json` 才能贴地。
新目录里两个文件都不存在，直接跑哪一步都会失败——`bootstrap_bbox.py` 就是解这个死循环的。

**铁律：只改生成源（`.py` / `.mjs`），绝不直接改生成出来的 `index.html` / `track.html` / `video.html`。**
每次改完必须重建 + 跑浏览器自检，否则改了会被下次重建覆盖。

---

## 1. 开工前：需要什么

| 输入 | 必需 | 说明 |
|---|---|---|
| 区域轮廓 GeoJSON | **可选** | 只用来在模型上画边界线（蓝=纠偏后 / 橙=原始），**不参与地形、不决定窗口**。不给也能建 |
| 轨迹 KML | **必需** | 两步路 `gx:Track`（带时间戳，WGS84）或奥维 `LineString`（CGCS2000） |
| 网络 | **必需** | 建模要在线拉 DEM 瓦片和卫星瓦片，离线建不了 |
| 天地图 tk | 可选 | 只有要「可对外发布」的合规底图才用；自用剪辑走 Esri |

**环境**（本机已具备，换机要确认）：
- Python：`~/.workbuddy/binaries/python/versions/3.13.12/python3`（Git Bash 里 `python3` 可直接调）
- 依赖：`PIL`（Pillow）
- Node 22（`render_frames.mjs` 用，内置 WebSocket）
- Edge：`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- 预览服务：`python3 -m http.server 8766 --directory D:\agent\svg3d\terrain-hd`

---

## 2. 建模窗口：按「轨迹」定，不按「蓝线」定

这是第一版返工最多的地方。蓝线只是地块，最终画面主体是轨迹，窗口必须以轨迹为准。

```python
# build.py 顶部
BUFFER  = 80.0   # 轨迹外接框每边外扩（m）
GRID_M  = 18.0   # 目标格距（m），NX/NY 自动取整
Z_DEM   = 13     # DEM 瓦片层级（Terrarium ≈ 30 m）
Z_SAT   = 16     # 脚本默认；实际重跑用 --z-sat=18
BASE    = -22.0  # 底座厚度（负数向下）
```

- 窗口 = 轨迹 WGS84 外接框 + 每边 80 m，原点平移到轨迹中心。
- 结论数据（本区域）：轨迹 bbox ≈ 662×588 m → 窗口 **821.9×748.3 m**，网格 **46×42**，格距 ≈ 17.9 m，高程 **362.1–588.7 m**，底座 22 m。
- **外扩为什么是 80 m**：小于 60 m 山体侧壁会切到轨迹投影；大于 120 m 主体在画面里变小、轨迹细节丢失。80 m 是「轨迹占屏 60–70%」的甜点。
- 底座别用旧值 `-60`：插画风需要薄侧壁，-60 会形成大块深色悬崖，白底上很脏。

### 2.1 窗口三种口径（开跑前必须和用户确认）

| 口径 | 参数 | 适用 | 本区域结果 |
|---|---|---|---|
| **贴轨迹裁**（默认） | `--box=auto --buffer=80` | 轨迹就是主角，要最大主体 | 821.9×748.3 m，46×42 |
| **包住整座山** | `--buffer=250`（可 150–400 调） | 轨迹只是路过，山体要完整 | 1161.9×1088.3 m，65×60 |
| **按区域轮廓** | `--box=geo --buffer=80` | 要保住整块地（需 GeoJSON） | 1387.5×1066.3 m，77×59 |
| 手填矩形 | `--box=minlon,minlat,maxlon,maxlat`（WGS84） | 山顶范围/人工微调 | 按输入 |

判断依据：**轨迹是否占满画面**。轨迹横跨整片山 → 用 auto；轨迹只走一条沟/一段山脊 → 用 250 或更大，否则模型会窄成一条带子。

### 2.2 先干跑，再真建

```bash
python3 build.py --dry-run --buffer=250      # 只打印窗口，不拉瓦片、不写文件
```

会输出窗口尺寸、网格、格距，以及 **track in window 百分比**。低于 95% 说明有轨迹点在框外被裁掉了，要放大窗口或改 `--box`。

```bash
python3 build.py --box=geo --dry-run         # 按轮廓
python3 build.py --geo="新区域.geojson" --dry-run   # 换一份边界文件
```

---

## 3. 坐标与朝向：两条独立铁律

### 3.1 坐标纠偏（横向错位）

不同来源的坐标系不一样，**判错就是整条轨迹平移几十米**：

| 来源 | 坐标系 | 处理 |
|---|---|---|
| 两步路 KML `gx:Track` | WGS84 | **原样使用，不要纠偏** |
| 奥维 KML `LineString` | CGCS2000（≈WGS84） | 基本等同，不纠偏。文件里会写 `OvCoordType>CGCS2000` |
| DataV / 高德 / 腾讯系导出的 GeoJSON | GCJ02（火星坐标） | **必须 `gcj2wgs()`** |

本工程已跑过两种来源，可作对照：

| 轨迹 | 来源 | 点数 | 处理 | corr / RMSE |
|---|---|---|---|---|
| `山顶人家小顶山环线.kml` | 奥维 LineString，CGCS2000 | 295 | 不纠偏 | 0.9344 / 21.87 m |
| `山顶人家小顶山徐霞客古道环线.kml` | 两步路 `gx:Track`，WGS84 | 285 | 不纠偏 | **0.9806 / 21.59 m** |

地形那份 GeoJSON 走 `gcj2wgs()`、轨迹那份不动，两者才对得上。

判定方法：把 KML 点打在地形上，看 KML 高程与 DEM 高程的相关性 ——
**未纠偏 0.93–0.98；强行纠偏 −0.73 / RMSE 113.56 m**。相关性为负就说明纠错了。
`track.py` 现在每次按当前轨迹实算 corr/RMSE 并写进 `track.json.meta`，换区域先看这个数：
**> 0.9 可信，0.5 以下大概率坐标系错了，负值一定错。**

### 3.2 轴向（整图镜像）

```
x = 东    y = 高程    -z = 北      （右手系）
```

把北映射到 `+z` 会让整个场景变左手系，**俯视时东西镜像**，而且从斜视角看不出来。这是最隐蔽的坑。
所有坐标转换统一走同一个 `to_m()`，不要在别处手写一遍。

---

## 4. 建地形：`build.py`

```bash
cd /d/agent/svg3d/terrain-hd
python3 build.py --sat=esri --z-sat=18        # 正式重建（自用/剪辑，清晰度最高）
python3 build.py --sat=tianditu --z-sat=18    # 合规底图（需 D:\agent\svg3d\tk.txt）
python3 build.py --sat=esri --sat-only        # 只拉贴图自检，不动模型文件
```

**注意**：脚本默认 `Z_SAT=16`，**重跑一定要显式带 `--sat=esri --z-sat=18`**，否则卫星图会退回较糊的层级，而且不会报错。

输出：`mesh.json`（页面数据）、`terrain.stl`、`terrain.glb`（内嵌卫星贴图）、`satellite.jpg`、`index.html`。

### 4.1 网格索引（会造成轨迹悬空/埋地）

顶点按 `i` 外层、`j` 内层追加，索引必须是：

```python
def idx(i, j): return i * (NY + 1) + j
```

写成 `j*(NX+1)+i` 会让轨迹与地表完全错位（本区域实测最大高差 **249 m**）。
`build.py` 里那个同名函数是**未被调用的死代码**，别照抄它。

### 4.2 自检指标

重建后必须看这行输出：

```
elevation=362.1..588.7 m  relief=226.6 m  V=... T=... non_manifold=0
```

`non_manifold` 必须 **0**（水密、无非流形边）。另外 `track.py` 开头有一段强校验，会逐点比对 `x` / `z` / `y` 与网格位置，误差 > 0.1 m 直接 assert 失败——留着，别删。

---

## 5. 贴轨迹：`track.py`

```bash
python3 track.py     # 出 track.json / track_in_model.geojson / track.html / index.html
```

### 5.1 贴地算法（必须按三角面插值）

页面网格是 `(a,b,c)+(a,c,d)` 两个三角形剖分的。用**双线性插值**会在陡坡上产生最高约 **20 m** 偏差，轨迹看着穿进山体或断续。正确做法：

```python
return a + (b-a)*u + (c-b)*v if u >= v else a + (c-d)*u + (d-a)*v
```

`track.py` 和页面 JS 必须用**同一套**公式，否则预览和导出不一致。

### 5.2 高度基准

- `LIFT = 7.0`：贴地后再抬 7 m，只为避免 z-fighting，不是"让轨迹飞起来"。
- **不要直接用 KML/GPS 高程**：GPS 高程与 DEM 有系统偏差，直接用会整条飘。代码里算 `BIAS`（平均差）后只作参考/剖面用，实际 y 走 DEM。
- 页面默认开「自适应离地」，实测平均抬升 2.51 m；配合「贴地假投影」给低角度一个接触线索。**穿透显示（xray）默认必须关**。

### 5.3 框外点处理

「有多少落多少」：框外的点一律丢弃，按 `inside()` + 连续段切分，不相连的段各自成段。起终点标注自动降级为「进入建模区 / 离开建模区」；环线（首尾 < 30 m）只标一次「起 / 终点」。

### 5.4 默认外观（可页面调，这里是出厂值）

```python
TUBE_R = 4.2          # 管半径（m）
TRACK_COLOR = "ff4d1a"  # 主色，6 位 hex 不带 #
HALO_SCALE = 1.8      # 白色包边倍数，0 = 关
```

### 5.5 建管前必须去重（两步路轨迹的高发事故）

两步路 `gx:Track` 是 1 秒一采，站着不动/信号抖动会产生**连续相同坐标**。
`CatmullRomCurve3` 在零长度线段上算切向得到零向量 → `TubeGeometry` 全部顶点 NaN →
**整条轨迹管静默消失，控制台不报错，只剩贴地影带**（表现为「轨迹变成一条深棕色细带」）。

```js
function dedupeSeg(seg){                    // 相邻点 < 0.5 m 视为重复
  const out=[seg[0]];
  for(let i=1;i<seg.length;i++){
    const a=out[out.length-1], b=seg[i];
    if(Math.hypot(b[0]-a[0],b[3]-a[3])>=0.5) out.push(b);
  }
  return out;
}
```

首末距离 < 2.5 m 判为环线 → 去掉末点 + `new THREE.CatmullRomCurve3(pts, true, 'centripetal')`，
`TubeGeometry(..., closed=true)`；否则接缝处会出现反向切向。GLB 导出走同一份逻辑。

**快速验伪**：`trkw=40&trkc=00ff00&trkshadow=0&trkhl=0` 打开——如果这么粗的绿管都看不见，
就是 NaN，不是被地形挡住。

### 5.6 地标模型（如山顶千尺塔）

真实尺度放进大窗口会小到看不见，必须给「尺度」而不是改几何：

- **几何**：程序化六棱台堆叠（`CylinderGeometry(6 段)` 底面贴 y=0，一层层加），不贴照片、不做贴图。
  形制按真实数据：千尺塔七级六角密檐、通高 15 m、底边周长 16 m（六边形外接圆半径 = 边长 2.667 m）；
  九层塔基 → 三级六棱台；每级叠涩 7+5 层 → 三层外挑 + 一层回叠；最后整体 `scale` 归一化到 15 m。
- **落位**：`PEAK_PT = max(mesh["verts"], key=lambda p: p[1])` 注入成 `__PEAK__`（本区域 544.5 m ≈ 官方塔山 544.9 m）；
  再取 8 m 半径内最低 DEM 坐底，陡坡一侧才不悬空。
- **尺度**：真实 15 m 在 1275 m 窗口的 1080 竖屏里只有约 13 px。用滑块 `tg=`（0–8，0=隐藏，默认 3），
  并写进「复制参数」串，渲染脚本直接带 `tg=3` 走同一条 URL。
- **和垂直夸张的关系**：塔挂在 `group` 下会被 `group.scale.y`（默认 1.0）拉长，
  必须 `towerRoot.scale.set(k, k/group.scale.y, k)`，并监听 `ex` 滑块的 `input` 重算。
- **位置微调**：必须有「东移 / 北移」滑块（±200 m，`tdx` / `tdn`，北 = `-z`），改偏移时用 `demAtLocal()` 重采样 8 m 半径最低点，塔才会跟着坡面走；配一个「回到山顶」复位。面板里给一行 `塔位 113.38057°E 34.63050°N` 读数，方便和地图/实拍点核对。
- **GLB**：导出时 `g.add(towerRoot.clone(true))`，与页面同一比例。
- 新状态常量（`const TWR`）**必须声明在 `styleParams()` 之前**，否则页面初始化先调 `styleParams` 会踩 TDZ 白屏（坑 25）。

---

## 6. 页面能力（都已实现，调参即可）

| 模块 | 能力 | 关键参数 |
|---|---|---|
| 底图 | 插画沙盘 / 卫星 双模式 | `art=1|0` |
| 色板 | 秋冬赭褐 / 暖黄土坡 / 灰岩冷调 / 素模石膏 | `pal=autumn|loess|slate|mono` |
| 轨迹 | 粗细、颜色、包边、透明度、离地、自动离地、假投影、穿透 | `trkw/trkc/trkhl/trkhc/trkop/trklift/trkauto/trkshadow/trkx` |
| 途径点 | 页面上点选添加、改大小/字号/标签/加粗/颜色/标签透视 | `wp/wpsz/wpfs/wplbl/wpb/wpocc` |
| 标注点 | KML 航点（「左转沿小路下山」这类）与起终点分开控制 | `mk=0/1`；「起终点标记」走 `ends=0/1` |
| 地标塔 | 山顶示意模型，尺度与位置可调（0=隐藏） | `tg=0..8` `tdx/tdn=±200` |
| 面板 | 左右两侧面板各自收/放：屏边中部的竖条把手，或 `[` 收左、`]` 收右、`H` 一起收放；状态刷新后仍记得 | `localStorage['terrain.panel']`；`video.html` 自动隐藏 |
| 镜头 | 7 滑块精调 + 分镜关键帧 + 播放预览 + 9:16 / 16:9 安全框 | `cam/shots/dur/asp` |
| 导出 | STL / GLB（含轨迹）/ 轨迹 JSON / 复制参数 | 页面按钮 |
| 视频导出 | **网页直出 MP4**：面板顶部一键导出，本机 GPU 编码，不跑 Node/ffmpeg | `fps` 24/30/60、清晰度 720p/1080p/1440p/4K；尺寸跟「画幅」走 |

### 运镜模板（右侧「镜头控制」下方，点一下即写入分镜表）

| 按钮 | 生成逻辑 | 时长 | 生成条数（本区实测） |
|---|---|---|---|
| 途径点巡礼 | 每个途径点「远处进 → 特写停 → 飞下一个」，末段拉远定格全局 | 点数×2.2 + 3.4 s | 7 点 → 23 条 / 18.8 s |
| 沿线跟拍 | 机位在轨迹后上方（0.34×对角线、0.20×高），朝前进方向 | 14 s | 17 条 |
| 环绕展示 | 匀速绕一圈，az 40→400，首尾同角可接 | 10 s | 5 条 |
| 高程跟随 | 贴地（0.23×/0.11×）沿轨迹随坡起伏 | 16 s | 21 条 |
| 俯冲进场 | 高空 → 起点低角度 | 4.2 s | 4 条 |
| 拉升退场 | 终点低角度 → 全局 | 5 s | 4 条 |
| 俯视平面 | el 89°、北朝上（`applyCam` 里 >88.9° 自动换 up=-z） | 2.6 s | 2 条 |
| 单点环绕 | 绕离当前取景中心最近的途径点转一圈 | 5 s | 5 条 |

- 所有模板都靠 `fitR()` 二分求「刚好装下」的距离，**不写死数字**，换区域/换模型尺寸不会跑偏；取景盒用 `DIAG` 比例。
- **取景比例必须按画幅算**：`fitR()` 用 `fitAr()`（16:9→16/9、9:16→9/16、自由→窗口比例），不能直接用 `innerWidth/innerHeight`。
  竖屏横向视野只有横屏的 1/3，沿用横屏距离求 r，模型必然左右出框（这也是「竖版模型超出屏幕」的根因）。
- **环绕类要对整圈求距离**：`orbitR()` 取一圈 4 个方位的最大 `fitR`。只按起始方位算，转到「宽边」就会裁角
  （单点环绕原来按 az=90 窄边算，转到 60° 横向差 37%）。
- **要求完整的镜头不能用 `globalFit()`**：它乘 0.86，把 `fitR(margin 0.9)` 放大到 ndc≈1.05（出画），
  只适合初始视角这种允许裁角的场景；环绕 / 全局定格 / 收尾用 `fitFull()`（margin 0.96，不缩）。
- **模板帧一律标 `sm:0`（线性插值）**：跟拍 16 段、每段 0.9 s，`smoothstep` 会让每段末速度归零，
  变成「走一下停一下」的顿挫；手调「记录当前画面」的帧不带 `sm`，仍走缓入缓出。
- **顶视要连 `fitR` 的 up 一起换**：`applyCam` 在 el>88.9° 时用 `up=(0,0,-1)`，临时相机不跟着换，
  判定的 roll 就和实际画面差 90°，长条形模型会「算着装下了、实际超框」。
- **巡礼 / 单点环绕读 `readCam()`，不要读 `CAM`**：拖动视角不会回写 `CAM`，
  按 `CAM.az / CAM.tx` 选「离当前视角最近的途径点」会用到上一次的旧位置。
- 跟拍切线必须取「前一点→后一点」，只朝前采样在终点会退化成零向量，机位会跑到正上方（el≈90°）。
- **轨迹生长**：勾选框 + 起/用时（参数 `grow=1&grows=0&growd=4`），靠 `setDrawRange` 切 tube / halo / 影带。
  生长时途径点的球/杆/环与标签按各自里程比例 `t` 一起出现（`WPMESH` + `GROWP`），KML 标注点用 `nearestT()` 折算；
  **环线首尾重合**：`growT()` 把 `t≥0.98` 的末位途径点按 `t=0` 处理，起点标签在动画一开始就出来，不用等整圈画完；
  **相机跟得上生长**：`_growcam.py 8 > _grow_shots.txt` 按生长前沿方位角生成分镜（±0.6 s 圆均值平滑 + 40°/s 限速），
  实测生长末端全程落在画面中心 26% 内；固定角速度的环绕会与轨迹反向，前沿经常出画；
  标签是「display 常开 + opacity 淡入」，所以 `__waypointProbe` 与探针必须把 `opacity=0` 的排除，否则会误判成「未显示/重叠」。
  逐帧渲染页必须 `.lbl{transition:none}`：CSS 过渡走墙上时间，而渲帧是动画时间，否则标签会半透明卡住。逐帧渲染时页面 rAF 循环（PLAY 恒 0）会和 `__setFrame` 抢着改 drawRange 把轨迹抹掉，`video.py` 里置 `window.__frameMode=true` 让 `syncGrow()` 让位。

**镜头默认值**：页面 `fov=44`；分镜默认 5 帧 / 10 s / `fov=58`。

**面板收放的两种实现**：本目录（terrain-hd）是「屏边竖条把手 + `[` `]` `H` + 记忆」，是标准做法；
`qianchita-hd` 里还留着早期那版「顶部胶囊按钮 `隐藏面板` + `H`，两侧同时收」，翻新区域时照本目录的模板改，别再新建第三种。
把手代码只写在 `index.template.html`（CSS `.pHandle` / `#tgL` `#tgR` + 一段普通 `<script>`），`track.py`、`video.py` 不用动；
`video.py` 的 `VIDEO_CSS` 已把 `.pHandle` 和 `.panel` 一起 `display:none`。

**途径点标签**：默认**不透视**——`labelBlocked()` 沿「相机→锚点」按 DEM 采样，地表高过视线就判定被山挡住、直接不画；
再叠一层防拥挤：按相机距离排序占位，撞车的标签最多上移一格，还撞就隐藏、让位给更近的那个。
想看全貌勾「标签透视（被山挡也显示）」，参数 `wpocc=1` 会写进「复制参数」串。

### 画幅裁切（9:16 / 16:9 取景框）

切画幅**不是只画一个虚框**：`applyAspect()` 同时把渲染视口收进框内（`renderer.setViewport/setScissor` + `camera.aspect`），
否则框里看到的是窗口比例取景被遮掉两侧，模型「超出屏幕」，而导出又是真 9:16 —— 预览和成片对不上。

| 项 | 做法 | 说明 |
|---|---|---|
| 视口 | `CUTF={x,y,w,h}`，CSS 语义（y=距顶） | `setViewport` 时翻成 GL 坐标 `innerHeight-y-h` |
| 框外 | 每帧先关 scissor 整屏 `renderer.clear()` 再按 CUTF 画 | 不先清会留上一帧残影 |
| 标签 | `x=CUTF.x+(ndc*0.5+0.5)*CUTF.w`，出画判定也用 CUTF | DOM 标签绝对定位，原来按整窗换算会跑到框外 |
| 切画幅 | 中远景机位 / 分镜按新比例重算 `globalFit` | 特写（r < 旧全局×0.55）保持不动，别把用户要的局部拉远 |
| 视频页 | `window.__frameMode` 时跳过裁切 | 窗口本身就是成片尺寸，再裁一圈出片会带黑边 |
| 导出 | `expSize()` 按 `expAr()` 算 W/H | 与预览的 `fitAr()` 同一口径 |

**关键**：调完点「复制参数」，那一串就是视频渲染的唯一输入源，页面预览与 `video.html` 共用同一套 `applyCam / shotAt`，构图一致。
**注意**：开 9:16 遮罩 + 拖播放头看到的才是最终视频构图，直接看全屏窗口会误判。

---

## 7. 出片前：单文件版与视频页

```bash
python3 make_standalone.py   # → index_standalone.html（约 4 MB，file:// 双击可用）
python3 video.py             # → video.html（无 UI、确定性镜头）
```

- **单文件版存在的唯一原因**：`file://` 下浏览器拦截 ES module（CORS origin null），页面会永远停在「正在加载 three.js」。普通 `index.html` 硬开就是这个现象，不是 bug。
- 打包要求：`three.core.js` / `three.module.js` / `OrbitControls` / `STLExporter` / `GLTFExporter` 各放进**独立函数作用域**再内联，否则不同模块的同名内部变量会重复声明报错；卫星图要 base64 内联。
- `video.py` 的 `SHOTS` **不要写死**，从 `track.json` 读（单一数据源），否则页面改了分镜、视频还用旧镜头。

---

## 8. 渲染

**默认走 §8.2 网页直出**：右侧面板顶部「⬇ 导出 MP4」，10 s 1080p60 约 4 s 出片，
构图与页面完全一致。下面的 `render_frames.mjs` 链路只在需要「无人值守批量 / 断点续渲」时用。

```bash
python3 -m http.server 8766 --directory D:\agent\svg3d\terrain-hd   # 先起服务
node render_frames.mjs "" 1080 1920 30 12       # 尺寸 / 帧率 / 时长
FRAME_LIST=0,72,144,204,239 node render_frames.mjs   # 先出 5 张关键帧验构图
TEST_FRAMES=3 node render_frames.mjs                 # 冒烟测试
```

- **先 5 帧确认构图，再全量渲**。全量渲一次十几分钟，构图错了白跑。
- 时长：不传第 6 个参数时自动读 `track_style.json` 的 `dur`（上限 60 s，与页面一致）；传了以传的为准。
- 轨迹样式：`TRACK_STYLE="trkw=8&trkc=ffcc00"` 环境变量优先于 `track_style.json`。
- 断点续渲靠 `frames_*/` 里已有帧，**重渲前必须把旧帧挪走**（项目里 `frames_*_old` 就是这么来的），否则会拼进上一次的画面。
- 竖屏 1080×1920 / 720×1280，24 或 30 fps，输出 H.264 + yuv420p（剪映能直接吃）。

### 8.1 渲染后端与分段（长片必看）

| 后端 | 开关 | 1920×1080 单帧 | 稳定性 |
|---|---|---|---|
| **D3D11 硬件光栅（默认）** | 不用加 | **≈0.35 s** | 1800 帧一口气跑完不降速 |
| SwiftShader 软件渲染 | `RENDER_GPU=0` | ≈2 s，且越跑越慢 | **跑到 100 帧左右标签页必挂**，`CDP timeout: Runtime.evaluate` |

- 软件渲染挂死后重试是死循环：整段从头再渲、渲到同一个位置又挂。先在软件后端上死磕过一轮，5 次重试全废（日志留着 `_render_chunks.log`）。
- 两种后端画面几乎一致（同参数同机位，PSNR 46–48 dB，肉眼只看得出抗锯齿差异），换后端不值得重调构图。
- 长片走 `bash _render_chunks.sh`：默认 300 帧一段、每段新开浏览器、失败重试 6 次，最后自动合成 MP4。
- 分段脚本**不要**逐帧 `stat` 扫目录来决定渲染哪几帧：Windows 上 300 次 `stat` 要 14 s，放到后台 shell 里会直接卡住不动。整段帧号交给 `render_frames.mjs`，它自己的续渲逻辑（`existsSync + size>20000`）会跳过已有帧。


### 8.2 网页直出 MP4（右侧面板顶部「视频导出」）

页面自己把分镜逐帧渲染 → WebCodecs 编 H.264 → `mp4-muxer` 封 MP4，全程在本机浏览器里，
**不需要 http 服务外的任何依赖**（`vendor/mp4-muxer.js` 已内置，单文件版也内联进了 `__THREE_BUNDLE__`）。

| 项 | 做法 | 备注 |
|---|---|---|
| 逐帧驱动 | `shotAt → applyCam → __applyGrowAt`，与 `video.html` 的 `__setFrame` 同源 | 页面预览的构图 = 导出构图 |
| 出帧渲染器 | 单独建一个 `THREE.WebGLRenderer({preserveDrawingBuffer:true})`，不挂 DOM | 主画布不参与，合成器不抢 GPU |
| 标签 | 用 canvas 2D 按 `updateLabels()` 同一套规则（可见性/遮挡/生长/撞车）重画 | DOM 标签在导出时由 `body.exporting` 隐藏，避免重影 |
| 编码 | `VideoEncoder` + `avc1.640028→…→640034` 由低到高试，取第一个 supported | 1080p 落 High 4.0，4K 自动跳 5.1/5.2 |
| 封装 | `Muxer({video:{codec:'avc',…},fastStart:'in-memory'})` | **v5 的 `codec` 只认族名 `'avc'`**，传 `avc1.xxxx` 会直接抛错 |
| 尺寸 | 短边 = 720/1080/1440/2160，长边按当前画幅（16:9 / 9:16 / 窗口比例）算，强制偶数 | 与窗口大小无关，1080p 就是 1920×1080 |
| 实测速度 | 1920×1080@60，600 帧 ≈ **4 s**；4K 约 150 ms/帧 | 比 `render_frames.mjs` 快两个数量级 |

- 导出期间 `window.__exporting=true`：rAF 循环让位，`syncGrow()` 也不再按 `PLAY` 改 drawRange（否则轨迹会被抹回 0）。
- 取消 / 关闭会把 `camera.aspect`、UI、`__exporting` 全部还原；每次导出重建离屏渲染器，结束后 `dispose()+forceContextLoss()`。
- 自检：`node _exp_check.mjs 1920 1080 600 60`（宽 高 帧数 fps，走真实下载 + ffprobe）；
  `T=0.8 node _exp_cmp.mjs` 出页面同帧截图做像素比对；`node _exp_ui.mjs` 验遮罩/取消/关闭。

---

## 9. 踩坑清单（症状 → 根因 → 修法）

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| 1 | 俯视整图东西镜像 | 北映射到 `+z`，变左手系 | 统一 `-z=North`，走同一个 `to_m()` |
| 2 | 轨迹整条错位/悬空，误差上百米 | 网格索引写成 `j*(NX+1)+i` | `i*(NY+1)+j`，并保留强校验 assert |
| 3 | 轨迹在陡坡穿地、忽隐忽现 | 用双线性插值算地表高度 | 按三角面剖分插值，JS/Python 同一套 |
| 4 | 某些角度看着轨迹腾空 | 低角度 + 白底 + 垂直夸张 + 穿透显示 | 自适应离地(≤3 m) + 假投影 + xray 默认关 |
| 5 | 山体范围不对，轨迹贴边 | 按蓝线（地块）定窗口 | 按轨迹 bbox + 80 m/边 |
| 6 | 卫星图糊 | 默认 `z16`，重跑没带参数 | 显式 `--sat=esri --z-sat=18` |
| 7 | 天地图 418 / 403 | 缺 tk 或 UA / tk 非 32 位 | tk 32 位 + 带浏览器 UA；但成图更糊，自用别选它 |
| 8 | `file://` 双击一直「正在加载」 | ES module 被 CORS 拦 | 用 `index_standalone.html`，或走 http 服务 |
| 9 | 单文件版布局重叠 | 多个模块内联后同名变量冲突 | 各放独立函数作用域；提示条横移避让面板 |
| 10 | 记录的关键帧不是屏幕上的画面 | 记录时读的是滑块缓存 `CAM` | 记录走 `readCam()` 读真实机位，再回写滑块 |
| 11 | 总时长超过 12 s 后加不了镜头 | 改时长时「等比缩放」把末帧永远贴到新末尾 | **只缩不扩**；同刻容差 0.06→0.005 s；时刻框可手填并自动延长 |
| 12 | 页面设 45 s、只渲了 10 s | 渲染脚本写死 `dur<=30` | 上限统一 60 s，未传参时读 `dur` |
| 13 | 面板底部被罗盘盖住 | 两者 `z-index` 相同 | 面板 `z-index` 提到罗盘之上 |
| 14 | 自检打印全 PASS 但退出码 1 | `taskkill /PID <edge> /T /F` 把当前 shell 一起杀了 | 只 `edge.kill('SIGKILL')` |
| 15 | 参数串里 `~` 变成 `%7E` | URLSearchParams 编码 | 正常现象，`unpackShots()` 能解，不用改 |
| 16 | **整条轨迹管消失，只剩一条深色影带，控制台无报错** | 两步路 GPS 高频采样有相邻重复点（本次 285 点里 47 个），`CatmullRomCurve3` 在零长度线段上算出零切向 → `TubeGeometry` 顶点 NaN → 整个 mesh 静默不渲染 | 建曲线前按 **0.5 m 去重**（`dedupeSeg()`），首末重合改 `closed=true` 并去掉末点；GLB 导出走同一份逻辑 |
| 17 | 页面标题出现 `<![CDATA[…]]>` 字面量 | 两步路把 `<name>` 包在 CDATA 里，`html.unescape` 不剥 | `strip_cdata()` 处理 Document 名与 Placemark 名 |
| 18 | 满屏无意义小圆点 | 两步路导出大量无名 `realPoint`，且 `startPoint/endPoint` 与 endpoints 重复 | 只保留有名字的标注，跳过 start/end |
| 19 | 信息栏显示的相关性对不上当前轨迹 | `corr/rmse` 是上一区域硬编码常量 | 每次按当前轨迹实算（KML 高程 vs DEM）；判坐标系时 **corr<0.5 或为负就是纠错了** |
| 20 | 两帧之间镜头突然横扫大半圈 | 方位角按数值线性插值，`268° → 2°` 会走 −266° 而不是 +94° | 把后一帧 az 写成等价角 `+360`（`2 → 362`），**机位完全不变、只改转向**；后续帧同批 +360 保持原速，列表显示用 `azN()` 归一化 |
| 21 | **换区域后渲染出的画面「是另一座山」** | `render_frames.mjs` 的默认 `URL_BASE` 写死 `127.0.0.1:8766`，那是上一个区域的 http 服务；不传第 1 个参数就直接渲了旧工程，画面看着正常、不报错 | 新目录必须把 `URL_BASE` 的端口改成新区域（且 `python3 -m http.server <新端口> --directory <新目录>` 起着），或显式传 `node render_frames.mjs http://127.0.0.1:<端口>/video.html` |
| 22 | 换机位试构图，改 `r` 画面却不变、或几张图字节完全相同 | ①`render_frames` 会跳过已存在的帧（`skipped N`），不删旧帧等于没渲；②`video.py` 里 `VSHOTS = SHOTS.length >= 2 ? SHOTS : __SHOTS__`，**只给 1 个分镜会被整个丢弃**，回落到默认分镜表 | 删掉目标帧再渲；单帧试镜时给两条重复分镜（`A~A`）才能生效；核验用 `__camDebug()`（video.py 已内置）读真实 fov/aspect/距离 |
| 23 | 1920×1080 长片渲到 100 帧上下必挂：`CDP timeout: Page.captureScreenshot` / `Runtime.evaluate`，重试 5 次都在同一位置失败 | SwiftShader 软件渲染跑久了显存/内存膨胀，标签页被拖死并逐渐降速（1.2 s/帧 → 2.5 s/帧） | 默认改 **D3D11 硬件光栅**（`--use-angle=d3d11`，0.35 s/帧）；确要软件渲染时 `RENDER_GPU=0` 且分段压到 40–60 帧 |
| 24 | 分段渲染脚本在后台「启动后没反应」，`[start]` 之后再也不输出 | 脚本里用 `for` + `stat -c%s` 逐帧扫 300 个文件，Windows 上 14 s/段，放到后台 shell 直接卡死 | 别在分段脚本里扫目录，整段帧号交给 `render_frames.mjs` 的断点续渲去跳过 |
| 25 | 加了新控件后整页白屏 / `status` 一直显示加载，控制台报 `Cannot access 'X' before initialization` | 新状态常量（`const TWR` 之类）写在 `styleParams()` 之后，而初始化流程会先调 `styleParams()` → TDZ | 状态变量必须声明在 `styleParams()` **之前**（挨着 `TS` 那组），UI 与几何体再放后面 |
| 27 | 网页导出跑到 ~1 s 就假死：`Runtime.evaluate` 全超时、画面卡住，像编码器挂了 | `drawImage(WebGL canvas)` / `new VideoFrame()` 在 Chrome 里是**异步**的（循环里 R/D/L/V 每项都 0 ms），几百帧 1080p 纹理全排进 GPU 队列（>1 GB），最后 `flush()` 把主线程拖死 | 每帧 `XR.getContext().finish()` 强制同步；出帧渲染器独立成**不挂 DOM 的离屏 renderer**（合成器不再参与） |
| 28 | 编码器 `isConfigSupported` 说支持，`new Muxer()` 却抛 `Unsupported video codec: avc1.640034` | `mp4-muxer` v5 的 `video.codec` 要的是族名 `'avc'/'hevc'/'vp9'/'av1'`；完整串只给 `VideoEncoder.configure` | muxer 传 `'avc'`，编码器另传 `avc1.xxxx` |
| 29 | 导出完自动下载没出现 / 第二次导出不自动落盘 | Chrome 对同一站点连续的「自动下载」会拦截（点遮罩里「下载 MP4」按钮是用户手势，不受影响） | 遮罩保留下载按钮；自动化测试每次换下载目录（`_expdl_<pid>`） |
| 30 | 竖屏 9:16 下模型左右超出画面，导出视频被裁 | `fitR()` 用 `innerWidth/innerHeight`（窗口 16:9）求距离，导出时 `camera.aspect` 是 9/16 | 新增 `fitAr()` 与 `expAr()` 同口径；切画幅时中远景机位 / 分镜按新比例重算 |
| 31 | 环绕转到某个角度，沙盘角被切掉 | 只按起始方位求一次 r；本区模型 822×748，az=90 是窄边、az=60 宽 37% | `orbitR()` 取整圈 4 个方位的最大距离 |
| 32 | 「环绕展示」四角仍轻微出画（自检 corner=7） | `globalFit()` 乘 0.86，把 fitR 的 margin 0.9 放大到 1.047 > 1 | 要求完整的镜头改用 `fitFull()`（margin 0.96 不缩） |
| 33 | 沿线跟拍一顿一顿，像每 0.9 s 停一下 | `shotAt` 每段都套 smoothstep，段末速度归零；跟拍 16 段 / 14 s 正好把脉冲暴露出来 | 模板帧标 `sm:0` 走线性；手调分镜不带 sm，保持原样 |
| 34 | 竖屏预览里标签跑到取景框外 | `updateLabels()` 按 `innerWidth/innerHeight` 算屏幕坐标，没考虑画幅视口 | 用 `CUTF` 映射 + 出画判定；`CUTF=null`（视频页 / 自由比例）时退回整窗 |
| 26 | 低角度掠视时几个途径点标签叠成一串、糊成一片 | ①标签从不做遮挡判断，山背后的标签照样画出来（透视）；②撞车时循环上移 8×20 px，越撞越往上堆 | `labelBlocked()` 按 DEM 采样判遮挡（默认关透视）；撞车改为「最多上移一格，再撞就隐藏、让位给更近的标签」，判定按相机距离排序 |


---

## 10. 换区域 Checklist

改之前先备份：`mkdir -p backup_<主题> && cp track.py build.py index.html track.html index_standalone.html video.html track.json backup_<主题>/`

**要改的行**：

| 文件:行 | 常量 | 改成 |
|---|---|---|
| `build.py:8` | `HERE` | 新项目目录（建议 `D:\agent\svg3d\<区域名>`） |
| `build.py:9` | `GEO` | 新区域 GeoJSON 路径 |
| `build.py:17` | `BUFFER` | 一般保持 80；轨迹特别碎可降到 60 |
| `build.py:18` | `GRID_M` | 窗口 > 1.2 km 时提到 22–25，控面数 |
| `build.py:20` | `BASE` | 保持 -22 |
| `track.py:11` | `HERE` | 同上新目录 |
| `track.py:13` | `KML` | 新轨迹 KML 路径 |
| `track.py:14` | `LIFT` | 保持 7.0 |
| `video.py:14` | `HERE` | 同上新目录 |
| `render_frames.mjs:21` | `ROOT` | 同上新目录 |
| `render_frames.mjs:25` | `URL_BASE` 端口 | 改成新区域的 http 端口（旧区域 8766，勿复用） |

`make_standalone.py` 用 `Path(__file__).parent` 定位，**不用改**；`index.template.html` 是页面模板，换区域也不用改。
`_cam_check.mjs` / `_play_check.mjs` / `_diag.mjs` 里的 ROOT 和 URL 是写死的，换目录后要同步改，否则自检跑的是旧项目。
`render_frames.mjs` 的 `URL_BASE` 端口同理（默认 8766 = 旧区域服务），新区域要改端口，否则渲出来是上一座山（踩坑 #21）。

需要一起复制的东西：`build.py` `track.py` `bootstrap_bbox.py` `video.py` `make_standalone.py`
`render_frames.mjs` `index.template.html` `vendor/`（5 个 three.js 文件）`MAKING_RULES.md`。

**顺序不能乱**：`build.py` 读 `track.json` 取轨迹 bbox，`track.py` 读 `mesh.json`。
新目录第一次按这三步走：

```bash
python3 bootstrap_bbox.py --kml "E:\下载\新区域.kml"      # 0) 占位 track.json（只含经纬度）
python3 build.py --sat=esri --z-sat=18                    # 1) 定窗口 + 建地形
python3 track.py                                          # 2) 覆盖成完整 track.json + 贴地
```

`bootstrap_bbox.py` 已用现有 KML 验证：295 点、外接框 662×588 m，与 `track.json` 完全一致。
它只算外接框，不贴地、不纠偏，所以新区域坐标判断仍要按第 3.1 节确认。

**验收顺序**（缺一项就可能带病交付）：
0. **先和用户确认窗口口径**（贴轨迹 / 包山 / 按轮廓），再 `--dry-run` 看尺寸与 `track in window` 是否 100%
1. `build.py` 输出 `non_manifold=0`，窗口尺寸/高程范围合理
2. `track.py` assert 通过，框内轨迹占比打印出来（低于 60% 说明窗口偏了）
3. 浏览器开 `http://127.0.0.1:8766/index.html`：默认视角无镜像、轨迹贴地、无 console 报错
4. 正俯视对照真实地图（`?top=1`）：山脊走向、道路、轨迹形状一致
5. 低角度掠视：轨迹不穿地、不腾空
6. 拖播放头 + 开 9:16：主体不出安全框
7. `index_standalone.html` 双击能开
8. `video.html` 逐帧接口正常（`__setCamera` 可调用）
9. 跑既有自检：`node _cam_check.mjs` / `_play_check.mjs` / `_diag.mjs`（新区域要改脚本里的 `ROOT`/URL）

---

## 11. 设计口径（避免反复返工）

- **风格**：插画沙盘（非绿色调）+ 薄底座 + 白底柔光 + 圆角。用户明确不要绿色山体、不要水面。
- **轨迹**：橙红 `#ff4d1a` + 白包边，粗细 4.2 m 起步；在 800 m 窗口下这个值在 1080 竖屏里约 3–4 px。
- **占比**：轨迹占画面 60–70%，剩余留给山体轮廓和留白。
- **竖直夸张**：保持 1:1（不要为了"好看"拉高），拉了会导致坡度观感失真、轨迹更容易腾空。
- **文案**：不虚构地名，途径点由用户自己加。
