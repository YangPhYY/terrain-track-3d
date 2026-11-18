---
name: terrain-track-3d
display_name: "山地 3D 沙盘 + 轨迹叠加"
display_name_en: "Mountain 3D Diorama with GPS Track Overlay"
description_zh: |
  用 GeoJSON 区域轮廓 + KML 轨迹 + 在线 DEM/卫星瓦片，生成可交互的 three.js 山地 3D 沙盘，
  并把真实坐标轨迹贴地叠加，支持插画/卫星双模式、轨迹样式、途径点、镜头分镜、竖屏安全框、
  离线单文件版与逐帧渲染出片。
  触发词：3D 地形、山地模型、沙盘、轨迹叠加、KML 上模型、海拔剖面、轨迹动画、换个区域做模型。
  流程约定：用户发来轨迹后，**先弹窗让其选择建模范围**（贴轨迹 / 包住整座山 / 按区域轮廓 / 手填），
  再 `--dry-run` 给数字确认；只给 KML 也能建，GeoJSON 仅用于画边界线。
  适用：已有 D:\agent\svg3d\terrain-hd 工程，要为新的区域重做一份。
description_en: |
  Build an interactive three.js mountain diorama from a GeoJSON outline + KML track,
  with DEM/satellite tiles, ground-snapped track, stylized/satellite modes, waypoints,
  camera keyframes, offline single-file build, and frame-by-frame video export.
version: 1.3.1
agent_created: true
license: MIT
category: media
platforms:
- WorkBuddy
---

# 山地 3D 沙盘 + 轨迹叠加

> **完整手册**：`D:\agent\svg3d\terrain-hd\MAKING_RULES.md`（换区域前必读，含全部数值依据与 15 条踩坑）
> **参考工程**：`D:\agent\svg3d\terrain-hd`（结构完整、可直接复制）

## 铁律（违反必返工）

1. **只改生成源**：`build.py` / `track.py` / `video.py` / `render_frames.mjs`。绝不直接改生成出的 `index.html`、`track.html`、`video.html`。
2. **改完必须重建 + 跑自检**：`python3 track.py && python3 video.py && node <对应>_check.mjs`。
3. **不主动渲染视频**：`render_frames.mjs` 会跑十几分钟，只在用户明确说"渲染"时才执行；说「导出视频」时优先让用户在页面点「⬇ 导出 MP4」（网页直出，秒级）。走命令行时先 `FRAME_LIST` 出 5 帧验构图。
4. **重建地形必须联网**：DEM 与卫星瓦片都是在线拉取，离线会直接失败。
5. **坐标别乱纠偏**：两步路 `gx:Track` = WGS84 原样用；奥维 `LineString` = CGCS2000 不纠偏；DataV/高德 GeoJSON = GCJ02 必须 `gcj2wgs()`。

## ⚠️ 开工第一件事：弹窗问「建模范围」（必做，不许默认跳过）

用户发来轨迹后，**先别跑 build.py**，用 `AskUserQuestion` 弹一个选择题。原因：轨迹常常只是路过一座山，按轨迹裁会把山切掉一半；这是用户明确要求每次都问的点。

题目写成：「这条轨迹要怎么定建模范围？」
Header：`建模范围`

| 选项 label | description | 对应命令 |
|---|---|---|
| **贴轨迹裁（推荐）** | 轨迹外接框 +80 m/边，主体最大，轨迹占屏 60–70% | `--box=auto --buffer=80` |
| **包住整座山** | 轨迹外接框 +250 m/边，山体完整、轨迹略小；仍被切可加到 400 | `--buffer=250` |
| **按区域轮廓** | 用你给的 GeoJSON 轮廓 +80 m/边，保住整块地（需 GeoJSON） | `--box=geo` |
| **我手填经纬度范围** | 你给 minlon,minlat,maxlon,maxlat，我直接按这个框建 | `--box=...` |

追问时机：
- 用户只发 KML、没说范围 → **必须问**。
- 用户说「轨迹只走一条沟/一段山脊」→ 主动推荐「包住整座山」。
- 用户已有 GeoJSON 且强调「这块地」→ 推荐「按区域轮廓」。
- 用户说「你看着办」→ 用默认「贴轨迹裁」，并在交付时说明可随时改。

选完先干跑给用户看数字，再真建：

