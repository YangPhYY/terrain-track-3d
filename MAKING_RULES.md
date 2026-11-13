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
   → 页面调参 → 复制参数 → render_frames.mjs（等你通知才渲染）
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

本工程当前用的轨迹就是**奥维 LineString**（`山顶人家小顶山环线.kml`，295 点，
`track.json.meta.source` 会写明来源与坐标系），所以地形那份 GeoJSON 走 `gcj2wgs()`、
轨迹那份不动，两者才对得上。

判定方法（本区域实测）：把 KML 点打在地形上，看 KML 高程与 DEM 高程的相关性 ——
**未纠偏 0.9344 / RMSE 21.87 m；强行纠偏 −0.7282 / RMSE 113.56 m**。相关性为负就说明纠错了。
代码里保留这两个数，换区域可以先跑一次相关性再决定。

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

---

## 6. 页面能力（都已实现，调参即可）

| 模块 | 能力 | 关键参数 |
|---|---|---|
| 底图 | 插画沙盘 / 卫星 双模式 | `art=1|0` |
| 色板 | 秋冬赭褐 / 暖黄土坡 / 灰岩冷调 / 素模石膏 | `pal=autumn|loess|slate|mono` |
| 轨迹 | 粗细、颜色、包边、透明度、离地、自动离地、假投影、穿透 | `trkw/trkc/trkhl/trkhc/trkop/trklift/trkauto/trkshadow/trkx` |
| 途径点 | 页面上点选添加、改大小/字号/标签/颜色 | `wp/wpsz/wpfs/wplbl` |
| 镜头 | 7 滑块精调 + 分镜关键帧 + 播放预览 + 9:16 / 16:9 安全框 | `cam/shots/dur/asp` |
| 导出 | STL / GLB（含轨迹）/ 轨迹 JSON / 复制参数 | 页面按钮 |

**镜头默认值**：页面 `fov=44`；分镜默认 5 帧 / 10 s / `fov=58`。
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

## 8. 渲染（默认不跑，等明确通知）

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

`make_standalone.py` 用 `Path(__file__).parent` 定位，**不用改**；`index.template.html` 是页面模板，换区域也不用改。
`_cam_check.mjs` / `_play_check.mjs` / `_diag.mjs` 里的 ROOT 和 URL 是写死的，换目录后要同步改，否则自检跑的是旧项目。

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
