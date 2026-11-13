"""把 KML 徒步轨迹按真实 WGS84 坐标叠加到既有 terrain-hd 模型上。

不重建地形：直接读取 mesh.json 的网格与高程，把落在建模 bbox 内的轨迹点
转换为模型局部米制坐标，贴地生成折线；范围外的点一律舍弃（有多少落多少）。

输出：track.json / track_in_model.geojson / track.html / index.html
运行：python3 track.py
"""
import json, math, os, re, sys, html as htmlmod

HERE = os.environ.get("TERRAIN_HOME") or os.path.dirname(os.path.abspath(__file__))
# 在两种 KML 格式间切换：奥维 LineString / 两步路 gx:Track
# 命令行 --kml=路径 可临时覆盖，优先级高于下面的默认值
KML = r"E:\下载\山顶人家小顶山环线.kml"
for _a in sys.argv[1:]:
    if _a.startswith("--kml="):
        KML = _a.split("=", 1)[1].strip()
if not os.path.isfile(KML):
    raise SystemExit("KML 不存在: %s（可用 --kml=路径 指定）" % KML)
LIFT = 7.0          # 精确贴地后仅需微量抬升，避免 z-fighting（模型单位 m）
# ---- 轨迹外观默认值；页面上可实时调节并自动保存在浏览器，导出视频可复用 ----
TUBE_R = 4.2        # 轨迹管半径（m）
TRACK_COLOR = "ff4d1a"   # 轨迹主色（6 位 hex，不带 #）
HALO_SCALE = 1.8    # 白色包边半径倍数，0 = 关闭包边
HALO_COLOR = "ffffff"
TRACK_OPACITY = 1.0
TRACK_STYLE = {"w": TUBE_R, "c": TRACK_COLOR, "hl": HALO_SCALE,
               "hc": HALO_COLOR, "op": TRACK_OPACITY, "xr": False,
               "lift": LIFT,       # 离地高度（m）：页面上可手调 / 按坡度自动
               "auto": True,       # 自动模式：缓坡贴地、陡坡略抬升，平衡贴地感与可见度
               "shadow": True}     # 贴地假投影：给低角度视角一个「地面在哪」的接触线索
# ---- 途径点默认值：默认不预置任何点（不虚构地名），由用户在页面上添加 ----
WAYPOINT_DEF = {"size": 12.0, "fs": 12.0, "label": True, "list": []}
# ---- 默认分镜表：单一数据源，video.py 也从 track.json 读它 ----
# 页面「镜头控制」可覆盖，结果经 shots= 参数传给 video.html；不填参数时行为与旧版一致。
DEFAULT_SHOTS = [
    {"t": 0.0,  "az": 140, "el": 38, "r": 1480, "tx": 0,   "ty": 130, "tz": 0,  "fov": 58},
    {"t": 3.0,  "az": 178, "el": 33, "r": 1320, "tx": -15, "ty": 130, "tz": 15, "fov": 57},
    {"t": 6.0,  "az": 214, "el": 42, "r": 1140, "tx": -30, "ty": 145, "tz": 25, "fov": 56},
    {"t": 8.5,  "az": 252, "el": 50, "r": 1080, "tx": -35, "ty": 150, "tz": 30, "fov": 58},
    {"t": 10.0, "az": 282, "el": 40, "r": 1430, "tx": 0,   "ty": 130, "tz": 10, "fov": 58},
]
DEFAULT_DURATION = 10.0
DEFAULT_ASPECT = "0"          # 0=不显示画幅框 / 916=竖屏 / 169=横屏
COORD_NOTE = "CGCS2000（≈WGS84，未纠偏）"
CORR_OK, RMSE_OK = 0.9344, 21.87
CORR_BAD, RMSE_BAD = -0.7282, 113.56

mesh = json.load(open(os.path.join(HERE, "mesh.json"), encoding="utf-8"))
M = mesh["meta"]
NX, NY = M["grid"]
W, H = M["width_m"], M["height_m"]
box = M["bbox_wgs84"]
lon0, lat0 = M["center"]
EMIN = M["elev_min"]
GRID_ELEV = mesh["elev"]


def idx(i, j):
    # build.py 里顶点按 i 外层 / j 内层 append，索引是 i*(NY+1)+j；
    # （build.py 中同名函数写成 j*(NX+1)+i，但那是未被调用的死代码）
    return i * (NY + 1) + j


def to_m(lon, lat):
    # 与 build.py 一致：x=东、-z=北（右手系）
    return ((lon - lon0) / (box[2] - box[0]) * W,
            (lat0 - lat) / (box[3] - box[1]) * H)


def dem_at(x, z):
    """按地形实际三角剖分（a,b,c)+(a,c,d) 做插值，与渲染网格逐点一致。

    用双线性会在陡坡上产生最高约 20 m 偏差，导致轨迹埋进地表、视觉断续。
    """
    fi = (x + W / 2) / W * NX
    fj = (H / 2 - z) / H * NY     # -z 为北，j 随纬度增大（南→北）
    fi = max(0.0, min(NX - 1e-9, fi))
    fj = max(0.0, min(NY - 1e-9, fj))
    i, j = int(fi), int(fj)
    u, v = fi - i, fj - j
    a = GRID_ELEV[idx(i, j)]
    b = GRID_ELEV[idx(i + 1, j)]
    c = GRID_ELEV[idx(i + 1, j + 1)]
    d = GRID_ELEV[idx(i, j + 1)]
    return a + (b - a) * u + (c - b) * v if u >= v else a + (c - d) * u + (d - a) * v


# 强校验：顶点索引必须与其 x/z 网格位置、y 高程三者同时对应
err = 0.0
for i in range(NX + 1):
    for j in range(NY + 1):
        k = idx(i, j)
        v = mesh["verts"][k]
        err = max(err, abs(v[0] - (i / NX - .5) * W),
                  abs(v[2] - (.5 - j / NY) * H),
                  abs(v[1] - (GRID_ELEV[k] - EMIN)))
assert err < 0.1, "grid index mismatch: %.3f" % err

kml = open(KML, encoding="utf-8-sig").read()
title = re.search(r"<Document[^>]*>\s*<name>(.*?)</name>", kml, re.S).group(1).strip()
pts, whens, line_ids = [], [], []
if "<gx:Track>" in kml:  # 两步路：带时间戳的实测轨迹
    track_xml = re.search(r"<gx:Track>(.*?)</gx:Track>", kml, re.S).group(1)
    cs = re.findall(r"<gx:coord>(.*?)</gx:coord>", track_xml)
    ws = re.findall(r"<when>(.*?)</when>", track_xml)
    pts = [tuple(map(float, c.split())) for c in cs]
    whens = ws if len(ws) == len(pts) else [""] * len(pts)
    line_ids = [0] * len(pts)
    SOURCE = "两步路 KML / WGS84"
else:                     # 奥维互动地图：LineString，OvCoordType=CGCS2000
    blocks = re.findall(r"<LineString>\s*<coordinates>(.*?)</coordinates>", kml, re.S)
    for lid, blk in enumerate(blocks):
        for c in blk.split():
            lon, lat, e = map(float, c.split(",")[:3])
            pts.append((lon, lat, e))
            whens.append("")
            line_ids.append(lid)
    SOURCE = "奥维互动地图 KML / CGCS2000"
assert pts and len(whens) == len(pts) == len(line_ids)

M_LON = W / (box[2] - box[0])
M_LAT = H / (box[3] - box[1])


def dist(a, b):
    return math.hypot((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT)


def inside(p):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


# 坐标系判定结论（KML 高程 vs Terrarium DEM）：原样相关 0.9344，纠偏后 -0.7282
BIAS = sum(p[2] - dem_at(*to_m(p[0], p[1])) for p in pts) / len(pts)  # KML 高程相对 DEM 的系统偏差

gps = [p[2] for p in pts]
length_all = sum(dist(a, b) for a, b in zip(pts, pts[1:]))
ascent = sum(max(0.0, b[2] - a[2]) for a, b in zip(pts, pts[1:]))

flags = [inside(p) for p in pts]
segs, cur = [], []
for i, f in enumerate(flags):
    if f and cur and line_ids[i] == line_ids[cur[-1]]:
        cur.append(i)
    else:
        if len(cur) > 1:
            segs.append((cur[0], cur[-1]))
        cur = [i] if f else []
if len(cur) > 1:
    segs.append((cur[0], cur[-1]))

length_in = sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
                if flags[i] and flags[i + 1] and line_ids[i] == line_ids[i + 1])


def point(i):
    lon, lat, g = pts[i]
    x, z = to_m(lon, lat)
    d = dem_at(x, z)
    return [round(x, 2), round(d - EMIN + LIFT, 2),
            round((g - BIAS) - EMIN + LIFT, 2), round(z, 2),
            round(lon, 7), round(lat, 7), round(g, 2), round(d, 2), whens[i]]


segments = [[point(i) for i in range(a, b + 1)] for a, b in segs]

# 航点 / 标注点（含起终点，仅保留落在建模区内的）
markers = []
for block in re.findall(r"<Placemark id=.*?</Placemark>", kml, re.S):
    mid = re.search(r'<Placemark id="([^"]+)"', block)
    mname = re.search(r"<name>(.*?)</name>", block, re.S)
    mc = re.search(r"<coordinates>(.*?)</coordinates>", block, re.S)
    if not (mid and mname and mc):
        continue
    pid, name = mid.group(1), htmlmod.unescape(mname.group(1)).strip()
    lon, lat, g = map(float, mc.group(1).strip().split(",")[:3])
    if not inside((lon, lat)):
        continue
    x, z = to_m(lon, lat)
    d = dem_at(x, z)
    markers.append({"id": pid, "name": name, "x": round(x, 2), "z": round(z, 2),
                    "y_dem": round(d - EMIN + LIFT, 2),
                    "y_gps": round((g - BIAS) - EMIN + LIFT, 2),
                    "lon": round(lon, 7), "lat": round(lat, 7),
                    "gps_elev": round(g, 2), "dem_elev": round(d, 2)})

# 首末端点：整条轨迹都在模型内时标「起点/终点」，否则标「进入/离开建模区」
full = all(flags)
i0, i1 = segs[0][0], segs[-1][1]
if full and dist(pts[i0], pts[i1]) < 30:      # 环线：起终点重合，只标一次
    specs = [("起 / 终点", i0)]
else:
    specs = [("起点" if full else "进入建模区", i0),
             ("终点" if full else "离开建模区", i1)]
endpoints = []
for lab, i in specs:
    p = point(i)
    text = lab + (" " + whens[i][11:16] + "Z" if whens[i] else "")
    endpoints.append({"label": text, "x": p[0], "z": p[3],
                      "y_dem": p[1], "y_gps": p[2], "time": whens[i],
                      "lon": p[4], "lat": p[5]})

# 高程剖面 SVG：整条轨迹灰色，落在模型内的部分橙色
PW, PH, PAD = 264, 74, 6
lo, hi = min(gps), max(gps)
def px(i):
    return PAD + i / (len(pts) - 1) * (PW - 2 * PAD)