```bash
python3 build.py --dry-run --buffer=250     # 只打印窗口，不联网、不写文件
```

输出里看两项：窗口尺寸是否符合预期、`track in window` 是否 100%（低于 95% 说明有轨迹点在框外）。

## 标准流程

```bash
cd /d/agent/svg3d/terrain-hd          # 或新区域目录
python3 bootstrap_bbox.py --kml "新轨迹.kml"   # 0) 新目录必需：占位 track.json
python3 build.py --dry-run --buffer=80        # 1) 先看窗口，跟用户确认
python3 build.py --sat=esri --z-sat=18       # 2) 建地形（务必显式带这两个参数）
python3 track.py                              # 3) 重新贴轨迹 + 注入页面 + 出单文件版
python3 video.py                              # 4) 出视频页（可选）
# 5) 预览：python3 -m http.server 8766 --directory <目录>  → http://127.0.0.1:8766/index.html
# 6) 页面调参 → 面板顶部「⬇ 导出 MP4」直接出片（推荐）
#    或点「复制参数」→ 参数串交给 render_frames.mjs（无人值守/批量）
```

顺序不能反：`build.py` 读 `track.json` 取轨迹 bbox，`track.py` 读 `mesh.json` 取网格。
新目录里两个文件都没有，所以必须先 `bootstrap_bbox.py` 破局（它只算外接框，不贴地）。
已有 `mesh.json` 的老目录直接跑 `track.py` 即可。

**用户只给 KML 就够**：GeoJSON 是可选件，只用来画地块边界线（蓝=纠偏后/橙=原始），不参与地形。
`build.py` 已支持缺省——找不到 GeoJSON 会打印 `outline: none` 并继续，窗口照样按轨迹算。

## build.py 命令行参数

| 参数 | 作用 |
|---|---|
| `--sat=esri\|tianditu` | 卫星源；自用剪裁默认 esri |
| `--z-sat=18` | 卫星层级；**不写就退回脚本默认 z16，图会糊且不报错** |
| `--buffer=NN` | 每边外扩米数（默认 80，包山用 250/400） |
| `--box=auto\|geo\|minlon,minlat,maxlon,maxlat` | 窗口口径 |
| `--geo=路径` | 临时指定区域轮廓文件 |
| `--dry-run` | 只算窗口并打印，不联网、不写文件 |
| `--sat-only` | 只拉贴图自检，不动模型文件 |

## 新区域怎么做

```bash
cd /d/agent/svg3d
mkdir -p <新区域名> && cd <新区域名>
# 复制工程骨架（不复制历史帧和备份）
cp -r ../terrain-hd/{build.py,track.py,bootstrap_bbox.py,video.py,make_standalone.py,render_frames.mjs,index.template.html,vendor} .
cp -r ../terrain-hd/{_cam_check.mjs,_play_check.mjs,_diag.mjs,MAKING_RULES.md} .
```

然后只改这几行（其余常量保持）：

| 文件:行 | 常量 | 说明 |
|---|---|---|
| `build.py:8` | `HERE` | 新目录 |
| `build.py:9` | `GEO` | 新区域 GeoJSON；**没有就留空**（也可用 `--geo=` 临时指定） |
| `build.py:17` | `BUFFER` | 默认 80 m/边；临时改用 `--buffer=` |
| `track.py:11` | `HERE` | 新目录 |
| `track.py:13` | `KML` | 新轨迹 KML |
| `track.py:14` | `LIFT` | 保持 7.0 |
| `render_frames.mjs:21` | `ROOT` | 新目录 |
| `render_frames.mjs:25` | `URL_BASE` | 端口改成新区域（默认 8766 是上一个区域的服务，不改就渲了旧山，画面正常不报错） |

## 页面交互（交付时一并告诉用户）

