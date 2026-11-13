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
version: 1.1.0
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
3. **不主动渲染视频**：`render_frames.mjs` 会跑十几分钟，只在用户明确说"渲染"时才执行。先 `FRAME_LIST` 出 5 帧验构图。
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
# 6) 页面调参 → 点「复制参数」→ 参数串给渲染用
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
| 画幅 | 9:16 竖屏（抖音）/ 16:9 | 拖播放头 + 开遮罩才是真实构图 |

## 最容易错的 6 件事

1. **整图镜像**：北必须映射到 `-z`（右手系），映射到 `+z` 俯视时东西反。
2. **轨迹错位几百米**：网格索引是 `i*(NY+1)+j`，不是 `j*(NX+1)+i`（`build.py` 里的同名函数是死代码，别抄）。
3. **轨迹穿山**：地表高度必须按三角面剖分插值（`(a,b,c)+(a,c,d)`），双线性在陡坡误差可达 20 m。
4. **轨迹看着腾空**：不是数据错，是低角度 + 白底 + 竖直夸张 + 穿透显示。开自适应离地 + 假投影，xray 默认关。
5. **`file://` 一直加载**：ES module 被 CORS 拦，用 `index_standalone.html` 或起 http 服务。
6. **卫星图糊**：`build.py` 默认 `z16`，重跑必须显式 `--sat=esri --z-sat=18`。

## 验收（缺一项就可能带病交付）

```bash
# 1 建模水密
python3 build.py --sat=esri --z-sat=18 2>&1 | grep non_manifold   # 必须 =0
# 2 轨迹贴地强校验（track.py 内置 assert，不通过会直接报错）
python3 track.py
# 3 浏览器自检
node _cam_check.mjs && node _play_check.mjs && node _diag.mjs
```

人工确认：默认视角无镜像 → `?top=1` 正俯视对照真实地图 → 低角度掠视不穿地不腾空 → 9:16 主体不出框 → 单文件版双击可开。

## 出片（用户明确要求时才做）

```bash
node render_frames.mjs "" 1080 1920 30 12            # 1080 竖屏
FRAME_LIST=0,72,144,204,239 node render_frames.mjs   # 先验 5 帧构图
TRACK_STYLE="trkw=8&trkc=ffcc00" node render_frames.mjs
```

重渲前把 `frames_*/` 里旧帧挪走（改成 `frames_*_old`），否则续渲会拼进旧画面。