def py(e):
    return PH - PAD - (e - lo) / max(1e-6, hi - lo) * (PH - 2 * PAD)
all_pts = " ".join("%.1f,%.1f" % (px(i), py(e)) for i, e in enumerate(gps))
in_pts = " ".join("%.1f,%.1f" % (px(i), py(gps[i]))
                  for a, b in segs for i in range(a, b + 1))
marks = "".join('<circle cx="%.1f" cy="%.1f" r="2.6" fill="#ff4d1a"/>'
                % (px(a), py(gps[a])) for a, b in segs)
profile = (
    '<svg width="%d" height="%d" viewBox="0 0 %d %d" '
    'style="display:block;margin-top:8px;border-top:1px solid #e9edf3;padding-top:6px">'
    '<polyline points="%s" fill="none" stroke="#c3ccd8" stroke-width="1.4"/>'
    '<polyline points="%s" fill="none" stroke="#ff4d1a" stroke-width="2"/>'
    '%s'
    '<text x="%d" y="%d" font-size="9" fill="#8b96a6">0 km</text>'
    '<text x="%d" y="%d" font-size="9" fill="#8b96a6" text-anchor="end">%.2f km</text>'
    '<text x="%d" y="11" font-size="9" fill="#8b96a6">%.0f–%.0f m</text>'
    '</svg>'
) % (PW, PH, PW, PH, all_pts, in_pts, marks,
     PAD, PH - 1, PW - PAD, PH - 1, length_all / 1000,
     PAD, lo, hi)

meta = {
    "title": title, "source": SOURCE,
    "date": whens[0][:10] if whens[0] else "无时间戳",
    "total": len(pts), "inside": sum(flags), "segments": len(segs),
    "seg_ranges": [list(s) for s in segs],
    "length_km": round(length_all / 1000, 2), "inside_km": round(length_in / 1000, 2),
    "ascent_m": round(ascent), "gps_min": round(lo, 1), "gps_max": round(hi, 1),
    "coord": COORD_NOTE, "corr": CORR_OK, "rmse": RMSE_OK,
    "corr_gcj": CORR_BAD, "rmse_gcj": RMSE_BAD, "bias": round(BIAS, 2),
    "time_start": whens[0], "time_end": whens[-1],
    "dem": M["dem"], "imagery": M["imagery"], "bbox": box,
}
data = {"meta": meta, "segments": segments, "markers": markers,
        "endpoints": endpoints, "profile": profile, "lift": LIFT,
        "tube_r": TUBE_R, "style": TRACK_STYLE, "wp": WAYPOINT_DEF,
        "shots": DEFAULT_SHOTS, "duration": DEFAULT_DURATION,
        "aspect": DEFAULT_ASPECT}
open(os.path.join(HERE, "track.json"), "w", encoding="utf-8").write(
    json.dumps(data, ensure_ascii=False, separators=(",", ":")))

# 落在范围内的轨迹导出为 WGS84 GeoJSON，便于在其它 GIS 里核对落点
features = []
for si, (a, b) in enumerate(segs):
    features.append({"type": "Feature", "properties": {"name": title, "seq": [a, b],
                     "points": b - a + 1, "source": SOURCE},
                     "geometry": {"type": "LineString",
                                  "coordinates": [[p[4], p[5], p[6]] for p in segments[si]]}})
for m in markers:
    features.append({"type": "Feature",
                     "properties": {"name": m["name"] or "标注点", "gps_elev": m["gps_elev"],
                                    "dem_elev": m["dem_elev"]},
                     "geometry": {"type": "Point", "coordinates": [m["lon"], m["lat"], m["gps_elev"]]}})
open(os.path.join(HERE, "track_in_model.geojson"), "w", encoding="utf-8").write(
    json.dumps({"type": "FeatureCollection", "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
                "features": features}, ensure_ascii=False, indent=1))

# ---------- 注入页面 ----------
tpl = open(os.path.join(HERE, "index.template.html"), encoding="utf-8").read()
page = tpl.replace("__DATA__", json.dumps(mesh, separators=(",", ":")))

# ---------- 镜头控制：独立 CSS / HTML / JS 片段，注入到既有页面 ----------
CAM_CSS = """
/* 镜头控制调参台（挤进右侧面板，可折叠） */
.panel{z-index:9}          /* 高于右下罗盘，展开时新控件不被盖住 */
#compass{z-index:7}
.camTitle{margin:10px 0 2px;padding-top:8px;border-top:1px solid #e9edf3;
font-size:12px;font-weight:500;color:#243042;display:flex;
justify-content:space-between;align-items:center}
.camTitle button{flex:0 0 auto;font-size:10px;padding:2px 6px}
.camHint{font-size:9px;color:#9aa5b4;line-height:1.35;margin-bottom:3px}
.camPresets,.camBtns{display:flex;gap:4px;margin:5px 0;align-items:center}
.camPresets button,.camBtns button{font-size:10px;padding:4px 2px;white-space:nowrap}
.camBtns input[type=number]{width:44px;font:10px system-ui;
border:1px solid #d9e1ec;border-radius:4px;padding:2px 3px}
.camGrid{display:grid;grid-template-columns:30px 1fr 44px;gap:2px 5px;
align-items:center;margin:4px 0}
.camGrid label{font-size:10px;color:#5b6779;white-space:nowrap}
.camGrid .val{font-size:10px;min-width:44px;text-align:right}
.camGrid input[type=range]{width:100%;margin:0;height:14px;accent-color:#2681ff}
.camTl{width:100%;margin:3px 0;height:14px;accent-color:#2681ff}
/* 预览轴播放：常显播放条 + 折叠区内的紧凑播放头 */
.camPlayBar{display:flex;gap:5px;align-items:center;margin:5px 0 2px;
white-space:nowrap}
.camPlayBar>button{font-size:10px;padding:4px 7px;white-space:nowrap;
min-width:76px;flex:0 0 auto}
.camPlayBar>button.on{background:#185fa5;color:#fff;border-color:#185fa5}
.camPlayBar label.chk{font-size:10px;white-space:nowrap;margin:0;gap:3px}
.camTlRow{display:flex;gap:5px;align-items:center;margin:3px 0}
.camTlRow>button{font-size:11px;padding:3px 7px;min-width:30px;
white-space:nowrap;line-height:1.2}
.camTlRow>button.on{background:#185fa5;color:#fff;border-color:#185fa5}
.camTlRow .camTl{flex:1;min-width:0;width:auto;margin:0}
.camList{max-height:92px;overflow-y:auto;border:1px solid #e9edf3;
border-radius:5px;background:#fbfcfe;margin:3px 0}
.camRow{display:grid;grid-template-columns:44px 1fr 18px 18px;gap:3px;
align-items:center;padding:2px 4px;border-bottom:1px solid #eff2f7;
font-size:10px;color:#5b6779;white-space:nowrap}
.camGo{cursor:pointer;text-underline-offset:2px;
text-decoration:underline dotted #b7c2d2}
.camGo:hover{color:#185fa5}
.camOvr{border:0;background:none;color:#2681ff;cursor:pointer;
font-size:12px;padding:0;line-height:1;flex:none}
.camOvr:hover{color:#185fa5}
/* 记录当前画面：主按钮 + 记录时刻；总时长单独一行，避免被误认成记录时刻 */
.camRecBar{gap:4px}
.camRecBar>button{flex:1;min-width:0;font-size:10px;padding:5px 4px;white-space:nowrap}
.camRecAt{display:flex;gap:2px;align-items:center;flex:0 0 auto;
font-size:10px;color:#5b6779;white-space:nowrap}
.camRecAt input{width:48px;font:10px system-ui;
border:1px solid #d9e1ec;border-radius:4px;padding:2px 2px;text-align:right}
.camDurBar{display:flex;gap:4px;align-items:center;margin:4px 0;
font-size:10px;color:#5b6779}
.camDurBar input{width:44px;font:10px system-ui;
border:1px solid #d9e1ec;border-radius:4px;padding:2px 3px}
.camDurBar button{font-size:10px;padding:3px 5px;white-space:nowrap;
margin-left:auto}
.camRow:last-child{border-bottom:0}
.camRow input{width:100%;font:10px system-ui;border:1px solid transparent;
background:transparent;padding:1px 2px;border-radius:3px}
.camRow input:focus{border-color:#2681ff;background:#fff;outline:none}
.camRow .camDel{border:0;background:none;color:#a5afbd;cursor:pointer;
font-size:14px;padding:0;line-height:1;flex:none}
.camRow .camDel:hover{color:#d33}
.camAsp{display:flex;gap:4px;margin-top:5px;align-items:center;
font-size:10px;color:#5b6779}
.camAsp button{font-size:10px;padding:3px 2px}
.camAsp button.on{background:#185fa5;color:#fff;border-color:#185fa5}
#aspBox{position:absolute;inset:0;pointer-events:none;z-index:6;display:none}
#aspFrame{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
border:1px dashed rgba(255,255,255,.92);
box-shadow:0 0 0 9999px rgba(18,28,42,.40)}
#camBody.hide{display:none}
@media (max-height:780px){.camTitle{margin:6px 0 1px;padding-top:5px}
.camList{max-height:60px}.camGrid{gap:1px 5px}}
"""