| 功能 | 用法 | 备注 |
|---|---|---|
| 收起两侧面板 | 屏幕左右边缘中部的竖条把手；或按 `[` 收左、`]` 收右、`H` 一起收/放 | 状态存 `localStorage['terrain.panel']`，刷新与单文件版都记得；`video.html` 里自动 `display:none` |
| 运镜模板 | 途径点巡礼 / 沿线跟拍 / 环绕展示 / 高程跟随 / 俯冲进场 / 拉升退场 / 俯视平面 / 单点环绕 | 右侧「镜头控制」下方 8 个按钮，点一下覆盖分镜表；距离由 `fitR()` 按模型对角线**和当前画幅**自动算，换区域/换竖屏都不用改数字 |
| 画幅裁切 | 9:16 / 16:9 切换后，取景框内就是成片画面 | 不只是遮罩：`applyAspect()` 同时改 `camera.aspect` + `renderer.setViewport/setScissor`，标签坐标也按框映射；切画幅会自动把中远景机位/分镜按新比例重算 |
| 生长跟随镜头 | 相机方位贴着轨迹生长点走，前沿始终在画面中部 | `python3 _growcam.py 8 > _grow_shots.txt`，把结果替换参数串里的 `shots=`；固定角速度环绕会与轨迹反向、前沿频繁出画 |
| 轨迹生长 | 轨迹从起点逐段长出 | 勾「轨迹生长」+ 起/用时，参数 `grow=1&grows=0&growd=4`；逐帧渲染靠 `window.__frameMode` 让位；球/标签按里程比例一起出现（`growT()` 把 t≥0.98 的末点按起点处理），探针要看 `opacity` |
| 标注点开关 | KML 航点（转路/岔路提示）单独关闭 | 右侧「标注点」勾选，参数 `mk=0`；与「起终点标记」`ends=0` 是两件事 |
| 标签加粗 | 途径点名称用 800 字重，缩小或压缩后仍清楚 | 右侧「加粗」勾选框，参数 `wpb=1`；只影响途径点，标注点标签不变 |
| 标签不透视（默认） | 被山体挡住的途径点标签自动隐藏 | 开关是右侧面板「标签透视（被山挡也显示）」，参数 `wpocc=1`；关着时低角度掠视不会出现背后标签透出来 |
| 标签防拥挤 | 撞车的标签最多上移一格，再撞就直接隐藏，让位给**更近**的标签 | 以前是循环上移 8 次，低角度会把几个标签叠成一串；判定用相机距离排序 + 屏幕矩形相交 |
| **网页直出视频** | 右侧面板**顶部**「⬇ 导出 MP4」：选 24/30/60 fps 与 720p–4K，点一下开始，进度条跑完自动下载 | 纯浏览器内 WebCodecs(H.264)+`mp4-muxer`，10 s 1080p60 ≈ 4 s 出片；尺寸跟「画幅」走（16:9→1920×1080、9:16→1080×1920）；标签按 canvas 2D 重画，导出时别切走标签页 |

改这两处只动 `index.template.html`（面板把手）和 `track.py`（`labelBlocked()` / `updateLabels()`），**不要手改生成的 html**；改完 `python3 track.py && python3 video.py` 重生成，跑 `node _panel_check.mjs` 自检（13 组，含把手/快捷键/刷新记忆/遮挡/不重叠/单文件版/video 页不出 UI）。

## 关键常量（出厂值，改动前先看手册第 2 节）

| 项 | 值 | 为什么 |
|---|---|---|
| 建模窗口 | 轨迹 bbox + **80 m/边**（包山用 250） | 轨迹占屏 60–70%，再大主体变小 |
| 格距 `GRID_M` | 18 m | 800 m 窗口下 46×42 网格，面数适中 |
| 底座 `BASE` | −22 m | 插画风要薄侧壁；−60 会出黑悬崖 |
| DEM | Terrarium **z13**（≈30 m） | 精度与瓦片量平衡 |
| 卫星 | Esri **z18** | 自用/剪辑首选；对外发布换天地图 img_w |
| 轨迹管 | 半径 **4.2 m**，色 `ff4d1a`，白包边 1.8× | 1080 竖屏约 3–4 px |
| 贴地抬升 | `LIFT=7` + 自适应平均 +2.5 m + 假投影 | 防 z-fighting + 低角度接触感 |
| 分镜 | 5 帧 / 10 s / fov 58 | 页面 fov 44 是自由视角 |
| 画幅 | 9:16 竖屏（抖音）/ 16:9 | 切了就是**真裁切视口**（aspect + viewport），框内=成片；标签按框映射，不再跑出框 |

## 八个运镜模板（点一下写入分镜表）