CSS = """
.trkTitle{margin:11px 0 5px;padding-top:9px;border-top:1px solid #e9edf3;
font-size:13px;font-weight:500;color:#243042}
.trkSub{font-size:11px;color:#8b96a6;margin-bottom:4px}
.trkDot{display:inline-block;width:9px;height:9px;border-radius:50%;
background:var(--trk,#ff4d1a);margin-right:5px}
.lbl{position:absolute;transform:translate(-50%,-100%);pointer-events:none;white-space:nowrap;
background:rgba(255,255,255,.92);border:1px solid var(--trk,#ff8a63);color:#9d3412;font-size:11px;
padding:2px 6px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.12)}
.lbl.end{border-color:#185fa5;color:#185fa5}
/* 轨迹样式调参台 */
.panel{max-height:calc(100vh - 32px);overflow-y:auto}
.trkStyle{margin:9px 0 2px;padding-top:8px;border-top:1px solid #e9edf3;
font-size:12px;font-weight:500;color:#243042}
.trkHint{font-size:10px;color:#9aa5b4;line-height:1.3}
.trkGrid{display:grid;grid-template-columns:34px 1fr 42px;gap:3px 6px;
align-items:center;margin:6px 0 4px}
.trkGrid label{font-size:10px;color:#5b6779;white-space:nowrap}
.trkGrid .val{font-size:10px;min-width:42px;text-align:right}
.trkGrid input[type=range]{width:100%;margin:0;accent-color:var(--trk,#ff4d1a)}
.trkGrid input[type=color]{width:34px;height:21px;padding:0;border:1px solid #d9e1ec;
border-radius:4px;background:none;cursor:pointer}
.trkGrid .cline{display:flex;align-items:center;gap:5px;min-width:0}
.trkGrid .btns{grid-column:1/-1;margin-top:3px;display:flex;gap:4px;
align-items:center;flex-wrap:nowrap}
.trkGrid .btns .chk{font-size:10px;gap:2px;white-space:nowrap;flex:0 0 auto}
.trkGrid .btns .chk input{margin:0}
.trkGrid .btns button{flex:1;min-width:0;white-space:nowrap;font-size:10px;padding:4px 2px}
.swatches{display:flex;gap:4px;margin:0}
.swatches i{display:block;width:16px;height:16px;border-radius:3px;
border:1px solid rgba(0,0,0,.14);cursor:pointer}
.swatches i:hover{outline:2px solid #2681ff;outline-offset:1px}
#trkParam{display:none;width:100%;font:10px/1.5 ui-monospace,Consolas,monospace;
color:#5b6779;background:#f7f9fc;border:1px solid #e2e7ef;border-radius:4px;
padding:4px 5px;margin-top:5px}
/* 途径点编辑台 */
.wpTitle{margin:10px 0 2px;padding-top:8px;border-top:1px solid #e9edf3;
font-size:12px;font-weight:500;color:#243042}
.wpHint{font-size:9px;color:#9aa5b4;line-height:1.35;margin-bottom:4px}
.wpBar{display:flex;gap:4px;align-items:center;margin:5px 0}
.wpBar button{flex:1;font-size:11px;padding:4px 2px}
.wpBar input[type=number]{width:52px;font:11px system-ui;border:1px solid #d9e1ec;
border-radius:4px;padding:3px 4px}
.wpBar span{font-size:10px;color:#8b96a6}
#wpPick.active{background:#ff4d1a;color:#fff;border-color:#ff4d1a}
#wpPick.active:hover{background:#e6440f}
.wpList{max-height:126px;overflow-y:auto;border:1px solid #e9edf3;
border-radius:5px;background:#fbfcfe;margin:4px 0}
.wpRow{display:grid;grid-template-columns:15px 1fr 26px 17px;gap:2px 3px;
align-items:center;padding:3px 5px;border-bottom:1px solid #eff2f7}
.wpRow:last-child{border-bottom:0}
.wpNo{font-size:10px;color:#8b96a6;text-align:center}
.wpName{width:100%;min-width:0;font:11px system-ui,"Microsoft YaHei",sans-serif;
border:1px solid transparent;background:transparent;padding:1px 2px;border-radius:3px}
.wpName:focus{border-color:#2681ff;background:#fff;outline:none}
.wpColor{width:24px;height:19px;padding:0;border:1px solid #d9e1ec;
border-radius:3px;background:none;cursor:pointer}
.wpDel{border:0;background:none;color:#a5afbd;cursor:pointer;
font-size:15px;padding:0;line-height:1}
.wpDel:hover{color:#d33}
.wpMeta{grid-column:2/-1;font-size:9px;color:#9aa5b4;line-height:1.2}
.wpEmpty{font-size:10px;color:#9aa5b4;padding:10px;text-align:center}
.wpFoot{display:flex;align-items:center;gap:6px;font-size:10px;color:#5b6779;margin-top:2px}
.wpFoot .chk{font-size:10px;gap:3px}
.wpFoot input[type=range]{flex:1;min-width:0;accent-color:#2681ff}
#wpSizeVal,#wpFsVal{font-variant-numeric:tabular-nums;min-width:32px;text-align:right}
.wpBtns{display:flex;gap:5px;margin-top:5px}
.wpBtns button{font-size:10px;padding:4px 3px;white-space:nowrap}
.lbl.wp{font-weight:500;background:rgba(255,255,255,.95);border-width:1.5px;
box-shadow:0 2px 7px rgba(0,0,0,.18);font-size:12px}
/* 矮窗口：收紧行距，保证新增的「离地 / 自动 / 落影」仍在首屏 */
@media (max-height:780px){
.trkTitle{margin:7px 0 3px;padding-top:6px}
.trkStyle{margin:6px 0 1px;padding-top:5px}
.trkGrid{gap:2px 5px;margin:4px 0 2px}
.trkGrid input[type=range]{height:14px}
.trkGrid .btns{margin-top:2px}
.wpTitle{margin:7px 0 1px;padding-top:5px}
.wpList{max-height:84px}
#profile{display:none}   /* 矮窗口让出剖面图的高度，保证调参控件首屏可见 */
}
"""

TRACK_HTML = """
<div class="trkTitle"><span class="trkDot"></span>徒步轨迹 · 真实坐标落点</div>
<div class="trkSub" id="trkTitle"></div>
<div id="trkInfo"></div>
__PROFILE__
"""

CTRL_HTML = """
<div class="sep"></div>
<label class="chk"><input type="checkbox" id="showTrk" checked>显示徒步轨迹</label>
<label class="chk"><input type="checkbox" id="gpsMode">用 KML 记录高程</label>
<label class="chk"><input type="checkbox" id="showMk" checked>标注点 / 起终点</label>
<label class="chk" title="低角度侧视时开启会把轨迹强行画在山体前面，看起来像腾空；建议保持关闭"><input type="checkbox" id="xray">轨迹穿透显示（看全貌）</label>
<div class="sep"></div>
<div class="wpTitle">途径点（<span id="wpCount">0 个</span>）</div>
<div class="wpHint" id="wpHint">点「拾取」后在橙色轨迹上点击，或按里程添加</div>
<div class="wpBar">
  <button id="wpPick">拾取</button>
  <input type="number" id="wpKm" min="0" step="0.05" value="1.00" title="里程 km">
  <span>km</span><button id="wpAddKm">按里程</button>
</div>
<div class="wpList" id="wpList"></div>
<div class="wpFoot">
  <label class="chk"><input type="checkbox" id="wpLbl" checked>名称标签</label>
  <input type="range" id="wpSize" min="4" max="30" step="1" value="12">
  <span id="wpSizeVal">12 m</span>
</div>
<div class="wpFoot">
  <span>标签字号</span>
  <input type="range" id="wpFs" min="9" max="26" step="1" value="12">
  <span id="wpFsVal">12 px</span>
</div>
<div class="wpBtns"><button id="wpEven" title="按 1/4、1/2、3/4 里程各加一个点">等分 3 点</button>
  <button id="wpGeo" title="导出含经纬度/里程/高程的 GeoJSON">导出 JSON</button>
  <button id="wpClear">清空</button></div>
"""

CAM_HTML = """
<div class="camTitle">镜头控制 <button id="camToggle">展开</button></div>
<div class="camPresets">
  <button id="camHome" title="恢复默认等距沙盘全景">等距全景</button>
  <button id="camLow" title="低角度侧视，用于检查轨迹贴地与遮挡">低角侧视</button>
  <button id="camOrbit" title="生成绕行一周并逐步推近的分镜">环绕推近</button>
</div>
<div class="camPlayBar">
  <button id="camPlayBtn" title="按分镜时间轴 1× 实时播放镜头预览（只在页面预览，不出帧）">▶ 播放预览</button>
  <label class="chk" title="播到结尾自动回到开头继续播"><input type="checkbox" id="camLoop">循环</label>
</div>
<div class="camAsp"><span>画幅</span>
  <button data-a="0" class="on">关</button><button data-a="916">9:16</button>
  <button data-a="169">16:9</button></div>
<div id="camBody" class="hide">
<div class="camHint">「记到」填秒数（超出总时长自动延长）→ 调好画面 → 记录当前画面；播放只预览、不出帧</div>
<div class="camGrid">
  <label>方位</label><input type="range" id="camAz" min="0" max="360" step="1"><span class="val" id="camAzV"></span>
  <label>俯角</label><input type="range" id="camEl" min="0" max="89" step="0.5"><span class="val" id="camElV"></span>
  <label>距离</label><input type="range" id="camR" min="300" max="4000" step="10"><span class="val" id="camRV"></span>
  <label>视点X</label><input type="range" id="camTx" min="-400" max="400" step="5"><span class="val" id="camTxV"></span>
  <label>视点Y</label><input type="range" id="camTy" min="0" max="400" step="5"><span class="val" id="camTyV"></span>
  <label>视点Z</label><input type="range" id="camTz" min="-400" max="400" step="5"><span class="val" id="camTzV"></span>
  <label>视场</label><input type="range" id="camFov" min="30" max="80" step="1"><span class="val" id="camFovV"></span>
</div>
<div class="camBtns camRecBar">
  <button id="camAdd" title="把屏幕上当前看到的机位记到左边指定的时刻，并自动把数值读回滑块">＋ 记录当前画面</button>
  <span class="camRecAt">记到
    <input type="number" id="camRecT" min="0" max="60" step="0.05" value="0" title="记录到这一秒；可以填超过总时长的时刻，会自动延长总时长">
    <span>s</span></span>
</div>
<div class="camDurBar">
  <span>总时长</span>
  <input type="number" id="camDur" min="3" max="60" step="0.5" value="10" title="分镜总时长（秒）；缩短会等比压缩已有关键帧，延长则保留原节奏、末尾留空档">
  <span>s</span>
  <button id="camRead" title="只把当前机位读回上方滑块，不记录分镜">读回滑块</button>
</div>
<div class="camTlRow">
  <button id="camPlayBtn2" title="播放 / 暂停分镜预览">▶</button>
  <input type="range" class="camTl" id="camPlay" min="0" max="10" step="0.01" value="0">
</div>
<div class="camHint" id="camPlayV">播放头 0.00 s</div>
<div class="camList" id="camList"></div>
<div class="camBtns"><button id="camClearShots">清空分镜</button>
  <button id="camRestore" title="恢复内置 5 帧默认分镜">默认分镜</button></div>
</div>
"""

STYLE_HTML = """
<div class="trkStyle">轨迹样式（可调 · 自动记住）</div>
<div class="trkHint">自动按坡度取最小离地 · 取消「自动」可手调 · 穿透显示建议关闭</div>
<div class="trkGrid">
  <label>线宽</label><input type="range" id="trkW" min="0.5" max="20" step="0.1" value="4.2">
  <span class="val" id="trkWVal">4.2 m</span>
  <label>颜色</label>
  <div class="cline"><input type="color" id="trkC" value="#ff4d1a">
    <div class="swatches" id="trkSw">
      <i data-c="ff4d1a" style="background:#ff4d1a" title="橙"></i>
      <i data-c="ffd166" style="background:#ffd166" title="黄"></i>
      <i data-c="ff2d55" style="background:#ff2d55" title="红"></i>
      <i data-c="00e5ff" style="background:#00e5ff" title="青"></i>
      <i data-c="4dff88" style="background:#4dff88" title="绿"></i>
      <i data-c="ffffff" style="background:#ffffff" title="白"></i>
    </div>
  </div><span class="val"></span>
  <label>描边</label><input type="range" id="trkHl" min="0" max="3" step="0.1" value="1.8">
  <span class="val" id="trkHlVal">1.8×</span>
  <label>边色</label><input type="color" id="trkHc" value="#ffffff">
  <span class="val"></span>
  <label>不透明</label><input type="range" id="trkOp" min="0.2" max="1" step="0.05" value="1">
  <span class="val" id="trkOpVal">100%</span>
  <label>离地</label><input type="range" id="trkLift" min="0.5" max="15" step="0.5" value="7">
  <span class="val" id="trkLiftVal">7.0 m</span>
  <div class="btns">
    <label class="chk" title="缓坡压低贴地、陡坡略抬升，自动平衡贴地感与可见度"><input type="checkbox" id="trkAuto" checked>自动</label>
    <label class="chk" title="在地表画半透明贴地影带，给轨迹一个落地参照"><input type="checkbox" id="trkShadow" checked>落影</label>
    <button id="trkReset">默认</button><button id="trkCopy">复制参数</button>
  </div>
</div>
<input id="trkParam" readonly placeholder="剪辑参数">
"""

# 镜头核心：页面与 video.html 共用同一份 applyCam / shotAt，保证构图完全一致
CAM_CORE = r"""
/* ---------- 镜头：统一状态 + 共用函数（页面与 video.html 同一份实现） ---------- */
const CKEY='terrain_hd_camera_v1', TOP=location.search.indexOf('top')>=0;
const CUNIT={az:'°',el:'°',r:'m',tx:'m',ty:'m',tz:'m',fov:'°'};
const CAMFIELDS=['az','el','r','tx','ty','tz','fov'];
function camSmooth(x){return x*x*(3-2*x);}
function camLerp(a,b,f){return a+(b-a)*f;}
function shotAt(shots,t){
  if(!shots||!shots.length)return null;
  let k=0;
  while(k<shots.length-2 && t>shots[k+1].t)k++;
  const A=shots[k],B=shots[Math.min(k+1,shots.length-1)];
  const f=camSmooth(Math.max(0,Math.min(1,(t-A.t)/Math.max(1e-6,B.t-A.t))));
  const o={};
  for(const key of CAMFIELDS)o[key]=camLerp(A[key],B[key],f);
  return o;
}
function applyCam(c){
  const a=c.az*Math.PI/180,e=c.el*Math.PI/180,r=c.r;
  const t=new THREE.Vector3(c.tx,c.ty,c.tz);
  camera.up.set(0,1,0);
  camera.position.set(t.x+r*Math.cos(e)*Math.sin(a),
                      t.y+r*Math.sin(e),
                      t.z+r*Math.cos(e)*Math.cos(a));
  if(Math.abs(camera.fov-c.fov)>1e-3){camera.fov=c.fov;camera.updateProjectionMatrix();}
  controls.target.copy(t);
  camera.lookAt(t);
  const damp=controls.enableDamping;
  controls.enableDamping=false;controls.update();controls.enableDamping=damp;
  camera.updateMatrixWorld();
}
function readCam(){
  const d=new THREE.Vector3().subVectors(camera.position,controls.target);
  const r=d.length()||1;
  return {az:+((Math.atan2(d.x,d.z)*180/Math.PI+360)%360).toFixed(1),
          el:+(Math.asin(Math.max(-1,Math.min(1,d.y/r)))*180/Math.PI).toFixed(1),
          r:+r.toFixed(1), tx:+controls.target.x.toFixed(1),
          ty:+controls.target.y.toFixed(1), tz:+controls.target.z.toFixed(1),
          fov:+camera.fov.toFixed(1)};
}
function homeCam(){
  const t=new THREE.Vector3(0,(MAX-MIN)*0.55,0);
  const d=new THREE.Vector3().subVectors(HOME,t), r=d.length();
  return {az:+((Math.atan2(d.x,d.z)*180/Math.PI+360)%360).toFixed(1),
          el:+(Math.asin(Math.max(-1,Math.min(1,d.y/r)))*180/Math.PI).toFixed(1),
          r:+r.toFixed(1),tx:0,ty:+t.y.toFixed(1),tz:0,fov:44};
}
/* 紧凑编码：字段用 _ 、帧用 ~ ，全是数字，URL 安全无需转义 */
function packCam(c){return CAMFIELDS.map(k=>(+c[k]).toFixed(1)).join('_');}
function unpackCam(s){
  const a=String(s||'').split('_').map(Number);
  if(a.length!==7||a.some(v=>!isFinite(v)))return null;
  const o={};CAMFIELDS.forEach((k,i)=>o[k]=a[i]);return o;
}
function packShots(list){
  return list.map(s=>[(+s.t).toFixed(2)]
    .concat(CAMFIELDS.map(k=>(+s[k]).toFixed(1))).join('_')).join('~');
}
function unpackShots(s){
  const out=[];
  for(const part of String(s||'').split('~')){
    if(!part)continue;
    const a=part.split('_').map(Number);
    if(a.length!==8||a.some(v=>!isFinite(v)))continue;
    const o={t:a[0]};CAMFIELDS.forEach((k,i)=>o[k]=a[i+1]);out.push(o);
  }
  out.sort((x,y)=>x.t-y.t);
  return out;
}
let CAM=homeCam(), SHOTS=TK.shots.map(o=>Object.assign({},o));
let DUR=TK.duration, ASP=TK.aspect, PLAY=0, COLLAPSED=true;
try{
  const sv=JSON.parse(localStorage.getItem(CKEY)||'null');
  if(sv){
    if(sv.cam&&CAMFIELDS.every(k=>isFinite(+sv.cam[k])))CAM=sv.cam;
    if(Array.isArray(sv.shots))SHOTS=sv.shots.filter(
      o=>o&&isFinite(+o.t)&&CAMFIELDS.every(k=>isFinite(+o[k])));
    if(isFinite(+sv.dur))DUR=Math.min(60,Math.max(3,+sv.dur));
    if(['0','916','169'].indexOf(sv.asp)>=0)ASP=sv.asp;
    if(typeof sv.collapsed==='boolean')COLLAPSED=sv.collapsed;
  }
}catch(e){}
if(QP.has('cam')){const c=unpackCam(QP.get('cam'));if(c)CAM=c;}
if(QP.has('shots'))SHOTS=unpackShots(QP.get('shots'));
if(QP.has('dur')){const n=parseFloat(QP.get('dur'));
  if(isFinite(n))DUR=Math.min(60,Math.max(3,n));}
if(QP.has('asp')&&['0','916','169'].indexOf(QP.get('asp'))>=0)ASP=QP.get('asp');
"""