| 按钮 | 生成逻辑 | 时长 |
|---|---|---|
| 途径点巡礼 | **沿轨迹连续前进**，靠近途径点自动放慢（≤2.2×）+ 轻微推近（0.9×），末段拉回全览定格 | 点数×1.5（8–20 s）+ 4.2 s |
| 沿线跟拍 | 轨迹后上方 0.34 / 0.20×对角线，朝前进方向，结尾全览定格 | 14 + 3.8 s |
| 环绕展示 | 匀速一圈 az 40→400，首尾同角可接；全程全览 | 10 s |
| 高程跟随 | 0.23 / 0.11×，贴地随坡，结尾全览定格 | 16 + 3.8 s |
| 俯冲进场 | 高空 → 起点低角度 → 拉回全览 | 4.2 + 3.4 s |
| 拉升退场 | 终点低角度 → 全局 | 5.6 s |
| 俯视平面 | el 89°、北朝上，缓推 7% 后定格 | 4.4 s |
| 单点环绕 | 绕最近的途径点一圈 → 全览定格 | 5 + 3.8 s |

**结尾铁律**：除环绕 / 俯视本身全程全览，其它模板都由 `endGlobal()` 追加「拉远 + 抬高到 el 34 + 中心」两帧，
定格 2 s。用户明确要求过：**无论哪种镜头，最后的定格都必须是 3D 模型全览，不能被画幅边框切**。

**取景三条铁律**（2026-11-18 全量排查后定稿）：

1. **距离按画幅算**：`fitR()` 用 `fitAr()`（16:9 / 9:16 / 窗口比例），不能直接用 `innerWidth/innerHeight`。
   竖屏横向视野只有横屏 1/3，用窗口比例求 r，模型必然左右出框 —— 这就是「竖版 9:16 模型超出屏幕」的根因。
2. **环绕取整圈最大距离**：`orbitR()` 对 4 个方位各求一次 `fitR` 取 max。只按起始方位算，转到「宽边」就裁角
   （单点环绕原来按 az=90 窄边算，转到 60° 横向差 37%）。
3. **要完整的镜头用 `overviewR()`（`allPts()` + margin 0.90）**，不要用 `globalFit()`：后者乘 0.86，
   会把 `fitR(margin 0.9)` 放大到 ndc≈1.05，角点出画。`globalFit` 只留给初始视角这类允许裁角的场景。
   `allPts()` = 8 角点 + 所有标签锚点（不然贴边的地名会被框线切）。
4. **载入分镜先校核**：`normOverview()` 对「中心目标 + r > 0.72×对角线」的全局机位，距离不足就补到当前画幅的
   全览距离（只加不减）。16:9 写好的分镜拿到 9:16，r 只有一半，模型必然左右出框。
5. **视频页画幅以窗口为准**：`video.py` 的 FRAME_JS 最前面按 `innerWidth/innerHeight` 校正 `ASP` 并 `normOverview()`，
   而且必须在 `const VSHOTS = SHOTS…` **之前**（它会重建数组）。URL 里的 `asp=` 只用于页面预览。
   视频页也不要写死 `group.scale.y`，统一听 `ex=`。

其它三个易漏点：

- 模板帧带 **`sm:0`** 走线性插值：跟拍 16 段、每段 0.9 s，`smoothstep` 每段末速度归零 → 走一下停一下。
  手调「记录当前画面」的帧不带 `sm`，保持缓入缓出。
- **顶视时 `fitR` 的临时相机也要 `up=(0,0,-1)`**（与 `applyCam` 一致），否则判定 roll 差 90°，长条形模型「算着装下了、实际超框」。
- **巡礼 / 单点环绕用 `readCam()`，不要用 `CAM`**：拖动视角不回写 `CAM`，按 `CAM.az/tx` 会拿旧位置选点。
- **竖屏跟拍要抬远**：9:16 横向视野只有 1/3，同样的 back/h 只剩一条窄带。`tourShots()` 在 `fitAr()<1` 时
  back/look ×1.6、h ×2.0。
- **标签贴边往里收**：页面 `updateLabels()` 与导出 `expDrawLabels()` 都做 clamp，否则最外侧地名被边框切半。

自检：`node _cam_tpl_check.mjs` —— 16:9 与 9:16 各跑一遍，用页面里的 `__camAudit()` 逐帧投影统计
「模型 8 角点 / 轨迹采样点」入画数：**所有模板的最后一帧必须 `corner=8`（全览定格）**，
环绕与俯视全程 `corner=8`，跟拍要求每帧有轨迹（`trk≥1`），且全程无 NaN。