CAM_UI = r"""
/* ---------- 镜头控制 UI（右侧面板） ---------- */
const _aspBox=document.createElement('div');
_aspBox.id='aspBox';_aspBox.innerHTML='<div id="aspFrame"></div>';
document.body.appendChild(_aspBox);
function saveCam(){
  try{localStorage.setItem(CKEY,JSON.stringify(
    {cam:CAM,shots:SHOTS,dur:DUR,asp:ASP,
     collapsed:el('camBody').classList.contains('hide')}));}catch(e){}
}
function refreshParam(){el('trkParam').value=styleParams();}
const CAMROWS=[['camAz','az'],['camEl','el'],['camR','r'],['camTx','tx'],
               ['camTy','ty'],['camTz','tz'],['camFov','fov']];
function camValues(){
  for(const [id,k] of CAMROWS)el(id+'V').textContent=(+CAM[k]).toFixed(0)+CUNIT[k];
}
function applyAspect(){
  const box=el('aspBox');
  if(ASP==='0'){box.style.display='none';return;}
  box.style.display='block';
  const aw=ASP==='916'?9:16, ah=ASP==='916'?16:9;
  const mw=innerWidth-32,mh=innerHeight-32;
  let w=Math.min(mw,mh*aw/ah), h=w*ah/aw;
  if(h>mh){h=mh;w=h*aw/ah;}
  el('aspFrame').style.width=Math.round(w)+'px';
  el('aspFrame').style.height=Math.round(h)+'px';
  document.querySelectorAll('.camAsp button').forEach(b=>
    b.classList.toggle('on',b.dataset.a===ASP));
}
function previewAt(t,keepCam){
  const s=shotAt(SHOTS,t);
  el('camPlayV').textContent = s
    ? '播放头 '+(+t).toFixed(2)+' s · az '+s.az.toFixed(0)+'° el '+s.el.toFixed(0)+'° r '+s.r.toFixed(0)+' m'
    : '播放头 '+(+t).toFixed(2)+' s · 无关键帧';
  if(s&&!TOP&&!keepCam)applyCam(s);   /* keepCam：只刷文字，不抢用户刚拖好的视角 */
}
/* ---------- 预览轴播放：只推进页面镜头，不产生任何帧/视频 ---------- */
let PLAYING=false, PLAY_T0=0, PLAY_BASE=0, PLAY_RAF=0;
let REC_FLASH=0, REC_FLASH_TXT='✓ 已记录';
function recUi(){
  const e=el('camRecT');
  if(document.activeElement!==e)e.value=(+PLAY).toFixed(2);  /* 默认跟随播放头，手填时不覆盖 */
  if(performance.now()<REC_FLASH)el('camPlayV').textContent=REC_FLASH_TXT;
}
function flashRec(txt){
  REC_FLASH_TXT=txt;REC_FLASH=performance.now()+1600;
  recUi();
  /* 反馈结束只恢复提示文字，不调用 applyCam，避免抢走用户这期间调好的视角 */
  setTimeout(()=>{if(performance.now()>=REC_FLASH){REC_FLASH=0;previewAt(PLAY,true);}},1750);
}
function clearFlash(){
  if(REC_FLASH){REC_FLASH=0;previewAt(PLAY,true);}
}
function playUi(){
  const b=el('camPlayBtn'), b2=el('camPlayBtn2');
  b.textContent=PLAYING
    ? '⏸ '+(+PLAY).toFixed(1)+' / '+(+DUR).toFixed(1)+' s'
    : '▶ 播放预览';
  b2.textContent=PLAYING?'⏸':'▶';
  b.classList.toggle('on',PLAYING);b2.classList.toggle('on',PLAYING);
  recUi();
}
function stopPlay(){
  if(PLAY_RAF)cancelAnimationFrame(PLAY_RAF);
  PLAY_RAF=0;PLAYING=false;PLAY_T0=0;clearFlash();playUi();
}
function tickPlay(ts){
  if(!PLAYING)return;
  if(!PLAY_T0)PLAY_T0=ts;
  let t=PLAY_BASE+(ts-PLAY_T0)/1000;
  if(t>=DUR){
    if(el('camLoop').checked){PLAY_BASE=0;PLAY_T0=ts;t=0;}
    else{PLAY=DUR;el('camPlay').value=PLAY;previewAt(PLAY);stopPlay();return;}
  }
  PLAY=t;el('camPlay').value=PLAY;previewAt(PLAY);playUi();
  PLAY_RAF=requestAnimationFrame(tickPlay);
}
function togglePlay(){
  if(PLAYING){stopPlay();return;}
  if(window.__frameMode)return;              /* video.html 逐帧模式不启用播放 */
  if(TOP){el('camPlayV').textContent='正俯视模式：播放预览暂不生效';return;}
  if(SHOTS.length<2){el('camPlayV').textContent='暂无分镜：先点「环绕推近」或「默认分镜」';return;}
  if(el('autoRot')&&el('autoRot').checked){el('autoRot').checked=false;controls.autoRotate=false;}
  if(PLAY>=DUR-0.01)PLAY=0;                /* 播完再点，从头重播 */
  clearFlash();
  PLAY_BASE=PLAY;PLAY_T0=0;PLAYING=true;playUi();
  PLAY_RAF=requestAnimationFrame(tickPlay);
}
el('camPlayBtn').onclick=togglePlay;
el('camPlayBtn2').onclick=togglePlay;
function renderShotList(){
  const box=el('camList');
  if(!SHOTS.length){box.innerHTML='<div class="wpEmpty">无关键帧 · 调好画面点「记录当前画面」，或用「环绕推近」</div>';return;}
  box.innerHTML=SHOTS.map((s,i)=>
    '<div class="camRow"><input type="number" class="camT" data-i="'+i+
    '" min="0" max="60" step="0.05" value="'+(+s.t).toFixed(2)+
    '"><span class="camGo" data-i="'+i+'" title="跳到这一帧预览">az '+s.az.toFixed(0)+
    '° el '+s.el.toFixed(0)+'° r '+s.r.toFixed(0)+
    '</span><button class="camOvr" data-i="'+i+'" title="用当前画面覆盖这一帧">↻</button>'+
    '<button class="camDel" data-i="'+i+'" title="删除">×</button></div>').join('');
  box.querySelectorAll('.camT').forEach(inp=>inp.onchange=e=>{
    const i=+e.target.dataset.i,v=parseFloat(e.target.value);
    if(isFinite(v)){
      const nt=Math.max(0,Math.min(60,+v.toFixed(2)));
      SHOTS[i].t=nt;
      if(nt>DUR)DUR=nt;          /* 把某帧改到时长之外时自动延长，不再夹回去 */
    }
    SHOTS.sort((a,b)=>a.t-b.t);syncCamUI();previewAt(PLAY);});
  box.querySelectorAll('.camGo').forEach(n=>n.onclick=e=>{
    const s=SHOTS[+e.target.dataset.i];if(!s)return;
    if(PLAYING)stopPlay();
    clearFlash();
    PLAY=+s.t;el('camPlay').value=PLAY;previewAt(PLAY);playUi();});
  box.querySelectorAll('.camOvr').forEach(b=>b.onclick=e=>{
    const i=+e.target.dataset.i;if(!SHOTS[i])return;
    const c=readCam();CAM=c;SHOTS[i]=Object.assign({t:SHOTS[i].t},c);
    syncCamUI();previewAt(SHOTS[i].t);flashRec('✓ 已覆盖 '+(+SHOTS[i].t).toFixed(2)+' s');});
  box.querySelectorAll('.camDel').forEach(b=>b.onclick=e=>{
    SHOTS.splice(+e.target.dataset.i,1);syncCamUI();previewAt(PLAY);});
}
function syncCamUI(){
  if(PLAYING)stopPlay();                    /* 改分镜/时长时先停下，避免播放头乱跳 */
  for(const [id,k] of CAMROWS)el(id).value=CAM[k];
  camValues();
  el('camDur').value=DUR;
  el('camPlay').max=DUR;el('camPlay').value=PLAY;
  renderShotList();applyAspect();refreshParam();playUi();saveCam();
}
function setCam(c){
  CAM=c;
  if(el('autoRot')&&el('autoRot').checked){el('autoRot').checked=false;controls.autoRotate=false;}
  syncCamUI();
  if(!TOP)applyCam(CAM);
}
for(const [id,k] of CAMROWS)el(id).oninput=e=>{
  CAM[k]=+e.target.value;
  if(el('autoRot')&&el('autoRot').checked){el('autoRot').checked=false;controls.autoRotate=false;}
  el(id+'V').textContent=(+CAM[k]).toFixed(0)+CUNIT[k];
  if(!TOP)applyCam(CAM);
  refreshParam();saveCam();
};
el('camRead').onclick=()=>{clearFlash();CAM=readCam();syncCamUI();};
el('camHome').onclick=()=>setCam(homeCam());
el('camLow').onclick=()=>setCam({az:132,el:8.9,r:1450,tx:0,ty:115,tz:0,fov:44});
el('camOrbit').onclick=()=>{
  const base=homeCam(),arr=[];
  for(let i=0;i<6;i++){
    const f=i/5;
    arr.push({t:+(DUR*f).toFixed(2),az:+(120+300*f).toFixed(1),
      el:+(34+16*f).toFixed(1),r:+(base.r*1.05-base.r*0.42*f).toFixed(1),
      tx:0,ty:130,tz:0,fov:58});
  }
  SHOTS=arr;PLAY=0;syncCamUI();previewAt(0);
};
/* 记录 = 所见即所得：readCam() 读屏幕上真实机位（鼠标拖拽、滑块精调、
   播放头插值都算），并顺手同步回滑块，不用再先点「拾取当前视角」。
   时刻取「记到」输入框：可超过当前总时长，此时自动延长总时长。 */
function recTime(){
  const v=parseFloat(el('camRecT').value);
  return isFinite(v)?Math.max(0,Math.min(60,+v.toFixed(2))):+PLAY.toFixed(2);
}
function recordCurrent(){
  const c=readCam(), t=recTime();
  const ext=t>DUR;
  if(ext)DUR=t;
  const hit=SHOTS.some(o=>Math.abs(o.t-t)<0.005);  /* 只有同一时刻才算覆盖 */
  CAM=c;
  SHOTS=SHOTS.filter(o=>Math.abs(o.t-t)>0.005);
  SHOTS.push(Object.assign({t:t},c));
  SHOTS.sort((a,b)=>a.t-b.t);
  PLAY=t;
  syncCamUI();                 /* 停播放 + 滑块 / 列表 / 总时长 / 参数串同步 */
  previewAt(t);                /* 画面保持在刚记录的机位 */
  flashRec(ext?'✓ 已延长并记录':(hit?'✓ 已覆盖':'✓ 已记录'));
}
el('camAdd').onclick=recordCurrent;
el('camClearShots').onclick=()=>{SHOTS=[];syncCamUI();};
el('camRestore').onclick=()=>{
  SHOTS=TK.shots.map(o=>Object.assign({},o));DUR=TK.duration;PLAY=0;
  syncCamUI();previewAt(0);
};
el('camDur').onchange=e=>{
  const v=parseFloat(e.target.value);
  if(!isFinite(v)){syncCamUI();return;}
  const nd=Math.min(60,Math.max(3,v)), old=DUR, changed=Math.abs(nd-DUR)>1e-6;
  let shrink=false;
  if(changed){
    if(nd<old){
      const k=nd/old;
      SHOTS.forEach(s=>{s.t=+(s.t*k).toFixed(2);});  /* 缩短才压缩，避免超时帧 */
      shrink=true;
    }
    /* 延长时保留原有节奏，末尾留出空档，才能继续往后加镜头 */
    PLAY=Math.min(PLAY,nd);
    DUR=nd;
  }
  syncCamUI();previewAt(PLAY);
  if(changed)flashRec(shrink?'✓ 已缩放（压缩）':'✓ 已延长');
};
el('camPlay').oninput=e=>{
  if(PLAYING)stopPlay();                    /* 手动拖播放头 = 暂停并接管 */
  clearFlash();
  PLAY=+e.target.value;previewAt(PLAY);playUi();
};
document.querySelectorAll('.camAsp button').forEach(b=>b.onclick=()=>{
  ASP=b.dataset.a;applyAspect();refreshParam();saveCam();});
window.addEventListener('resize',applyAspect);
el('camToggle').onclick=()=>{
  const b=el('camBody');b.classList.toggle('hide');
  el('camToggle').textContent=b.classList.contains('hide')?'展开':'收起';
  saveCam();
};
window.__camProbe=function(){
  return {cam:CAM,shots:SHOTS.map(o=>Object.assign({},o)),dur:DUR,asp:ASP,
    play:PLAY,camPos:[camera.position.x,camera.position.y,camera.position.z]
      .map(v=>+v.toFixed(1)),
    target:[controls.target.x,controls.target.y,controls.target.z].map(v=>+v.toFixed(1)),
    fov:+camera.fov.toFixed(1),
    aspVisible:el('aspBox').style.display,
    aspSize:[el('aspFrame').style.width,el('aspFrame').style.height],
    shotAt0:shotAt(SHOTS,0),param:el('trkParam').value,
    playing:PLAYING,loop:el('camLoop').checked,
    playBtn:el('camPlayBtn').textContent,
    recAt:+el('camRecT').value,
    recText:'记到 '+el('camRecT').value+' s | '+el('camPlayV').textContent,
    collapsed:el('camBody').classList.contains('hide')};
};
el('camBody').classList.toggle('hide',COLLAPSED);
el('camToggle').textContent=COLLAPSED?'展开':'收起';
syncCamUI();
if(TOP)el('camPlayV').textContent='正俯视模式：镜头滑块暂不生效';
else if(QP.has('cam'))applyCam(CAM);
"""

TRACK_JS = r"""
/* ---------- 徒步轨迹层（KML 原坐标，落在建模区内的部分） ---------- */
const TK = __TRACK__, TM = TK.meta;
/* 轨迹外观：默认值 → 浏览器记忆 → URL 参数（视频逐帧渲染用同一套参数） */
const SDEF = TK.style, SKEY = 'terrain_hd_track_style_v1';
const QP = new URLSearchParams(location.search);
const QMAP = {trkw:'w',trkc:'c',trkhl:'hl',trkhc:'hc',trkop:'op',trkx:'xr',
              trklift:'lift',trkauto:'auto',trkshadow:'shadow'};
const HEXK = {c:1,hc:1}, BOOLK = {xr:1,auto:1,shadow:1};
const RANGE = {w:[0.5,20],hl:[0,3],op:[0.2,1],lift:[0.5,15]};
__CAM_CORE__
/* ---------- 途径点数据：默认 → 浏览器记忆 → URL 参数（视频逐帧渲染沿用） ---------- */
const WDEF = TK.wp, WKEY = 'terrain_hd_waypoints_v1';
function cleanWP(o){
  if(!o || typeof o!=='object')return null;
  const t=parseFloat(o.t);
  if(!isFinite(t))return null;
  return {t:Math.min(1,Math.max(0,t)),
          name:String(o.name||'途径点').replace(/[~,]/g,' ').slice(0,24),
          color:cleanHex(o.color||'ffd166','ffd166'),
          on:!(o.on===0 || o.on===false || o.on==='0')};
}
/* 紧凑串： t,color,on,urlencoded-name  用 ~ 分隔多个点 */
function wpPack(){
  return WP.list.map(o=>[o.t.toFixed(4),o.color,o.on?'1':'0',
    encodeURIComponent(o.name)].join(',')).join('~');
}
function wpUnpack(s){
  const out=[];
  for(const part of String(s).split('~')){
    if(!part)continue;
    const a=part.split(','); let name='';
    try{name=decodeURIComponent(a[3]||'');}catch(e){name=a[3]||'';}
    const o=cleanWP({t:a[0],color:a[1],on:a[2],name:name||'途径点'});
    if(o)out.push(o);
  }
  return out;
}
function saveWP(){try{localStorage.setItem(WKEY,JSON.stringify(WP));}catch(e){}}
const WP = {size:WDEF.size, fs:WDEF.fs, label:WDEF.label, list:[]};
try{
  const sw=JSON.parse(localStorage.getItem(WKEY)||'null');
  if(sw && Array.isArray(sw.list)){
    WP.list=sw.list.map(cleanWP).filter(Boolean);
    if(isFinite(+sw.size))WP.size=Math.min(30,Math.max(4,+sw.size));
    if(isFinite(+sw.fs))WP.fs=Math.min(26,Math.max(9,+sw.fs));
    if(typeof sw.label==='boolean')WP.label=sw.label;
  }
}catch(e){}
if(QP.has('wp')){
  WP.list=wpUnpack(QP.get('wp'));       /* 空串=明确没有途径点，覆盖浏览器记忆 */
  if(QP.has('wpsz')){const n=parseFloat(QP.get('wpsz'));
    if(isFinite(n))WP.size=Math.min(30,Math.max(4,n));}
  if(QP.has('wpfs')){const n=parseFloat(QP.get('wpfs'));
    if(isFinite(n))WP.fs=Math.min(26,Math.max(9,n));}
  if(QP.has('wplbl'))WP.label=QP.get('wplbl')!=='0';
}
function cleanHex(v,d){v=String(v).replace(/^#/,'');
  return /^[0-9a-fA-F]{6}$/.test(v)?v.toLowerCase():d;}
function cleanNum(k,v){const n=parseFloat(v),a=RANGE[k][0],b=RANGE[k][1];
  return isFinite(n)?Math.min(b,Math.max(a,n)):SDEF[k];}
const TS = Object.assign({},SDEF);
try{const saved=JSON.parse(localStorage.getItem(SKEY)||'{}');
  for(const k in SDEF)if(k in saved)TS[k]= BOOLK[k]?!!saved[k]
    :(HEXK[k]?cleanHex(saved[k],SDEF[k]):cleanNum(k,saved[k]));
}catch(e){}
for(const q in QMAP){if(!QP.has(q))continue;
  const k=QMAP[q],v=QP.get(q);
  TS[k]= BOOLK[k]?(v!=='0'&&v!=='false'&&v!=='')
       :(HEXK[k]?cleanHex(v,SDEF[k]):cleanNum(k,v));
}
/* ---------- 离地高度：固定值 / 按局部坡度自动 ---------- */
const SLOPE = new Map();
for(const seg of TK.segments){
  for(let k=0;k<seg.length;k++){
    const a=seg[Math.max(0,k-1)], b=seg[Math.min(seg.length-1,k+1)];
    const dh=Math.abs(b[1]-a[1]);
    const dd=Math.hypot(b[0]-a[0],b[3]-a[3])||1;
    SLOPE.set(seg[k],Math.atan2(dh,dd));
  }
}
function liftOf(p){
  const r=+TS.w;
  if(!TS.auto)return +TS.lift;
  const th=SLOPE.get(p)||0;
  /* 自适应：缓坡压到 2.2 m 贴地，陡坡按 tan 略抬升避免整段埋进山体。
     管半径大于抬升时下半部会被地形自然遮住，这正是「贴着山走」的观感。 */
  return Math.min(8,Math.max(0.8,2.2+r*Math.tan(Math.min(th,1.2))*0.30));
}
/* 页面自带 DEM 网格（D.verts 前 NG 个为顶面顶点，y 为局部高程），
   按与 build.py 相同的三角剖分插值，供贴地影带逐点取真实地表高度。 */
function demAtLocal(x,z){
  const NX=M.grid[0],NY=M.grid[1],W=M.width_m,H=M.height_m,NGY=NY+1;
  const fi=Math.max(0,Math.min(NX-1e-9,(x+W/2)/W*NX));
  const fj=Math.max(0,Math.min(NY-1e-9,(H/2-z)/H*NY));
  const i=Math.floor(fi),j=Math.floor(fj),u=fi-i,v=fj-j;
  const h=(a,b)=>D.verts[a*NGY+b][1];
  const a=h(i,j),b=h(i+1,j),c=h(i+1,j+1),d=h(i,j+1);
  return u>=v ? a+(b-a)*u+(c-b)*v : a+(c-d)*u+(d-a)*v;
}
/* 所有用到轨迹 y 的地方都必须走这里，改离地高度时管/标记/标签/导出才会同步 */
function trackY(p,yi){return p[yi]-TK.lift+liftOf(p);}
function markerY(y){return y-TK.lift+liftOf(null);}
function styleParams(){
  return 'art='+(ART?'art':'sat')+'&pal='+PAL
       +'&trkw='+TS.w+'&trkc='+TS.c+'&trkhl='+TS.hl+'&trkhc='+TS.hc
       +'&trkop='+TS.op+'&trklift='+TS.lift
       +'&trkx='+(TS.xr?1:0)+'&trkauto='+(TS.auto?1:0)+'&trkshadow='+(TS.shadow?1:0)
       +'&wp='+encodeURIComponent(wpPack())+'&wpsz='+WP.size+'&wpfs='+WP.fs
       +(WP.label?'':'&wplbl=0')
       +'&cam='+packCam(CAM)+'&shots='+packShots(SHOTS)
       +'&dur='+DUR+'&asp='+ASP;
}
function saveStyle(){
  try{localStorage.setItem(SKEY,JSON.stringify(TS));}catch(e){}
  saveWP();
  document.documentElement.style.setProperty('--trk','#'+TS.c);
  el('trkParam').value=styleParams();
}
function styleText(){
  el('trkWVal').textContent=(+TS.w).toFixed(1)+' m';
  el('trkHlVal').textContent=(+TS.hl).toFixed(1)+'×';
  el('trkOpVal').textContent=Math.round(TS.op*100)+'%';
  el('trkLiftVal').textContent=(+TS.lift).toFixed(1)+' m';
  el('trkC').value='#'+TS.c; el('trkHc').value='#'+TS.hc;
  el('trkLift').disabled=!!TS.auto;
  saveStyle();
}
function syncStyleUI(){
  el('trkW').value=TS.w; el('trkHl').value=TS.hl; el('trkOp').value=TS.op;
  el('trkLift').value=TS.lift;
  el('xray').checked=!!TS.xr;
  el('trkAuto').checked=!!TS.auto;
  el('trkShadow').checked=!!TS.shadow;
  styleText();
}
window.__trackStyle = TS;        /* 供视频渲染自检 */
/* 读取当前实际生成的轨迹几何 / 材质，用于验证样式确实生效 */
window.__trackProbe = ()=>{
  const t=trackGroup.children.find(o=>o.geometry&&o.geometry.type==='TubeGeometry'&&o.renderOrder===2);
  return {tubeR:t?t.geometry.parameters.radius:null,
          color:t?t.material.color.getHexString():null,
          halo:trackGroup.children.some(o=>o.renderOrder===1),
          parts:trackGroup.children.length,
          lift:+TS.lift,auto:!!TS.auto,shadow:!!TS.shadow,xr:!!TS.xr,
          shadowBands:trackGroup.children.filter(o=>o.userData&&o.userData.groundShadow).length,
          liftStats:(()=>{const a=[];for(const s of TK.segments)for(const p of s)
            a.push(trackY(p,1)-(p[1]-TK.lift));
            return {min:+Math.min(...a).toFixed(2),max:+Math.max(...a).toFixed(2),
                    avg:+(a.reduce((u,v)=>u+v,0)/a.length).toFixed(2)};})(),
          param:el('trkParam').value,
          sat:!!(typeof satTex!=='undefined'&&satTex&&satTex.image&&satTex.image.width>0)};
};
el('trkTitle').textContent = TM.title + ' · ' + TM.source;
el('trkInfo').innerHTML = [
 ['坐标', TM.coord],
 ['落点', TM.inside+' / '+TM.total+' 点（'+(100*TM.inside/TM.total).toFixed(1)+'%）'],
 ['段数', TM.segments+' 段 · '+TM.inside_km+' km / 全程 '+TM.length_km+' km'],
 ['累计爬升', TM.ascent_m+' m（全程）'],
 ['KML 高程', TM.gps_min+' – '+TM.gps_max+' m'],
 ['坐标判定', '原坐标 corr='+TM.corr+'，纠偏后 corr='+TM.corr_gcj]
].map(([k,v])=>'<div class="kv"><span>'+k+'</span><b>'+v+'</b></div>').join('');
document.getElementById('profile').innerHTML = TK.profile;

const trackGroup = new THREE.Group();
group.add(trackGroup);                 // 与地形共用 group.scale.y（垂直夸张）
const labels = [];
/* ---------- 途径点：按累计里程定位，渲染 3D 标记 + 可避让的名称标签 ---------- */
const TRKP=[]; let TRKLEN=0;
for(const seg of TK.segments){
  for(let k=0;k<seg.length;k++){
    const p=seg[k];
    if(k>0){const q=seg[k-1];TRKLEN+=Math.hypot(p[0]-q[0],p[3]-q[3]);}
    TRKP.push({p:p,cum:TRKLEN});
  }
}
function sampleAt(t){
  const d=Math.max(0,Math.min(1,t))*TRKLEN;
  let lo=0,hi=TRKP.length-1;
  while(lo<hi){const m=(lo+hi)>>1;if(TRKP[m].cum<d)lo=m+1;else hi=m;}
  return TRKP[lo];
}
function escTxt(s){return String(s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function wpElev(p){return el('gpsMode').checked?p[6]:p[7];}
function renderWP(){
  const box=el('wpList');
  box.innerHTML=WP.list.length ? WP.list.map(function(o,i){
    const s=sampleAt(o.t),p=s.p;
    return '<div class="wpRow" data-i="'+i+'">'
      +'<span class="wpNo">'+(i+1)+'</span>'
      +'<input class="wpName" value="'+escTxt(o.name)+'" title="改名">'
      +'<input type="color" class="wpColor" value="#'+o.color+'" title="标记颜色">'
      +'<button class="wpDel" title="删除">×</button>'
      +'<span class="wpMeta">'+(s.cum/1000).toFixed(2)+' km · '
      +wpElev(p).toFixed(0)+' m · '+p[4].toFixed(5)+', '+p[5].toFixed(5)+'</span></div>';
  }).join('') : '<div class="wpEmpty">暂无途径点 · 用「拾取」或「按里程」添加</div>';
  el('wpCount').textContent=WP.list.length+' 个';
  el('wpSize').value=WP.size; el('wpSizeVal').textContent=WP.size+' m';
  el('wpFs').value=WP.fs; el('wpFsVal').textContent=WP.fs+' px';
  el('wpLbl').checked=WP.label;
  el('wpKm').max=(TRKLEN/1000).toFixed(2);
  saveWP(); saveStyle();
}
function nextWPName(){
  let mx=0;
  for(const o of WP.list){const m=/途径点\s*(\d+)/.exec(o.name);
    if(m)mx=Math.max(mx,+m[1]);}
  return '途径点 '+(mx+1);
}
function addWaypoint(t,name,color){
  const o=cleanWP({t:t,name:name||nextWPName(),
                   color:color||'ffd166',on:1});
  if(!o)return null;
  WP.list.push(o); WP.list.sort(function(a,b){return a.t-b.t;});
  renderWP(); buildTrack();
  return o;
}
window.__addWaypoint=addWaypoint;      /* 供自动化自检调用 */
function buildWaypoints(){
  const yi=el('gpsMode').checked?2:1, xray=!!TS.xr;
  WP.list.forEach(function(o,i){
    if(!o.on)return;
    const p=sampleAt(o.t).p, y=trackY(p,yi), col=new THREE.Color('#'+o.color);
    const mat=new THREE.MeshBasicMaterial({color:col,depthTest:!xray});
    const ball=new THREE.Mesh(new THREE.SphereGeometry(WP.size,16,12),mat);
    ball.position.set(p[0],y+WP.size*1.3,p[3]);ball.renderOrder=12;
    ball.userData.wpBall=true;trackGroup.add(ball);
    const pole=new THREE.Mesh(new THREE.CylinderGeometry(WP.size*.16,WP.size*.16,WP.size*1.3,8),mat);
    pole.position.set(p[0],y+WP.size*.65,p[3]);pole.renderOrder=12;trackGroup.add(pole);
    const ring=new THREE.Mesh(new THREE.TorusGeometry(WP.size*1.7,WP.size*.17,8,24),
      new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,depthTest:!xray}));
    ring.rotation.x=-Math.PI/2;ring.position.set(p[0],y+2,p[3]);
    ring.renderOrder=12;trackGroup.add(ring);
    const d=label(o.name||('P'+(i+1)),p[0],y+WP.size*2.7,p[3],'wp');
    d.style.borderColor='#'+o.color;d.style.color='#'+o.color;
    d.style.fontSize=WP.fs+'px';
    labels[labels.length-1].kind='wp';
  });
}
el('wpAddKm').onclick=function(){
  const km=parseFloat(el('wpKm').value);
  if(!isFinite(km))return;
  const w=addWaypoint(km*1000/TRKLEN);
  el('wpHint').textContent='已按 '+km.toFixed(2)+' km 添加「'+w.name+'」';
};
el('wpEven').onclick=function(){
  [0.25,0.5,0.75].forEach(function(t){addWaypoint(t);});
  el('wpHint').textContent='已按 1/4、1/2、3/4 里程添加 3 个点，可改名 / 改色';
};
el('wpList').oninput=function(e){
  const row=e.target.closest('.wpRow');if(!row)return;
  const o=WP.list[+row.dataset.i];if(!o)return;
  if(e.target.classList.contains('wpName')){
    o.name=e.target.value.slice(0,24);saveWP();saveStyle();buildTrack();
  }else if(e.target.classList.contains('wpColor')){
    o.color=cleanHex(e.target.value,o.color);saveWP();saveStyle();buildTrack();
  }
};
el('wpList').onclick=function(e){
  if(!e.target.classList.contains('wpDel'))return;
  const row=e.target.closest('.wpRow');if(!row)return;
  WP.list.splice(+row.dataset.i,1);renderWP();buildTrack();
};
el('wpLbl').onchange=function(e){WP.label=e.target.checked;saveWP();saveStyle();buildTrack();};
el('wpSize').oninput=function(e){
  WP.size=+e.target.value;el('wpSizeVal').textContent=WP.size+' m';
  saveWP();saveStyle();buildTrack();};
el('wpFs').oninput=function(e){
  WP.fs=+e.target.value;el('wpFsVal').textContent=WP.fs+' px';
  saveWP();saveStyle();buildTrack();};
el('wpClear').onclick=function(){
  WP.list=[];renderWP();buildTrack();el('wpHint').textContent='已清空，可重新添加';};
el('wpGeo').onclick=function(){
  const fc={type:'FeatureCollection',features:WP.list.map(function(o,i){
    const s=sampleAt(o.t),p=s.p;
    return {type:'Feature',properties:{seq:i+1,name:o.name,
      km:+(s.cum/1000).toFixed(3),gps_elev:p[6],dem_elev:p[7],
      lon:p[4],lat:p[5],color:o.color},
      geometry:{type:'Point',coordinates:[p[4],p[5],p[6]]}};
  })};
  download(new Blob([JSON.stringify(fc,null,1)],{type:'application/geo+json'}),
    'waypoints.geojson');
};
/* 在 3D 画面上点选轨迹添加途径点；拖拽旋转不会误触发 */
let pickMode=false, pickDown=null;
function setPick(on){
  pickMode=on;
  el('wpPick').classList.toggle('active',on);
  renderer.domElement.style.cursor=on?'crosshair':'';
  el('wpHint').textContent=on
    ? '拾取中：点击画面里的橙色轨迹（可连续添加），再点「拾取」结束'
    : '点「拾取」后在橙色轨迹上点击，或按里程添加';
}
el('wpPick').onclick=function(){setPick(!pickMode);};
const wpRay=new THREE.Raycaster();
renderer.domElement.addEventListener('pointerdown',function(e){
  pickDown=[e.clientX,e.clientY];});
renderer.domElement.addEventListener('pointerup',function(e){
  if(!pickMode||!pickDown)return;
  const moved=Math.hypot(e.clientX-pickDown[0],e.clientY-pickDown[1]);
  pickDown=null; if(moved>5)return;
  const c=renderer.domElement,r=c.getBoundingClientRect();
  wpRay.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,
    -((e.clientY-r.top)/r.height*2-1)),camera);
  group.updateMatrixWorld(true);
  const tubes=trackGroup.children.filter(function(o){
    return o.geometry&&o.geometry.type==='TubeGeometry'
      &&(o.renderOrder===2||o.renderOrder===11);});
  const hits=wpRay.intersectObjects(tubes,false);
  if(!hits.length){el('wpHint').textContent='没点到轨迹，请对准橙色线再试';return;}
  const q=group.worldToLocal(hits[0].point.clone());
  let best=TRKP[0],bd=Infinity;
  for(const o of TRKP){const d=Math.hypot(o.p[0]-q.x,o.p[3]-q.z);
    if(d<bd){bd=d;best=o;}}
  const w=addWaypoint(best.cum/TRKLEN);
  el('wpHint').textContent='已添加「'+w.name+'」 @ '+(best.cum/1000).toFixed(2)
    +' km，可继续点选或改名';
});
window.__waypointProbe=function(){
  return {count:WP.list.length,size:WP.size,fs:WP.fs,label:WP.label,
    list:WP.list.map(function(o){return {t:+o.t.toFixed(4),name:o.name,
      color:o.color,on:o.on};}),
    spheres:trackGroup.children.filter(function(o){
      return o.userData&&o.userData.wpBall;}).length,
    wpLabels:labels.filter(function(o){return o.kind==='wp';}).length,
    trackLenKm:+(TRKLEN/1000).toFixed(3),param:el('trkParam').value};
};
/* 自检辅助：把某个里程比例的轨迹点投影到屏幕像素，供自动化点击拾取验证 */
window.__wpScreenAt=function(t){
  const p=sampleAt(t).p;
  const v=new THREE.Vector3(p[0],trackY(p,1)*group.scale.y,p[3]).project(camera);
  return {x:Math.round((v.x*.5+.5)*innerWidth),
          y:Math.round((-v.y*.5+.5)*innerHeight)};
};
renderWP();
function label(text, x, y, z, cls){
  const d=document.createElement('div');
  d.className='lbl'+(cls?' '+cls:''); d.textContent=text;
  document.body.appendChild(d);
  labels.push({p:new THREE.Vector3(x,y,z),el:d,base:y});
  return d;
}
function clearTrack(){
  while(trackGroup.children.length){
    const o=trackGroup.children[0];
    trackGroup.remove(o);
    if(o.geometry)o.geometry.dispose();
    if(o.material)o.material.dispose();
  }
  labels.forEach(o=>o.el.remove()); labels.length=0;
}
function buildTrack(){
  clearTrack();
  const useGps=el('gpsMode').checked, yi=useGps?2:1, xray=!!TS.xr;
  const r=+TS.w, op=+TS.op, hl=+TS.hl;
  const tubeMat=new THREE.MeshBasicMaterial({color:new THREE.Color('#'+TS.c),
    depthTest:!xray,transparent:(xray||op<1),
    opacity:xray?Math.min(1,op*.9):op,depthWrite:!(xray||op<1)});
  const haloMat=hl>0?new THREE.MeshBasicMaterial({color:new THREE.Color('#'+TS.hc),
    side:THREE.BackSide,depthTest:!xray}):null;
  const dotGeo=new THREE.SphereGeometry(9,12,10);
  const dotMat=new THREE.MeshBasicMaterial({color:0xffd166});
  const endMat=new THREE.MeshBasicMaterial({color:0x185fa5});
  /* 贴地假投影：沿 DEM 地表画一条半透明窄影带，给低角度视角一个「地面在哪」的参照。
     始终取真实 DEM 地表 p[1]-TK.lift，与轨迹用 KML 高程还是 DEM 高程无关。 */
  if(TS.shadow && !xray){
    const sw=r*2.0;
    const shMat=new THREE.MeshBasicMaterial({color:0x241a12,transparent:true,
      opacity:.30,depthWrite:false,side:THREE.DoubleSide,
      polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
    for(const seg of TK.segments){
      const n=seg.length;
      const sp=new Float32Array(n*6), si=new Uint16Array((n-1)*6);
      for(let k=0;k<n;k++){
        const p=seg[k], a=seg[Math.max(0,k-1)], b=seg[Math.min(n-1,k+1)];
        let tx=b[0]-a[0], tz=b[3]-a[3];
        const tl=Math.hypot(tx,tz)||1; tx/=tl; tz/=tl;
        const lx=p[0]-tz*sw, lz=p[3]+tx*sw;
        const rx=p[0]+tz*sw, rz=p[3]-tx*sw;
        sp[k*6]  =lx; sp[k*6+1]=demAtLocal(lx,lz)+0.6; sp[k*6+2]=lz;
        sp[k*6+3]=rx; sp[k*6+4]=demAtLocal(rx,rz)+0.6; sp[k*6+5]=rz;
        if(k<n-1){
          const q=k*6, v0=k*2;
          si[q]=v0; si[q+1]=v0+1; si[q+2]=v0+2;
          si[q+3]=v0+1; si[q+4]=v0+3; si[q+5]=v0+2;
        }
      }
      const sg=new THREE.BufferGeometry();
      sg.setAttribute('position',new THREE.BufferAttribute(sp,3));
      sg.setIndex(new THREE.BufferAttribute(si,1));
      const band=new THREE.Mesh(sg,shMat);
      band.renderOrder=0; band.userData.groundShadow=true;
      trackGroup.add(band);
    }
  }
  for(const seg of TK.segments){
    const v=seg.map(p=>new THREE.Vector3(p[0],trackY(p,yi),p[3]));
    const curve=new THREE.CatmullRomCurve3(v,false,'centripetal');
    if(haloMat && !xray){
      const halo=new THREE.Mesh(new THREE.TubeGeometry(curve,v.length,r*hl,6,false),haloMat);
      halo.renderOrder=1; trackGroup.add(halo);
    }
    const tube=new THREE.Mesh(new THREE.TubeGeometry(curve,v.length,r,8,false),tubeMat);
    tube.renderOrder=xray?11:2; trackGroup.add(tube);
  }
  // 起终点 / 进入离开建模区 / 航点
  if(el('showMk').checked){
    for(const e of TK.endpoints){
      const y=markerY(useGps?e.y_gps:e.y_dem);
      const s=new THREE.Mesh(dotGeo,endMat); s.position.set(e.x,y,e.z);
      trackGroup.add(s);
      label(e.label,e.x,y+24,e.z,'end');
    }
    for(const m of TK.markers){
      const y=markerY(useGps?m.y_gps:m.y_dem);
      const s=new THREE.Mesh(dotGeo,dotMat); s.position.set(m.x,y,m.z);
      trackGroup.add(s);
      if(m.name) label(m.name,m.x,y+22,m.z,'');
    }
  }
  buildWaypoints();
  trackGroup.visible=el('showTrk').checked;
}
syncStyleUI();
buildTrack();
el('showTrk').onchange=()=>{trackGroup.visible=el('showTrk').checked;};
el('gpsMode').onchange=()=>buildTrack();
el('showMk').onchange=()=>buildTrack();
el('xray').onchange=()=>{TS.xr=el('xray').checked;saveStyle();buildTrack();};
const SLIDERS={trkW:'w',trkHl:'hl',trkOp:'op',trkLift:'lift'};
for(const id in SLIDERS)el(id).oninput=e=>{
  TS[SLIDERS[id]]=+e.target.value;styleText();buildTrack();};
el('trkAuto').onchange=e=>{TS.auto=e.target.checked;styleText();buildTrack();};
el('trkShadow').onchange=e=>{TS.shadow=e.target.checked;styleText();buildTrack();};
el('trkC').oninput=e=>{TS.c=cleanHex(e.target.value,TS.c);styleText();buildTrack();};
el('trkHc').oninput=e=>{TS.hc=cleanHex(e.target.value,TS.hc);styleText();buildTrack();};
document.querySelectorAll('#trkSw i').forEach(s=>s.onclick=()=>{
  TS.c=cleanHex(s.dataset.c,TS.c);syncStyleUI();buildTrack();});
el('trkReset').onclick=()=>{for(const k in SDEF)TS[k]=SDEF[k];syncStyleUI();buildTrack();};
el('trkCopy').onclick=()=>{
  el('trkParam').style.display='block';
  el('trkParam').focus();el('trkParam').select();
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(styleParams()).then(()=>{
      el('trkCopy').textContent='已复制';
      setTimeout(()=>el('trkCopy').textContent='复制参数',1400);
    }).catch(()=>{});
  }
};
el('trkGlb').onclick=()=>{
  const g=new THREE.Group();
  const useGps=el('gpsMode').checked, yi=useGps?2:1;
  for(const seg of TK.segments){
    const v=seg.map(p=>new THREE.Vector3(p[0],trackY(p,yi),p[3]));
    g.add(new THREE.Mesh(
      new THREE.TubeGeometry(new THREE.CatmullRomCurve3(v,false,'centripetal'),v.length,+TS.w,8,false),
      new THREE.MeshStandardMaterial({color:new THREE.Color('#'+TS.c),roughness:.6})));
  }
  for(const o of WP.list){                 /* 途径点一并导出 */
    if(!o.on)continue;
    const p=sampleAt(o.t).p;
    const s=new THREE.Mesh(new THREE.SphereGeometry(WP.size,12,10),
      new THREE.MeshStandardMaterial({color:new THREE.Color('#'+o.color),roughness:.6}));
    s.position.set(p[0],trackY(p,yi)+WP.size*1.3,p[3]);g.add(s);
  }
  g.scale.y=group.scale.y; g.updateMatrixWorld(true);
  new GLTFExporter().parse(g,
    r=>download(new Blob([r],{type:'model/gltf-binary'}),'hiking_track.glb'),
    e=>console.error(e),{binary:true});
};
const _rx=new THREE.Vector3(),_uy=new THREE.Vector3(),_bz=new THREE.Vector3();
const _north=new THREE.Vector3(0,0,-1);
function updateLabels(){
  if(location.search.indexOf('top')>=0){       // 北朝上正俯视：每帧覆盖 OrbitControls
    controls.enabled=false;
    camera.up.set(0,0,-1);
    camera.position.set(0,DIAG*1.4,1);
    camera.lookAt(0,0,0);
    camera.updateMatrixWorld();
  }
  camera.matrixWorld.extractBasis(_rx,_uy,_bz);
  const ang=Math.atan2(_north.dot(_rx),_north.dot(_uy));
  el('compassArrow').style.transform='rotate('+(ang*180/Math.PI)+'deg)';
  const sy=group.scale.y, placed=[];
  for(const o of labels){
    const v=o.p.clone(); v.y*=sy; v.project(camera);
    const vis=v.z<1 && el('showTrk').checked
      && (o.kind==='wp' ? WP.label : el('showMk').checked);
    o.el.style.display=vis?'block':'none';
    if(!vis)continue;
    let x=(v.x*.5+.5)*innerWidth, y=(-v.y*.5+.5)*innerHeight-6;
    const hw=Math.max(o.el.offsetWidth,60)/2+8;
    for(let k=0;k<8;k++){
      if(placed.some(p=>Math.abs(p.x-x)<p.hw+hw && Math.abs(p.y-y)<17)) y-=20;
      else break;
    }
    placed.push({x:x,y:y,hw:hw});
    o.el.style.left=x+'px'; o.el.style.top=y+'px';
  }
}
el('focusTrk').onclick=()=>{
  const pp=[];
  for(const seg of TK.segments)for(const p of seg)pp.push(new THREE.Vector3(p[0],trackY(p,1),p[3]));
  const bx=new THREE.Box3().setFromPoints(pp), c=bx.getCenter(new THREE.Vector3());
  const s=bx.getSize(new THREE.Vector3()), R=Math.max(s.x,s.z)*.85+320;
  c.y*=group.scale.y;
  controls.target.copy(c);
  camera.position.set(c.x+R*.75,c.y+R*.8,c.z+R*.95);
  controls.update();
};
if(location.search.indexOf('focus')>=0 && location.search.indexOf('top')<0)el('focusTrk').click();
/* 自检 / 调镜头辅助：按俯角、方位、距离设定相机（模块作用域，需挂到 window） */
window.__setCamera=function(elDeg,azDeg,R,ty){
  const e=elDeg*Math.PI/180, a=azDeg*Math.PI/180;
  camera.position.set(R*Math.cos(e)*Math.sin(a), ty+R*Math.sin(e), R*Math.cos(e)*Math.cos(a));
  controls.target.set(0,ty,0);
  for(let i=0;i<6;i++)controls.update();
  camera.updateMatrixWorld(); updateLabels(); renderer.render(scene,camera);
  return [camera.position.x,camera.position.y,camera.position.z].map(v=>+v.toFixed(1));
};
__CAM_UI__
"""