## 最容易错的 7 件事

1. **整图镜像**：北必须映射到 `-z`（右手系），映射到 `+z` 俯视时东西反。
2. **轨迹错位几百米**：网格索引是 `i*(NY+1)+j`，不是 `j*(NX+1)+i`（`build.py` 里的同名函数是死代码，别抄）。
3. **轨迹穿山**：地表高度必须按三角面剖分插值（`(a,b,c)+(a,c,d)`），双线性在陡坡误差可达 20 m。
4. **轨迹管整条消失、只剩深色影带**：两步路 GPS 有相邻重复点 → CatmullRom 零切向 → TubeGeometry 顶点 NaN，**不报错**。建曲线前按 0.5 m 去重（`dedupeSeg`），首末重合用 `closed=true`。验伪：`trkw=40&trkc=00ff00&trkshadow=0` 都看不见就是 NaN。
5. **轨迹看着腾空**：不是数据错，是低角度 + 白底 + 竖直夸张 + 穿透显示。开自适应离地 + 假投影，xray 默认关。
6. **`file://` 一直加载**：ES module 被 CORS 拦，用 `index_standalone.html` 或起 http 服务。
7. **卫星图糊**：`build.py` 默认 `z16`，重跑必须显式 `--sat=esri --z-sat=18`。
8. **渲出来是上一座山**：`render_frames.mjs` 的 `URL_BASE` 默认指旧区域端口 8766，新目录不改就渲旧工程，画面正常、不报错。单帧试镜还要注意：只给 1 条分镜会被丢弃，旧帧不删会被 `skipped`。

## 换轨迹时额外三件事（两步路）

- `<name>` 常包 `<![CDATA[…]]>`：`html.unescape` 不剥，需 `strip_cdata()`，否则标题字面量出现在页面上。
- 会导出大量**无名 `realPoint`**：只保留有名字的标注，跳过 `startPoint/endPoint`（与 endpoints 重复）。
- `corr/RMSE` 已改为每次按当前轨迹实算：**>0.9 可信，<0.5 大概率坐标系错，负值一定错**。

## 验收（缺一项就可能带病交付）

```bash
# 1 建模水密
python3 build.py --sat=esri --z-sat=18 2>&1 | grep non_manifold   # 必须 =0
# 2 轨迹贴地强校验（track.py 内置 assert，不通过会直接报错）
python3 track.py
# 3 浏览器自检
node _cam_check.mjs && node _cam_tpl_check.mjs && node _play_check.mjs && node _panel_check.mjs && node _diag.mjs
```

`_panel_check.mjs` 覆盖 13 组：左右把手收起/展开、`[` `]` `H`、刷新记忆、输入框内不触发、标签遮挡、标签不重叠、video 页不出 UI、单文件版可用。

人工确认：默认视角无镜像 → `?top=1` 正俯视对照真实地图 → 低角度掠视不穿地不腾空 → 9:16 主体不出框 → 单文件版双击可开。

## 出片（用户明确要求时才做）

**首选网页直出**：页面右侧面板顶部「⬇ 导出 MP4」直接出片，构图与预览逐像素一致，
不用起 Node / ffmpeg，也不用等十几分钟。交付时告诉用户这条路径即可。

需要 4K 长片、无人值守批量或断点续渲时，才走下面的命令行链路：

```bash
node render_frames.mjs "" 1080 1920 30 12            # 1080 竖屏
FRAME_LIST=0,72,144,204,239 node render_frames.mjs   # 先验 5 帧构图
TRACK_STYLE="trkw=8&trkc=ffcc00" node render_frames.mjs
bash _render_chunks.sh                               # 长片：300 帧一段各开新浏览器，末了自动合成
```

- 渲染后端默认 **D3D11 硬件光栅**（1920×1080 ≈0.35 s/帧，长跑不降速）。`RENDER_GPU=0` 才退回 SwiftShader（≈2 s/帧，**跑到 100 帧左右标签页必挂**）。
- 重渲前把 `frames_*/` 里旧帧挪走（改成 `frames_*_old`），否则续渲会拼进旧画面。