page = page.replace("</style>", CSS + CAM_CSS + "</style>")
page = page.replace("<div class=\"src\" id=\"src\"></div>",
                    "<div class=\"src\" id=\"src\"></div>\n"
                    + TRACK_HTML.replace("__PROFILE__", "<div id=\"profile\"></div>" + STYLE_HTML))
page = page.replace("<label class=\"chk\"><input type=\"checkbox\" id=\"autoRot\">自动旋转</label>",
                    CTRL_HTML + CAM_HTML +
                    "\n<label class=\"chk\"><input type=\"checkbox\" id=\"autoRot\">自动旋转</label>")
page = page.replace("<div class=\"btns\"><button id=\"reset\">重置视角</button></div>",
                    "<div class=\"btns\"><button id=\"focusTrk\">聚焦轨迹</button>"
                    "<button id=\"trkGlb\">导出轨迹 GLB</button></div>\n"
                    "<div class=\"btns\"><button id=\"reset\">重置视角</button></div>")
page = page.replace("el('status').style.display='none';",
                    TRACK_JS.replace("__TRACK__", json.dumps(data, ensure_ascii=False,
                                                             separators=(",", ":")))
                    .replace("__CAM_CORE__", CAM_CORE)
                    .replace("__CAM_UI__", CAM_UI)
                    + "\nel('status').style.display='none';")
page = page.replace(
    "(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();",
    "(function loop(){requestAnimationFrame(loop);controls.update();"
    "updateLabels();renderer.render(scene,camera);})();")
page = page.replace(
    "Imagery © Esri, Maxar, Earthstar Geographics",
    "卫星影像为本地离线纹理（Esri/Maxar）；对外发布请改用天地图 / 腾讯地图等合规底图")
page = page.replace("</head>", "<meta name=\"track-points\" content=\"%d\">\n</head>" % sum(flags))

# 校验注入点全部命中
for token in ("const TK = {", "trackGroup", "hiking_track.glb",
              "updateLabels();renderer.render", ".lbl{", "id=\"trkInfo\"",
              "id=\"trkW\"", "window.__trackStyle", "trkCopy", "styleParams()",
              "id=\"wpPick\"", "window.__waypointProbe", "buildWaypoints();",
              "id=\"trkLift\"", "id=\"trkAuto\"", "id=\"trkShadow\"",
              "trklift=", "groundShadow", "function trackY(",
              "id=\"camAz\"", "id=\"camPlay\"", "_aspBox.id=",
              "id=\"camPlayBtn\"", "id=\"camPlayBtn2\"", "id=\"camLoop\"",
              "id=\"camRecT\"", "camRecAt", "camGo", "camOvr",
              "function togglePlay(", "function recordCurrent(",
              "function recTime(", "playing:PLAYING",
              "function applyCam(", "function shotAt(", "function unpackShots(",
              "window.__camProbe", "&shots=", "&cam="):
    assert token in page, "inject failed: " + token

old = os.path.join(HERE, "index.html")
if os.path.exists(old) and not os.path.exists(os.path.join(HERE, "index.before-track.html")):
    os.replace(old, os.path.join(HERE, "index.before-track.html"))
open(old, "w", encoding="utf-8").write(page)
open(os.path.join(HERE, "track.html"), "w", encoding="utf-8").write(page)

print("track: %d/%d pts inside, %d segment(s), %.2f/%.2f km, %d markers"
      % (sum(flags), len(pts), len(segs), length_in / 1000, length_all / 1000, len(markers)))
print("grid self-check max err = %.4f m" % err)
print("wrote track.json / track_in_model.geojson / track.html / index.html (%d B)" % len(page))

# 同步刷新可双击打开的单文件版（依赖内联 + 贴图 base64），避免下次重建后 standalone 过期
try:
    import make_standalone
    make_standalone.build()
except Exception as e:
    print("standalone 生成失败（http 版不受影响）: %s" % e)
