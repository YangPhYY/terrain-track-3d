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
KML = r"E:\下载\山顶人家小顶山徐霞客古道环线.kml"
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
# COORD_NOTE 在解析 KML 后按实际格式赋值（两步路 gx:Track = WGS84；奥维 LineString = CGCS2000）
# 历史对照（旧轨迹实测）：原样 corr 0.9344 / RMSE 21.87；强行纠偏后 -0.7282 / 113.56。
# 现在 corr、RMSE 每次按当前轨迹实算，仅保留「纠偏后」这组负值作为反例参考。
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


def strip_cdata(s):
    """两步路把标题/标注名包在 CDATA 里，直接显示会出现 <![CDATA[…]]> 字面量。"""
    s = (s or "").strip()
    m = re.match(r"^<!\[CDATA\[(.*?)\]\]>$", s, re.S)
    return (m.group(1) if m else s).strip()


title = strip_cdata(re.search(r"<Document[^>]*>\s*<name>(.*?)</name>", kml, re.S).group(1))
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
COORD_NOTE = ("WGS84（两步路实测，未纠偏）" if "<gx:Track>" in kml
              else "CGCS2000（≈WGS84，未纠偏）")

M_LON = W / (box[2] - box[0])
M_LAT = H / (box[3] - box[1])


def dist(a, b):
    return math.hypot((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT)


def inside(p):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


# 贴合校验：KML 高程 vs Terrarium DEM 的皮尔逊相关与 RMSE（每次按当前轨迹实算）。
# 坐标系判错（该纠没纠 / 不该纠却纠）时相关性会掉到 0 附近甚至变负，是换区域时最灵敏的探针。
DEMS = [dem_at(*to_m(p[0], p[1])) for p in pts]
BIAS = sum(p[2] - d for p, d in zip(pts, DEMS)) / len(pts)  # KML 高程相对 DEM 的系统偏差
_mg = sum(p[2] for p in pts) / len(pts)
_md = sum(DEMS) / len(DEMS)
_sg = math.sqrt(sum((p[2] - _mg) ** 2 for p in pts))
_sd = math.sqrt(sum((d - _md) ** 2 for d in DEMS))
CORR = round(sum((p[2] - _mg) * (d - _md) for p, d in zip(pts, DEMS)) / (_sg * _sd), 4) if _sg and _sd else 0.0
RMSE = round(math.sqrt(sum((p[2] - d) ** 2 for p, d in zip(pts, DEMS)) / len(pts)), 2)

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
    pid, name = mid.group(1), strip_cdata(htmlmod.unescape(mname.group(1))).strip()
    lon, lat, g = map(float, mc.group(1).strip().split(",")[:3])
    if not inside((lon, lat)):
        continue
    # 两步路会导出大量无名 realPoint（随手打的点）以及 startPoint/endPoint；
    # 后者与上面的 endpoints 重复标一次，无名点只有圆点没有含义 —— 都跳过。
    if pid in ("startPoint", "endPoint"):
        continue
    x, z = to_m(lon, lat)
    d = dem_at(x, z)
    if not name:                     # 无名「标注点」不落点，避免满屏无意义圆点
        continue
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


def bj_hm(iso):
    """gx:Track 的 when 是 UTC；两步路记录名用的是本地时间，这里统一显示北京时间 HH:MM。"""
    if not iso:
        return ""
    try:
        from datetime import datetime, timedelta
        return (datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
                + timedelta(hours=8)).strftime("%H:%M")
    except Exception:
        return iso[11:16]


for lab, i in specs:
    p = point(i)
    text = lab + (" " + bj_hm(whens[i]) if whens[i] else "")
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
    "coord": COORD_NOTE, "corr": CORR, "rmse": RMSE,
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
.camPresets.wrap{flex-wrap:wrap}
.camPresets.wrap button{flex:1 1 46%}
.camMini{font-size:10px;color:#8b96a6;white-space:nowrap}
#growS,#growD{width:46px;font-size:11px;padding:2px 3px;border:1px solid #d6dee9;border-radius:4px}
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
box-shadow:0 0 0 9999px rgba(10,17,28,.62)}   /* 框外压暗：画幅裁切后框外不再绘制，深一点更像「取景器」 */
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
.lbl{position:absolute;transform:translate(-50%,-100%);pointer-events:none;white-space:nowrap;transition:opacity .25s linear;
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
.lbl.wp.bold{font-weight:800;letter-spacing:.2px}
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
<label class="chk"><input type="checkbox" id="showEnds" checked>起终点标记</label>
<label class="chk"><input type="checkbox" id="showMk" checked>标注点</label>
<label class="chk" title="默认关闭：被山体挡住的标签自动隐藏。低角度掠视时远近标签不会挤成一团；要看全貌再打开"><input type="checkbox" id="wpOcc">标签透视（被山挡也显示）</label>
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
  <label class="chk" title="标签文字加粗，远看或压缩后更清楚"><input type="checkbox" id="wpBold">加粗</label>
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
<div class="camPresets wrap" id="camTplRow1">
  <button id="camTour" title="依次飞到每个途径点做特写，最后拉远定格全局">途径点巡礼</button>
  <button id="camFollow" title="相机贴着轨迹前进方向低空飞行，走完全程">沿线跟拍</button>
  <button id="camSpin" title="匀速绕模型一圈，首尾可无缝衔接">环绕展示</button>
  <button id="camRide" title="机位贴近地面随坡度升降，强调爬升段">高程跟随</button>
</div>
<div class="camPresets wrap" id="camTplRow2">
  <button id="camDive" title="从高空俯冲到起点，转为低角度">俯冲进场</button>
  <button id="camPull" title="从终点低角度拉升到全局">拉升退场</button>
  <button id="camTop" title="正上方北朝上，平面地图感，适合配字幕开场">俯视平面</button>
  <button id="camPoi" title="绕离当前视角最近的途径点小半径盘旋">单点环绕</button>
</div>
<div class="camPresets wrap">
  <label class="chk" title="轨迹从起点逐段长出（渲染成片也会带上）"><input type="checkbox" id="growOn">轨迹生长</label>
  <span class="camMini">起</span><input type="number" id="growS" min="0" max="59" step="0.1" value="0">
  <span class="camMini">秒 用时</span><input type="number" id="growD" min="0.2" max="30" step="0.1" value="4">
  <span class="camMini">秒</span>
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

# ---------- 网页直出视频：WebCodecs 编码 + mp4-muxer 封装，不依赖 Node / ffmpeg ----------
EXP_CSS = """
/* 网页直出 MP4：控制面板里一行按钮 + 一个进度遮罩 */
.expHead{display:flex;justify-content:space-between;align-items:baseline;
font-size:12px;font-weight:500;color:#243042;margin:0 0 2px}
.expHead span{font-size:9px;font-weight:400;color:#9aa5b4}
.expBar{display:flex;gap:4px;align-items:center;margin:5px 0}
.expBar>button{flex:1 1 auto;min-width:0;font-size:10px;padding:5px 3px;white-space:nowrap}
.expBar select{font:10px system-ui,"Microsoft YaHei",sans-serif;color:#243042;
background:#fff;border:1px solid #d9e1ec;border-radius:4px;padding:2px 1px;flex:0 0 auto}
#expHint{margin:0 0 3px}
body.exporting .lbl{display:none!important}   /* 导出时标签由 canvas 自己画，避免重影 */
#expMask{position:fixed;inset:0;z-index:60;display:none;align-items:center;
justify-content:center;background:rgba(16,26,40,.5)}
body.exporting #expMask{display:flex}
.expCard{width:342px;background:#fff;border-radius:12px;padding:16px 18px;
box-shadow:0 14px 44px rgba(20,35,60,.3)}
.expCard h3{margin:0 0 10px;font-size:14px;font-weight:500;color:#243042}
.expTrack{height:6px;background:#eef2f8;border-radius:3px;overflow:hidden}
.expTrack i{display:block;height:100%;width:0;border-radius:3px;
background:linear-gradient(90deg,#2681ff,#185fa5);transition:width .12s linear}
.expStat{font:11px/1.65 ui-monospace,Consolas,monospace;color:#5b6779;
margin-top:8px;min-height:34px;word-break:break-all}
.expActs{display:flex;gap:6px;margin-top:10px}
.expActs button,.expActs a{flex:1;text-align:center;font:12px system-ui,"Microsoft YaHei",sans-serif;
padding:7px 8px;border-radius:6px;border:1px solid #cddffb;background:#eef4ff;
color:#185fa5;cursor:pointer;text-decoration:none}
.expActs a{background:#185fa5;color:#fff;border-color:#185fa5;font-weight:500}
.expActs a[hidden]{display:none}
.expNote{font-size:9px;color:#9aa5b4;line-height:1.45;margin-top:8px}
"""

EXP_HTML = """
<div class="expHead">视频导出<span>网页直出 MP4 · 不经渲染脚本</span></div>
<div class="expBar">
  <button id="expVideo" title="在本机浏览器里直接编码 MP4（GPU 硬件编码，不上传）">⬇ 导出 MP4</button>
  <select id="expFps" title="帧率"><option value="24">24fps</option><option value="30">30fps</option><option value="60">60fps</option></select>
  <select id="expRes" title="清晰度（短边 720 / 1080 / 1440 / 2160）"><option value="720">720p</option><option value="1080">1080p</option><option value="1440">1440p</option><option value="2160">4K</option></select>
</div>
<div class="camHint" id="expHint">—</div>
<div class="sep"></div>
<div id="expMask"><div class="expCard">
  <h3 id="expTitle">正在导出 MP4</h3>
  <div class="expTrack"><i id="expFill"></i></div>
  <div class="expStat" id="expStat"></div>
  <div class="expActs"><button id="expCancel">取消</button><a id="expSave" download hidden>下载 MP4</a></div>
  <div class="expNote">编码在本机 GPU 上完成，画面不外传。导出时保持本标签页在前台，别切走或最小化。</div>
</div></div>
"""

EXP_JS = r"""
/* ---------- 网页直出 MP4：WebCodecs(H.264) + mp4-muxer ----------
   逐帧驱动与 video.html 的 __setFrame 同源（shotAt/applyCam/__applyGrowAt），
   所以页面上预览到的构图 = 导出的构图；标签由 canvas 2D 按同一套避让规则重画，
   不依赖 DOM 叠层，导出分辨率与窗口大小无关。 */
const EKEY='terrain_hd_export_v1';
const EXP={fps:60,res:1080};
try{
  const sv=JSON.parse(localStorage.getItem(EKEY)||'null');
  if(sv){
    if([24,30,60].indexOf(+sv.fps)>=0)EXP.fps=+sv.fps;
    if([720,1080,1440,2160].indexOf(+sv.res)>=0)EXP.res=+sv.res;
  }
}catch(e){}
function expSaveCfg(){try{localStorage.setItem(EKEY,JSON.stringify(EXP));}catch(e){}}
function expAr(){return ASP==='916'?9/16:(ASP==='169'?16/9:innerWidth/Math.max(1,innerHeight));}
function expSize(){
  const ar=expAr(), s=EXP.res;
  let W,H;
  if(ar>=1){W=Math.round(s*ar);H=s;}else{W=s;H=Math.round(s/ar);}
  return {W:W+(W&1),H:H+(H&1)};          /* H.264 要求偶数边长 */
}
function expTotal(){return Math.max(1,Math.round(DUR*EXP.fps));}
function expHintText(){
  if(!el('expHint'))return;
  const z=expSize();
  el('expHint').textContent=(+DUR).toFixed(1)+' s · '+expTotal()+' 帧 · '+
    z.W+'×'+z.H+' · '+EXP.fps+' fps';
}
window.__expHint=expHintText;      /* syncCamUI 在初始化阶段会调，先挂窗口避免 TDZ */
let EXPING=false, EXPCANCEL=false, XR=null;   /* XR：导出专用离屏渲染器 */
function expOpen(title,stat){
  el('expTitle').textContent=title||'正在导出 MP4';
  el('expStat').textContent=stat||'';
  el('expFill').style.width='0%';
  el('expSave').hidden=true;
  el('expCancel').textContent='取消';
  document.body.classList.add('exporting');
}
function expClose(){document.body.classList.remove('exporting');}
function expRoundRect(c,x,y,w,h,r){
  if(c.roundRect){c.beginPath();c.roundRect(x,y,w,h,r);return;}
  c.beginPath();
  c.moveTo(x+r,y);c.lineTo(x+w-r,y);c.quadraticCurveTo(x+w,y,x+w,y+r);
  c.lineTo(x+w,y+h-r);c.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
  c.lineTo(x+r,y+h);c.quadraticCurveTo(x,y+h,x,y+h-r);
  c.lineTo(x,y+r);c.quadraticCurveTo(x,y,x+r,y);
  c.closePath();
}
/* 标签重画：可见性 / 遮挡 / 生长 / 撞车避让规则与 updateLabels 保持一致，
   区别只是投影基准换成导出分辨率（W,H），字号按高度等比放大 */
function expDrawLabels(ctx,W,H,scl,lstyle){
  if(!el('showTrk').checked)return;
  const sy=group.scale.y||1, cand=[];
  for(const o of labels){
    const w=o.p.clone();w.y*=sy;
    const v=w.clone().project(camera);
    if(v.z>=1)continue;
    const isWp=o.kind==='wp';
    if(isWp){if(!WP.label)continue;}
    else if(o.el.classList.contains('end')? !el('showEnds').checked
                                         : !el('showMk').checked)continue;
    if(!WP.occ&&labelBlocked(w))continue;
    if(GRW.on&&o.t!==null&&o.t!==undefined&&GROWP<0.999&&o.t>GROWP+0.004)continue;
    const st=lstyle.get(o);
    if(!st||!st.text)continue;
    cand.push({st:st,v:v,d:w.distanceToSquared(camera.position)});
  }
  if(!cand.length)return;
  cand.sort(function(a,b){return a.d-b.d;});
  const placed=[];
  ctx.textAlign='center';ctx.textBaseline='middle';
  for(const c of cand){
    const st=c.st, f=st.fs*scl;
    ctx.font=(st.bold?'800 ':'500 ')+f+'px system-ui,"Microsoft YaHei",sans-serif';
    const bw=ctx.measureText(st.text).width+12*scl+2*st.bw*scl;
    const bh=f*1.65+6*scl+2*st.bw*scl;
    const x=(c.v.x*.5+.5)*W;
    let y=(-c.v.y*.5+.5)*H-6*scl;
    if(x<-60*scl||x>W+60*scl||y<-60*scl||y>H+60*scl)continue;
    const hw=bw/2+8*scl;
    for(let k=0;k<2;k++){
      if(!placed.some(function(p){return Math.abs(p.x-x)<p.hw+hw&&Math.abs(p.y-y)<17*scl;}))break;
      y-=20*scl;
    }
    if(placed.some(function(p){return Math.abs(p.x-x)<p.hw+hw&&Math.abs(p.y-y)<17*scl;}))continue;
    placed.push({x:x,y:y,hw:hw});
    const left=x-bw/2, top=y-bh;
    ctx.save();
    ctx.shadowColor='rgba(0,0,0,.12)';ctx.shadowBlur=4*scl;ctx.shadowOffsetY=scl;
    ctx.fillStyle='rgba(255,255,255,.93)';
    expRoundRect(ctx,left,top,bw,bh,4*scl);ctx.fill();
    ctx.restore();
    ctx.strokeStyle=st.border;ctx.lineWidth=Math.max(1,st.bw*scl);
    expRoundRect(ctx,left,top,bw,bh,4*scl);ctx.stroke();
    ctx.fillStyle=st.color;
    ctx.fillText(st.text,x,top+bh/2);
  }
}
async function expRun(opt){
  opt=opt||{};
  if(EXPING)return {ok:false,err:'busy'};
  const fps=opt.fps||EXP.fps;
  const z=opt.W?{W:opt.W,H:opt.H}:expSize();
  const total=opt.frames||Math.max(1,Math.round((opt.dur||DUR)*fps));
  const bitrate=opt.bitrate||Math.min(48e6,Math.round(z.W*z.H*fps*0.10));
  EXPING=true;EXPCANCEL=false;
  expOpen('正在导出 MP4','检测本机 H.264 编码器…');
  stopPlay();
  const t0=performance.now();
  const ASPECT=camera.aspect;
  let enc=null, muxer=null, done=0;
  try{
    if(!('VideoEncoder' in window))
      throw new Error('当前浏览器不支持 WebCodecs，请用 Chrome / Edge 94+ 打开');
    /* 1) 挑一个本机支持的 H.264 profile/level（4K 需要更高 level） */
    let codec=null;
    /* 从低 level 往高试：拿到能满足当前分辨率/帧率的最低档，
       兼容性最好（4K60 才会落到 5.1/5.2） */
    for(const c of ['avc1.640028','avc1.640029','avc1.64002a','avc1.640032',
                    'avc1.640033','avc1.640034',
                    'avc1.4d0028','avc1.4d002a','avc1.4d0033','avc1.42E01E']){
      let r=null;
      try{r=await VideoEncoder.isConfigSupported(
        {codec:c,width:z.W,height:z.H,framerate:fps,bitrate:bitrate});}catch(e){}
      if(r&&r.supported){codec=c;break;}
    }
    if(!codec)throw new Error('本机没有可用的 H.264 编码器（'+z.W+'×'+z.H+' @'+fps+'fps）');
    /* 2) 封装器：http 页面从 vendor 动态加载；单文件版用内联进 __THREE_BUNDLE__ 的那份 */
    let Mux=null;
    if(window.__THREE_BUNDLE__&&window.__THREE_BUNDLE__.Muxer)Mux=window.__THREE_BUNDLE__;
    else{
      try{Mux=await import('./vendor/mp4-muxer.js');}
      catch(e){throw new Error('读不到 vendor/mp4-muxer.js（单文件版请用 http:// 打开）');}
    }
    muxer=new Mux.Muxer({target:new Mux.ArrayBufferTarget(),
      video:{codec:'avc',width:z.W,height:z.H,framerate:fps},
      fastStart:'in-memory'});          /* moov 前置，微信/手机播放器都能直接播 */
    let encErr=null;
    enc=new VideoEncoder({
      output:function(chunk,meta){try{muxer.addVideoChunk(chunk,meta);}catch(e){encErr=e;}},
      error:function(e){encErr=e;}});
    enc.configure({codec:codec,width:z.W,height:z.H,framerate:fps,bitrate:bitrate,
      latencyMode:'realtime',avc:{format:'avc'}});
    /* 编码器背压：硬件编码器队列深了会卡住整条管线（1080p 长片实测），
       所以每帧都等队列降到 3 以下再继续；超过 15 s 不动就报错，避免假死 */
    async function expDrain(){
      const t0=performance.now();
      while(enc.encodeQueueSize>3){
        await new Promise(function(r){setTimeout(r,4);});
        if(performance.now()-t0>15000)
          throw new Error('编码器无响应（队列 '+enc.encodeQueueSize+
                          '），可降到 720p 或 30fps 再试');
      }
    }
    /* 3) 单独开一个离屏渲染器出帧：不挂到 DOM，合成器完全不参与，
          避免「主画布被合成器反复缩放 + drawImage 抢 GPU」跑到 ~80 帧假死。
          主画布保持原样，导出结束后不用还原尺寸。 */
    if(!XR){
      XR=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true,alpha:false});
      XR.outputColorSpace=renderer.outputColorSpace;
      XR.toneMapping=renderer.toneMapping;
      XR.toneMappingExposure=renderer.toneMappingExposure;
    }
    XR.setPixelRatio(1);
    XR.setSize(z.W,z.H,false);
    camera.aspect=z.W/z.H;camera.updateProjectionMatrix();
    const comp=document.createElement('canvas');
    comp.width=z.W;comp.height=z.H;
    const ctx=comp.getContext('2d',{alpha:false});
    const scl=z.H/1080;
    /* 标签样式先缓存一份：逐帧 getComputedStyle 太慢 */
    const lstyle=new Map();
    for(const o of labels){
      const cs=getComputedStyle(o.el);
      lstyle.set(o,{text:o.el.textContent||'',color:cs.color||'#9d3412',
        border:cs.borderTopColor||'#ff8a63',
        bold:(parseInt(cs.fontWeight,10)||400)>=600,
        fs:parseFloat(cs.fontSize)||11,
        bw:parseFloat(cs.borderTopWidth)||1});
    }
    window.__exporting=true;        /* 让 rAF 循环让位，逐帧由这里驱动 */
    let lastPaint=0, tStart=0;      /* tStart：第一帧之后才算，别把着色器编译算进帧率 */
    for(let i=0;i<total;i++){
      if(EXPCANCEL)break;
      const t=i/fps;
      const s=shotAt(SHOTS,t);if(s)applyCam(s);
      if(window.__applyGrowAt)window.__applyGrowAt(t);
      const p0=performance.now();
      XR.render(scene,camera);
      /* 每帧跟 GPU 同步一次：drawImage/VideoFrame 在 Chrome 里是异步的，
         不同步就会把几百帧的 1080p 纹理全排进队列（>1 GB），
         最后 flush 时主线程被拖死（实测 200 帧后假死） */
      XR.getContext().finish();
      const p1=performance.now();
      ctx.drawImage(XR.domElement,0,0,z.W,z.H);
      const p2=performance.now();
      expDrawLabels(ctx,z.W,z.H,scl,lstyle);
      const p3=performance.now();
      const vf=new VideoFrame(comp,{timestamp:Math.round(i*1e6/fps),
        duration:Math.round(1e6/fps)});
      const p4=performance.now();
      enc.encode(vf,{keyFrame:(i%(fps*2))===0});
      vf.close();
      if(opt.debug)console.log('[exp] i='+i+' R='+(p1-p0).toFixed(0)+
        ' D='+(p2-p1).toFixed(0)+' L='+(p3-p2).toFixed(0)+
        ' V='+(p4-p3).toFixed(0)+' E='+(performance.now()-p4).toFixed(0)+
        ' q='+enc.encodeQueueSize);
      done=i+1;
      if(!tStart)tStart=performance.now();
      if(encErr)throw encErr;
      await expDrain();
      /* 每 24 帧彻底排空一次：Chrome 的 encodeQueueSize 有时不反映真实占用，
         积压的 VideoFrame 会把 GPU/帧池占满，表现为 1080p 长片跑到 ~80 帧假死 */
      if(i%24===23)await enc.flush();
      const now=performance.now();
      if(now-lastPaint>120||i===total-1){
        lastPaint=now;
        const per=(now-tStart)/Math.max(1,done-1);
        const left=Math.max(0,(total-done)*per)/1000;
        el('expFill').style.width=(100*done/total).toFixed(1)+'%';
        el('expStat').textContent=done+' / '+total+' 帧 · '+
          (100*done/total).toFixed(0)+'% · '+(1000/per).toFixed(1)+' 帧/秒 · 剩余 '+
          (left<10?left.toFixed(1):left.toFixed(0))+' s';
        if(opt.debug)console.log('[exp] '+done+'/'+total+' q='+enc.encodeQueueSize+
          ' t='+Math.round(now-t0)+'ms');
        await new Promise(function(r){setTimeout(r,0);});
      }
    }
    if(EXPCANCEL){
      try{enc.close();}catch(e){}
      enc=null;
      throw {cancelled:true};
    }
    el('expStat').textContent='正在封装 MP4…';
    if(opt.debug)console.log('[exp] flush start q='+enc.encodeQueueSize);
    await enc.flush();
    if(opt.debug)console.log('[exp] flush done');
    enc.close();enc=null;
    muxer.finalize();
    if(opt.debug)console.log('[exp] muxed '+(muxer.target.buffer.byteLength/1048576).toFixed(1)+'MB');
    const blob=new Blob([muxer.target.buffer],{type:'video/mp4'});
    const ms=performance.now()-t0;
    const name='terrain_'+z.W+'x'+z.H+'_'+fps+'fps.mp4';
    const url=URL.createObjectURL(blob);
    const a=el('expSave');
    a.href=url;a.download=name;a.hidden=false;
    a.textContent='下载 MP4（'+(blob.size/1048576).toFixed(1)+' MB）';
    el('expTitle').textContent='导出完成';
    el('expStat').textContent=name+' · '+done+' 帧 · '+
      (blob.size/1048576).toFixed(1)+' MB · 用时 '+(ms/1000).toFixed(1)+' s · '+codec;
    el('expCancel').textContent='关闭';
    el('expFill').style.width='100%';
    if(!opt.noDownload){try{a.click();}catch(e){}}
    const out={ok:true,bytes:blob.size,frames:done,codec:codec,ms:Math.round(ms),
               w:z.W,h:z.H};
    if(opt.dump){
      out.b64=await new Promise(function(res){
        const fr=new FileReader();
        fr.onload=function(){res(String(fr.result).split(',')[1]||'');};
        fr.onerror=function(){res('');};
        fr.readAsDataURL(blob);
      });
    }
    return out;
  }catch(e){
    if(e&&e.cancelled){
      el('expTitle').textContent='已取消导出';
      el('expStat').textContent='没有生成文件。';
      el('expCancel').textContent='关闭';
      el('expFill').style.width='0%';
      return {ok:false,err:'cancel'};
    }
    const msg=(e&&e.message)||String(e);
    el('expTitle').textContent='导出失败';
    el('expStat').textContent=msg;
    el('expCancel').textContent='关闭';
    console.error('[export]',e);
    return {ok:false,err:msg};
  }finally{
    EXPING=false;
    window.__exporting=false;
    try{if(enc&&enc.state!=='closed')enc.close();}catch(e){}
    if(XR){try{XR.dispose();XR.forceContextLoss();}catch(e){} XR=null;}  /* 释放离屏上下文 */
    camera.aspect=ASPECT;camera.updateProjectionMatrix();
    try{previewAt(PLAY,true);updateLabels();renderer.render(scene,camera);}catch(e){}
    expHintText();
  }
}
window.__exportRun=expRun;                /* 自检 / 自动化用 */
window.__exportProbe=function(){
  return {webcodecs:('VideoEncoder' in window),fps:EXP.fps,res:EXP.res,
          size:expSize(),frames:expTotal(),dur:DUR,asp:ASP};
};
el('expFps').value=String(EXP.fps);
el('expRes').value=String(EXP.res);
el('expFps').onchange=function(){EXP.fps=+el('expFps').value;expSaveCfg();expHintText();};
el('expRes').onchange=function(){EXP.res=+el('expRes').value;expSaveCfg();expHintText();};
el('expVideo').onclick=function(){expRun();};
el('expCancel').onclick=function(){
  if(EXPING){EXPCANCEL=true;el('expStat').textContent='正在停止…';return;}
  expClose();
};
expHintText();
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
/* az 可以累积到 0–360 之外（相邻帧差 >180° 时用它控制转向，机位不变），显示时归一化 */
const azN=a=>{const v=(((+a)%360)+360)%360;return Math.abs(v-360)<0.05?0:v;};
function camSmooth(x){return x*x*(3-2*x);}
function camLerp(a,b,f){return a+(b-a)*f;}
function shotAt(shots,t){
  if(!shots||!shots.length)return null;
  let k=0;
  while(k<shots.length-2 && t>shots[k+1].t)k++;
  const A=shots[k],B=shots[Math.min(k+1,shots.length-1)];
  const raw=Math.max(0,Math.min(1,(t-A.t)/Math.max(1e-6,B.t-A.t)));
  /* sm:0 = 线性推进（模板自动生成的密集关键帧用）。
     smoothstep 在每一段首尾速度都归零，16 段的跟拍会变成「走 0.9 s 停一下」的顿挫；
     手调分镜不带 sm，仍走缓入缓出。 */
  const f=A.sm===0?raw:camSmooth(raw);
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
  /* 接近正上方时用「-z＝北」作上方向，否则 up 与视线几乎平行，画面会随机歪 */
  if(e>1.551){camera.up.set(0,0,-1);}
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
/* 垂直夸张 ex=（0.5–4）与起终点开关 ends=0/1 也进参数串，
   保证逐帧渲染出来的坡度和页面上一模一样 */
if(QP.has('ex')){
  const n=parseFloat(QP.get('ex'));
  if(isFinite(n)){const v=Math.min(4,Math.max(0.5,n));
    group.scale.y=v; el('ex').value=v; el('exVal').textContent=v.toFixed(1)+'×'; syncBase();}
}
if(QP.has('ends'))el('showEnds').checked=QP.get('ends')!=='0';
if(QP.has('mk'))el('showMk').checked=QP.get('mk')!=='0';   /* 标注点（KML 航点）开关 */
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
/* 画幅裁切：切 9:16 / 16:9 时不能只画个虚框 —— 原来相机仍是窗口比例，
   框里看到的是 16:9 取景被遮掉两侧，模型就「超出屏幕」，而导出又是真 9:16，预览与成片对不上。
   现在把渲染视口也收进框内、camera.aspect 同步，框内像素 = 导出的那一帧。 */
let CUTF=null;                 /* {x,y,w,h} 渲染视口（GL 坐标，原点左下） */
function applyAspect(){
  const box=el('aspBox'), frame=el('aspFrame');
  if((ASP!=='169'&&ASP!=='916')||window.__frameMode){
    /* 自由比例 / 视频逐帧页：整窗渲染（视频页窗口本身就是成片尺寸，再裁一圈会带黑边） */
    box.style.display='none';
    CUTF=null;
    renderer.setScissorTest(false);
    renderer.setViewport(0,0,innerWidth,innerHeight);
    camera.aspect=innerWidth/Math.max(1,innerHeight);
    camera.updateProjectionMatrix();
    return;
  }
  box.style.display='block';
  const aw=ASP==='916'?9:16, ah=ASP==='916'?16:9;
  const mw=innerWidth-32,mh=innerHeight-32;
  let w=Math.min(mw,mh*aw/ah), h=w*ah/aw;
  if(h>mh){h=mh;w=h*aw/ah;}
  w=Math.round(w);h=Math.round(h);
  frame.style.width=w+'px';frame.style.height=h+'px';
  /* CUTF 用 CSS 语义（y = 距顶），标签定位和视口两处都要用；
     WebGL 的 y 原点在左下，setViewport 时再换算 */
  const x=Math.round((innerWidth-w)/2), ty=Math.round((innerHeight-h)/2);
  CUTF={x:x,y:ty,w:w,h:h};
  camera.aspect=w/h;
  camera.updateProjectionMatrix();
  renderer.setScissorTest(true);
  renderer.setViewport(x,innerHeight-ty-h,w,h);
  renderer.setScissor(x,innerHeight-ty-h,w,h);
  document.querySelectorAll('.camAsp button').forEach(b=>
    b.classList.toggle('on',b.dataset.a===ASP));
}
function previewAt(t,keepCam){
  const s=shotAt(SHOTS,t);
  el('camPlayV').textContent = s
    ? '播放头 '+(+t).toFixed(2)+' s · az '+azN(s.az).toFixed(0)+'° el '+s.el.toFixed(0)+'° r '+s.r.toFixed(0)+' m'
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
    '"><span class="camGo" data-i="'+i+'" title="跳到这一帧预览">az '+azN(s.az).toFixed(0)+
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
  if(window.__expHint)window.__expHint();      /* 时长 / 分镜变了，导出提示跟着更新 */
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
/* ---------- 运镜模板：由轨迹 / 途径点自动生成分镜 ---------- */
function camAtPos(pos,tgt,fov){
  const d=pos.clone().sub(tgt), r=d.length()||1;
  const el=Math.asin(Math.max(-1,Math.min(1,d.y/r)))*180/Math.PI;
  return {az:+(((Math.atan2(d.x,d.z)*180/Math.PI)+360)%360).toFixed(1),
          el:+Math.max(3,Math.min(89.5,el)).toFixed(1), r:+r.toFixed(1),
          tx:+tgt.x.toFixed(1), ty:+tgt.y.toFixed(1), tz:+tgt.z.toFixed(1),
          fov:fov||58};
}
/* 取景比例：必须和「画幅」一致，不能用窗口比例。
   画幅选 9:16 / 16:9 时预览框与导出的 camera.aspect 都是 9/16 或 16/9，
   若这里按窗口比例求距离，竖屏导出就会横向出画（反之横屏留白）。 */
function fitAr(){
  return ASP==='916'?9/16:(ASP==='169'?16/9:innerWidth/Math.max(1,innerHeight));
}
/* 二分求「刚好装下」的机位距离：把给定点集投影到临时相机，全部落进 margin 内 */
function fitR(o){
  const t=new THREE.Vector3(o.tx,o.ty,o.tz);
  const cam=new THREE.PerspectiveCamera(o.fov||58,fitAr(),1,300000);
  const a=o.az*Math.PI/180, e=o.el*Math.PI/180, m=o.margin||0.9;
  /* 顶视时 applyCam 会换 up=(0,0,-1)；临时相机不跟着换，
     判定的 roll 就和实际画面差 90°，长条形模型会「算着装下了、实际超框」 */
  if(e>1.551)cam.up.set(0,0,-1);
  let lo=120, hi=60000, best=hi;
  for(let i=0;i<26;i++){
    const r=(lo+hi)/2;
    cam.position.set(t.x+r*Math.cos(e)*Math.sin(a), t.y+r*Math.sin(e), t.z+r*Math.cos(e)*Math.cos(a));
    cam.lookAt(t); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
    let ok=true;
    for(const p of o.pts){
      const v=p.clone().project(cam);
      if(Math.abs(v.x)>m||Math.abs(v.y)>m||v.z>1){ok=false;break;}
    }
    if(ok){best=r;hi=r;}else lo=r;
  }
  return Math.round(best);
}
/* 环绕类模板专用：模型长宽不等时每个方位角需要的距离不同。
   只按起始方位求一次 r，转到「宽边」就会出画（camPoi 原按 az=90 窄边算，转到 60° 差 37%）。
   取一圈 n 个方位的最大值，保证全程装得下。 */
function orbitR(o){
  let r=0, n=o.k||4;
  for(let i=0;i<n;i++){
    const rr=fitR({az:o.az+i*360/n,el:o.el,tx:o.tx,ty:o.ty,tz:o.tz,
                   fov:o.fov,pts:o.pts,margin:o.margin});
    if(rr>r)r=rr;
  }
  return r;
}
function modelPts(){
  const sy=group.scale.y, hw=M.width_m/2, hh=M.height_m/2;
  const top=Math.max(80,(MAX-MIN)*sy);
  const out=[];
  for(const x of [-hw,hw])for(const z of [-hh,hh])for(const y of [0,top])
    out.push(new THREE.Vector3(x,y,z));
  return out;
}
function nearPts(x,z,y,half,h){
  const out=[];
  for(const dx of [-half,half])for(const dz of [-half,half])
    for(const dy of [-h*0.4,h])out.push(new THREE.Vector3(x+dx,y+dy,z+dz));
  return out;
}
function trkYHere(p){return trackY(p,el('gpsMode').checked?2:1);}
/* 环线起点=终点：末位途径点（≥0.98）按起点处理，生长一开始就出现，
   否则「起终点山顶人家」要等整圈画完才冒出来 */
function growT(t){
  return (t===null||t===undefined)?t:(t>=0.98?0:t);
}
/* 标注点不在轨迹上带 t，按最近的采样点折算里程比例，生长动画里跟着一起出现 */
function nearestT(x,z){
  let bt=0, bd=1e18;
  for(let i=0;i<=240;i++){
    const p=sampleAt(i/240).p;
    const d=(p[0]-x)*(p[0]-x)+(p[3]-z)*(p[3]-z);
    if(d<bd){bd=d;bt=i/240;}
  }
  return bt;
}
function globalFit(az,el,m){
  /* 只按 8 个角点求距离会偏保守：靠近相机的那个底角先顶到画面边界，
     整块沙盘就显得很小。乘 0.86 让沙盘撑满，代价是最外沿底角轻微出画。 */
  const r=fitR({az:az,el:el,tx:0,ty:130,tz:0,fov:58,
                pts:modelPts(),margin:m||0.9});
  return Math.round(r*0.86);
}
/* 完整不裁的全局取景：margin 本身就是「留边比例」，再乘 0.86 会让角点 ndc 到 1.05（出画）。
   环绕、定格、收尾这类要求「沙盘完整」的镜头必须用这个。 */
function fitFull(az,el,m){
  return fitR({az:az,el:el,tx:0,ty:130,tz:0,fov:58,
               pts:modelPts(),margin:m||0.96});
}
function useShots(arr,tip){
  /* 模板生成的帧一律标 sm:0（线性插值）：帧距 0.8–2.5 s，靠插值本身就连得上，
     不需要每段再缓入缓出；手调/记录的分镜不带 sm，保持原来的电影感停顿。 */
  SHOTS=arr.map(function(o){return Object.assign({sm:0},o);})
           .sort(function(a,b){return a.t-b.t;});
  DUR=+SHOTS[SHOTS.length-1].t.toFixed(2);
  PLAY=0; syncCamUI(); previewAt(0);
  flashRec(tip);
}
function followShots(o){
  const N=o.n||14, arr=[];
  for(let i=0;i<=N;i++){
    const f=i/N, p=sampleAt(f).p;
    /* 切线用「前一点 → 后一点」：只朝前采样在终点会退化成零向量，
       机位会跑到目标正上方（el≈90°，画面直接朝天） */
    const pa=sampleAt(Math.max(0,f-0.02)).p, pb=sampleAt(Math.min(1,f+0.02)).p;
    let dx=pb[0]-pa[0], dz=pb[3]-pa[3];
    const L=Math.hypot(dx,dz)||1; dx/=L; dz/=L;
    const y=trkYHere(p);
    const pos=new THREE.Vector3(p[0]-dx*o.back, y+o.h, p[3]-dz*o.back);
    const tgt=new THREE.Vector3(p[0]+dx*o.look, y+o.up, p[3]+dz*o.look);
    arr.push(Object.assign({t:+(o.dur*f).toFixed(2)}, camAtPos(pos,tgt,o.fov||58)));
  }
  return arr;
}
/* 1 途径点巡礼：每点「远处进→特写停→再飞下一个」，末段拉远定格全局 */
el('camTour').onclick=function(){
  const list=WP.list.filter(function(w){return w.on;});
  if(!list.length){flashRec('先添加途径点，再生成巡礼');return;}
  const STEP=2.2, arr=[], az0=readCam().az;   /* 从当前实际视角起转，不用面板里的旧值 */
  let t=0;
  list.forEach(function(w,i){
    const p=sampleAt(w.t).p, y=trkYHere(p), fov=58;
    const az=+((az0+i*18)%360).toFixed(1);
    const near=function(){return nearPts(p[0],p[3],y,Math.round(DIAG*0.10),Math.round(DIAG*0.07));};
    /* 远景帧与特写帧各自求距离：特写 el 从 30 降到 22，同一距离会放大画面，
       原来拿 r1*0.72 顶替，陡坡上的点会顶出画框 */
    const r1=fitR({az:az,el:30,tx:p[0],ty:y+18,tz:p[3],fov:fov,pts:near(),margin:0.92});
    const r2=fitR({az:az+10,el:22,tx:p[0],ty:y+12,tz:p[3],fov:fov,pts:near(),margin:0.92});
    arr.push({t:+t.toFixed(2),az:az,el:30,r:r1,tx:p[0],ty:y+18,tz:p[3],fov:fov});
    arr.push({t:+(t+STEP*0.62).toFixed(2),az:+(az+10).toFixed(1),el:22,r:r2,tx:p[0],ty:y+12,tz:p[3],fov:fov});
    arr.push({t:+(t+STEP).toFixed(2),az:+(az+10).toFixed(1),el:22,r:r2,tx:p[0],ty:y+12,tz:p[3],fov:fov});
    t+=STEP;
  });
  /* 定格帧的 el 必须和求距离时一致：原来按 30° 求 r、却按 34° 拍，沙盘会小一圈；
     再用 fitFull 而不是 globalFit 的 0.86，收尾时沙盘完整不出框 */
  const R=fitFull(138,34,0.96);
  arr.push({t:+(t+1.6).toFixed(2),az:138,el:34,r:R,tx:0,ty:130,tz:0,fov:58});
  arr.push({t:+(t+3.4).toFixed(2),az:142,el:34,r:R,tx:0,ty:130,tz:0,fov:58});
  useShots(arr,'途径点巡礼 · '+list.length+' 点 · '+(t+3.4).toFixed(1)+' s');
};
/* 2 沿线跟拍 */
el('camFollow').onclick=function(){
  const d=DIAG;
  useShots(followShots({n:16,dur:14,back:Math.round(d*0.34),h:Math.round(d*0.20),
    look:Math.round(d*0.10),up:Math.round(d*0.02)}),'沿线跟拍 · 14 s');
};
/* 3 环绕展示（首尾同角，接得上） */
el('camSpin').onclick=function(){
  /* 整圈取 4 个方位的最大距离（只按 40° 求一次，转到窄边会空、转到宽边会裁角）；
     margin 0.96 直接当留边用，不再乘 0.86 —— 乘了角点就会顶到 1.05 出画 */
  const R=orbitR({az:40,el:34,tx:0,ty:130,tz:0,fov:58,pts:modelPts(),margin:0.96});
  const arr=[];
  for(let k=0;k<=4;k++)arr.push({t:+(10*k/4).toFixed(2),az:+(40+k*90).toFixed(1),
    el:34,r:R,tx:0,ty:130,tz:0,fov:58});
  useShots(arr,'环绕展示 · 10 s（首尾可接）');
};
/* 4 高程跟随：贴地随坡，看爬升 */
el('camRide').onclick=function(){
  const d=DIAG;
  useShots(followShots({n:20,dur:16,back:Math.round(d*0.23),h:Math.round(d*0.11),
    look:Math.round(d*0.12),up:0}),'高程跟随 · 16 s');
};
/* 5 俯冲进场 */
el('camDive').onclick=function(){
  const R=globalFit(140,52), p=sampleAt(0.02).p, y=trkYHere(p);
  const r2=fitR({az:120,el:12,tx:p[0],ty:y+8,tz:p[3],fov:58,
                 pts:nearPts(p[0],p[3],y,Math.round(DIAG*0.14),Math.round(DIAG*0.09)),margin:0.88});
  useShots([
    {t:0,az:140,el:52,r:Math.round(R*1.5),tx:0,ty:130,tz:0,fov:58},
    {t:1.4,az:136,el:46,r:Math.round(R*0.9),tx:Math.round(p[0]*0.5),ty:y+60,tz:Math.round(p[3]*0.5),fov:58},
    {t:3.2,az:124,el:16,r:Math.round(r2*1.6),tx:p[0],ty:y+10,tz:p[3],fov:58},
    {t:4.2,az:120,el:12,r:r2,tx:p[0],ty:y+8,tz:p[3],fov:58}
  ],'俯冲进场 · 4.2 s');
};
/* 6 拉升退场 */
el('camPull').onclick=function(){
  const R=fitFull(140,42,0.96), p=sampleAt(0.98).p, y=trkYHere(p);
  const r1=fitR({az:300,el:14,tx:p[0],ty:y+8,tz:p[3],fov:58,
                 pts:nearPts(p[0],p[3],y,Math.round(DIAG*0.14),Math.round(DIAG*0.09)),margin:0.88});
  useShots([
    {t:0,az:300,el:14,r:r1,tx:p[0],ty:y+8,tz:p[3],fov:58},
    {t:1.6,az:330,el:22,r:Math.round(r1*1.5),tx:Math.round(p[0]*0.6),ty:y+70,tz:Math.round(p[3]*0.6),fov:58},
    /* 500°≡140°：接着正向再转 170°，不要写成 140 让插值倒退 190° 绕大圈 */
    {t:4.0,az:500,el:42,r:R,tx:0,ty:130,tz:0,fov:58},
    {t:5.0,az:502,el:42,r:R,tx:0,ty:130,tz:0,fov:58}
  ],'拉升退场 · 5 s');
};
/* 7 俯视平面 */
el('camTop').onclick=function(){
  const R=fitR({az:180,el:89,tx:0,ty:0,tz:0,fov:58,pts:modelPts(),margin:0.86});
  /* 两帧同参数会变成 2.6 s 完全静止的死画面（看着像卡住），留 7% 缓慢推进 */
  useShots([
    {t:0,az:180,el:89,r:Math.round(R*1.07),tx:0,ty:0,tz:0,fov:58},
    {t:2.6,az:180,el:89,r:R,tx:0,ty:0,tz:0,fov:58}
  ],'俯视平面 · 2.6 s');
};
/* 8 单点环绕：取离当前取景中心最近的途径点 */
el('camPoi').onclick=function(){
  const list=WP.list.filter(function(w){return w.on;});
  if(!list.length){flashRec('先添加途径点，再生成单点环绕');return;}
  /* 用相机的真实取景中心：拖动视角不会回写 CAM，原来按 CAM.tx 选点会选到旧位置 */
  const cur=readCam();
  let best=list[0], bd=1e12;
  for(const w of list){
    const p=sampleAt(w.t).p;
    const d=Math.hypot(p[0]-cur.tx,p[3]-cur.tz);
    if(d<bd){bd=d;best=w;}
  }
  const p=sampleAt(best.t).p, y=trkYHere(p);
  const pts=nearPts(p[0],p[3],y,Math.round(DIAG*0.16),Math.round(DIAG*0.11));
  /* 绕一圈要对 4 个方位求最大距离：只按 az=90（窄边）算，转到 60° 时画面横向差 37% */
  const R=orbitR({az:60,el:26,tx:p[0],ty:y+15,tz:p[3],fov:58,pts:pts,margin:0.92});
  const arr=[];
  for(let k=0;k<=4;k++)arr.push({t:+(5*k/4).toFixed(2),az:+(60+k*90).toFixed(1),
    el:26,r:R,tx:p[0],ty:y+15,tz:p[3],fov:58});
  useShots(arr,'单点环绕 · '+best.name+' · 5 s');
};
/* 轨迹生长：勾选即生效，页面播放与逐帧渲染共用同一组状态 */
el('growOn').onchange=function(e){
  GRW.on=e.target.checked;saveGrow();saveStyle();syncGrow();
};
el('growS').oninput=function(e){
  const v=+e.target.value;if(!isFinite(v))return;
  GRW.s=Math.max(0,Math.min(59,v));saveGrow();saveStyle();syncGrow();
};
el('growD').oninput=function(e){
  const v=+e.target.value;if(!isFinite(v))return;
  GRW.d=Math.max(.2,Math.min(30,v));saveGrow();saveStyle();syncGrow();
};
el('growOn').checked=GRW.on;
el('growS').value=GRW.s; el('growD').value=GRW.d;

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
  /* 切换前先量一次「旧画幅下的全局距离」：用它判断当前机位/分镜是中远景还是特写。
     9:16 的横向视野只有 16:9 的 1/3，沿用旧 r 模型必然左右出框。 */
  const gOld=globalFit(CAM.az,CAM.el,0.9);
  ASP=b.dataset.a;
  applyAspect();
  const isWide=function(v){return v>gOld*0.55;};   /* 中远景：跟着画幅重算；特写保持 */
  let touched=false;
  if(isWide(CAM.r))CAM.r=globalFit(CAM.az,CAM.el,0.9);
  if(SHOTS.length){
    SHOTS=SHOTS.map(function(s){
      if(!isWide(s.r))return s;
      touched=true;
      return Object.assign({},s,{r:globalFit(s.az,s.el,0.9)});
    });
  }
  syncCamUI();
  if(SHOTS.length)previewAt(PLAY);
  else{if(!TOP)applyCam(CAM);}
  if(touched)flashRec('分镜已按 '+(ASP==='916'?'9:16':(ASP==='169'?'16:9':'窗口比例'))+' 重算远景距离');
});
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
/* 镜头模板自检：按当前画幅逐帧投影，统计「模型 8 角点 / 轨迹采样点」各有几个落在画面内。
   corner=8 说明沙盘完整，corner 掉说明顶出画框；trk=0 说明这一帧根本看不到轨迹。
   自动化脚本用它验证每个模板在 16:9 与 9:16 下都不出画。 */
window.__camAudit=function(step){
  if(!SHOTS.length)return {error:'no shots'};
  const ar=fitAr(), cam=new THREE.PerspectiveCamera(58,ar,1,300000);
  const corners=modelPts(), trk=[];
  for(let i=0;i<=60;i++){const p=sampleAt(i/60).p;trk.push(new THREE.Vector3(p[0],trkYHere(p),p[3]));}
  const rows=[];step=step||0.1;
  for(let t=0;t<=DUR+1e-6;t+=step){
    const s=shotAt(SHOTS,Math.min(t,DUR));
    if(!s)break;
    const a=s.az*Math.PI/180, e=s.el*Math.PI/180;
    cam.fov=s.fov||58;cam.aspect=ar;cam.up.set(0,1,0);
    cam.position.set(s.tx+s.r*Math.cos(e)*Math.sin(a),
                     s.ty+s.r*Math.sin(e),
                     s.tz+s.r*Math.cos(e)*Math.cos(a));
    if(e>1.551)cam.up.set(0,0,-1);
    cam.lookAt(new THREE.Vector3(s.tx,s.ty,s.tz));
    cam.updateMatrixWorld();cam.updateProjectionMatrix();
    let ci=0,ti=0,nan=false,out=[];
    for(const p of corners){
      const v=p.clone().project(cam);
      if(Math.abs(v.x)<=1&&Math.abs(v.y)<=1&&v.z<=1)ci++;
      else if(out.length<3)out.push([+v.x.toFixed(2),+v.y.toFixed(2),+v.z.toFixed(2)]);
    }
    for(const p of trk){const v=p.clone().project(cam);
      if(Math.abs(v.x)<=1&&Math.abs(v.y)<=1&&v.z<=1)ti++;}
    for(const k of ['az','el','r','tx','ty','tz','fov'])if(!isFinite(s[k]))nan=true;
    rows.push({t:+t.toFixed(2),az:+azN(s.az).toFixed(0),el:+s.el.toFixed(0),
      r:Math.round(s.r),corner:ci,trk:ti,nan:nan,out:out});
  }
  const pick=function(k,fn){return rows.reduce(function(a,b){return fn(a,b[k])?a:b[k];},rows[0][k]);};
  return {ar:+ar.toFixed(4),dur:DUR,shots:SHOTS.length,sm:SHOTS[0].sm,
          minCorner:Math.min.apply(null,rows.map(r=>r.corner)),
          minTrk:Math.min.apply(null,rows.map(r=>r.trk)),
          maxTrk:Math.max.apply(null,rows.map(r=>r.trk)),
          nan:rows.filter(r=>r.nan).length,
          rMin:pick('r',(a,b)=>b<a), rMax:pick('r',(a,b)=>b>a),
          rows:rows};
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
const WP = {size:WDEF.size, fs:WDEF.fs, label:WDEF.label, occ:false, bold:false, list:[]};
try{
  const sw=JSON.parse(localStorage.getItem(WKEY)||'null');
  if(sw && Array.isArray(sw.list)){
    WP.list=sw.list.map(cleanWP).filter(Boolean);
    if(isFinite(+sw.size))WP.size=Math.min(30,Math.max(4,+sw.size));
    if(isFinite(+sw.fs))WP.fs=Math.min(26,Math.max(9,+sw.fs));
    if(typeof sw.label==='boolean')WP.label=sw.label;
    if(typeof sw.occ==='boolean')WP.occ=sw.occ;
    if(typeof sw.bold==='boolean')WP.bold=sw.bold;
  }
}catch(e){}
if(QP.has('wp')){
  WP.list=wpUnpack(QP.get('wp'));       /* 空串=明确没有途径点，覆盖浏览器记忆 */
  if(QP.has('wpsz')){const n=parseFloat(QP.get('wpsz'));
    if(isFinite(n))WP.size=Math.min(30,Math.max(4,n));}
  if(QP.has('wpfs')){const n=parseFloat(QP.get('wpfs'));
    if(isFinite(n))WP.fs=Math.min(26,Math.max(9,n));}
  if(QP.has('wplbl'))WP.label=QP.get('wplbl')!=='0';
  if(QP.has('wpocc'))WP.occ=QP.get('wpocc')!=='0';
  if(QP.has('wpb'))WP.bold=QP.get('wpb')!=='0';
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
/* ---------- 轨迹生长（drawRange 动画）：0=没画到，1=整条都出来 ---------- */
const GROW=[];
const WPMESH=[];              /* 途径点球/杆/环：随生长进度显隐 */
let GROWP=1;                  /* 当前生长进度 0..1，updateLabels 要用 */
const GRW={on:false,s:0,d:4};
try{
  const g=JSON.parse(localStorage.getItem('terrain_grow_v1')||'null');
  if(g){GRW.on=!!g.on;
    if(isFinite(+g.s))GRW.s=Math.max(0,Math.min(59,+g.s));
    if(isFinite(+g.d))GRW.d=Math.max(.2,Math.min(30,+g.d));}
}catch(e){}
if(QP.has('grow'))GRW.on=QP.get('grow')!=='0';
if(QP.has('grows')){const n=parseFloat(QP.get('grows'));
  if(isFinite(n))GRW.s=Math.max(0,Math.min(59,n));}
if(QP.has('growd')){const n=parseFloat(QP.get('growd'));
  if(isFinite(n))GRW.d=Math.max(.2,Math.min(30,n));}
function saveGrow(){try{localStorage.setItem('terrain_grow_v1',JSON.stringify(GRW));}catch(e){}}
function registerGrow(mesh){
  const g=mesh.geometry;
  if(!g||!g.attributes||!g.attributes.position)return;
  GROW.push({g:g,n:(g.index?g.index.count:g.attributes.position.count),m:mesh});
}
function setGrow(p){
  const q=Math.max(0,Math.min(1,p));
  GROWP=q;
  for(const o of GROW){
    o.m.visible=q>0.0005;
    o.g.setDrawRange(0,Math.floor(o.n*q));
  }
  /* 轨迹还没长到的途径点，球/杆/环先不画，不会出现「悬空地名」 */
  for(const o of WPMESH)o.m.visible=(o.t<=q+0.004);
}
function growP(sec){
  if(!GRW.on)return 1;
  return Math.max(0,Math.min(1,(sec-GRW.s)/Math.max(.1,GRW.d)));
}
/* 逐帧渲染时由 video.py 的 __setFrame 直接给时刻，页面 rAF 循环（PLAY 恒为 0）
   会和它抢着改 drawRange，把轨迹整体抹掉，所以这里让位 */
function syncGrow(){ if(window.__frameMode||window.__exporting)return; if(GROW.length)setGrow(growP(PLAY)); }
window.__applyGrowAt=function(sec){ if(GROW.length)setGrow(growP(sec)); };
window.__previewAt=function(t){ PLAY=Math.max(0,Math.min(DUR,+t||0)); previewAt(PLAY); };
window.__shotsProbe=function(){ return {dur:DUR,shots:SHOTS.length,
  first:SHOTS[0]||null,last:SHOTS[SHOTS.length-1]||null}; };
window.__growProbe=function(){
  return {on:GRW.on,s:GRW.s,d:GRW.d,parts:GROW.length,
          drawn:GROW.map(function(o){return o.g.drawRange.count;})};
};
function styleParams(){
  return 'art='+(ART?'art':'sat')+'&pal='+PAL
       +'&trkw='+TS.w+'&trkc='+TS.c+'&trkhl='+TS.hl+'&trkhc='+TS.hc
       +'&trkop='+TS.op+'&trklift='+TS.lift
       +'&trkx='+(TS.xr?1:0)+'&trkauto='+(TS.auto?1:0)+'&trkshadow='+(TS.shadow?1:0)
       +'&wp='+encodeURIComponent(wpPack())+'&wpsz='+WP.size+'&wpfs='+WP.fs
       +(WP.label?'':'&wplbl=0')+(WP.occ?'&wpocc=1':'')+(WP.bold?'&wpb=1':'')
       +'&cam='+packCam(CAM)+'&shots='+packShots(SHOTS)
       +'&dur='+DUR+'&asp='+ASP
       +'&ex='+(+group.scale.y).toFixed(1)
       +'&ends='+(el('showEnds').checked?1:0)
       +'&mk='+(el('showMk').checked?1:0)
       +'&grow='+(GRW.on?1:0)+'&grows='+GRW.s+'&growd='+GRW.d;
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
 ['贴合校验', 'corr='+TM.corr+' · RMSE '+TM.rmse+' m（高程 vs DEM）']
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
  el('wpOcc').checked=WP.occ;
  el('wpBold').checked=WP.bold;
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
    ball.userData.wpBall=true;trackGroup.add(ball);WPMESH.push({m:ball,t:growT(o.t)});
    const pole=new THREE.Mesh(new THREE.CylinderGeometry(WP.size*.16,WP.size*.16,WP.size*1.3,8),mat);
    pole.position.set(p[0],y+WP.size*.65,p[3]);pole.renderOrder=12;trackGroup.add(pole);
    WPMESH.push({m:pole,t:growT(o.t)});
    const ring=new THREE.Mesh(new THREE.TorusGeometry(WP.size*1.7,WP.size*.17,8,24),
      new THREE.MeshBasicMaterial({color:col,side:THREE.DoubleSide,depthTest:!xray}));
    ring.rotation.x=-Math.PI/2;ring.position.set(p[0],y+2,p[3]);
    ring.renderOrder=12;trackGroup.add(ring);WPMESH.push({m:ring,t:growT(o.t)});
    const d=label(o.name||('P'+(i+1)),p[0],y+WP.size*2.7,p[3],'wp',growT(o.t));
    d.style.borderColor='#'+o.color;d.style.color='#'+o.color;
    d.style.fontSize=WP.fs+'px';
    if(WP.bold)d.classList.add('bold');
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
/* 标签透视开关：关掉后被山体挡住的标签直接不画（滑杆拖动时也立即生效） */
el('wpOcc').onchange=function(e){WP.occ=e.target.checked;saveWP();saveStyle();updateLabels();};
/* 加粗只改 class，不重建球和杆，拖动观察时不会闪 */
function applyWPBold(){
  document.querySelectorAll('.lbl.wp').forEach(function(e){
    e.classList.toggle('bold',WP.bold);});
}
el('wpBold').onchange=function(e){
  WP.bold=e.target.checked;saveWP();saveStyle();applyWPBold();
  if(WP.bold)updateLabels();          /* 变宽后重新排一遍避让 */};
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
  return {count:WP.list.length,size:WP.size,fs:WP.fs,label:WP.label,occ:WP.occ,
    list:WP.list.map(function(o){return {t:+o.t.toFixed(4),name:o.name,
      color:o.color,on:o.on};}),
    spheres:trackGroup.children.filter(function(o){
      return o.userData&&o.userData.wpBall;}).length,
    wpLabels:labels.filter(function(o){return o.kind==='wp';}).length,
    wpLabelsShown:labels.filter(function(o){
      return o.kind==='wp'&&o.el.style.display!=='none'
        &&o.el.style.opacity!=='0';}).length,
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
function label(text, x, y, z, cls, t){
  const d=document.createElement('div');
  d.className='lbl'+(cls?' '+cls:''); d.textContent=text;
  document.body.appendChild(d);
  labels.push({p:new THREE.Vector3(x,y,z),el:d,base:y,t:(t===undefined?null:t)});
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
  GROW.length=0; WPMESH.length=0;
}
/* 两步路 GPS 高频采样会出现连续同一坐标（本次 285 点里有 47 个相邻重复）。
   CatmullRomCurve3 在零长度线段上算切向得到零向量，TubeGeometry 顶点变 NaN，
   整条轨迹管会静默消失（画面上只剩贴地影带，不报错）。建曲线前先按 0.5 m 去重。 */
function dedupeSeg(seg){
  if(!seg||!seg.length)return [];
  const out=[seg[0]];
  for(let i=1;i<seg.length;i++){
    const a=out[out.length-1], b=seg[i];
    if(Math.hypot(b[0]-a[0],b[3]-a[3])>=0.5) out.push(b);
  }
  return out;
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
      trackGroup.add(band); registerGrow(band);
    }
  }
  for(const raw of TK.segments){
    const seg=dedupeSeg(raw);
    if(seg.length<3) continue;
    /* 环线：首末重合就走闭合曲线（显式 closed=true 才不会有接缝处的反向切向） */
    const closed=Math.hypot(seg[0][0]-seg[seg.length-1][0],
                            seg[0][3]-seg[seg.length-1][3])<2.5;
    const pts=closed?seg.slice(0,-1):seg;
    const v=pts.map(p=>new THREE.Vector3(p[0],trackY(p,yi),p[3]));
    const curve=new THREE.CatmullRomCurve3(v,closed,'centripetal');
    if(haloMat && !xray){
      const halo=new THREE.Mesh(new THREE.TubeGeometry(curve,v.length,r*hl,6,closed),haloMat);
      halo.renderOrder=1; trackGroup.add(halo); registerGrow(halo);
    }
    const tube=new THREE.Mesh(new THREE.TubeGeometry(curve,v.length,r,8,closed),tubeMat);
    tube.renderOrder=xray?11:2; trackGroup.add(tube); registerGrow(tube);
  }
  // 起终点 / 进入离开建模区 / 航点（起终点与标注点分开开关）
  if(el('showEnds').checked){
    for(const e of TK.endpoints){
      const y=markerY(useGps?e.y_gps:e.y_dem);
      const s=new THREE.Mesh(dotGeo,endMat); s.position.set(e.x,y,e.z);
      trackGroup.add(s);
      label(e.label,e.x,y+24,e.z,'end');
    }
  }
  if(el('showMk').checked){
    for(const m of TK.markers){
      const y=markerY(useGps?m.y_gps:m.y_dem);
      const s=new THREE.Mesh(dotGeo,dotMat); s.position.set(m.x,y,m.z);
      trackGroup.add(s);
      const mt=nearestT(m.x,m.z);
      WPMESH.push({m:s,t:growT(mt)});
      if(m.name) label(m.name,m.x,y+22,m.z,'',growT(mt));
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
el('showEnds').onchange=()=>buildTrack();
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
  for(const raw of TK.segments){
    const seg=dedupeSeg(raw);
    if(seg.length<3) continue;
    const closed=Math.hypot(seg[0][0]-seg[seg.length-1][0],
                            seg[0][3]-seg[seg.length-1][3])<2.5;
    const pts=closed?seg.slice(0,-1):seg;
    const v=pts.map(p=>new THREE.Vector3(p[0],trackY(p,yi),p[3]));
    g.add(new THREE.Mesh(
      new THREE.TubeGeometry(new THREE.CatmullRomCurve3(v,closed,'centripetal'),v.length,+TS.w,8,closed),
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
/* 标签是否被山体挡住：沿「相机 → 锚点」的视线按 DEM 采样，任一采样点的地表
   高过视线就算挡住。步长 40 m，掠视时 60 m 宽的山脊也不会漏判；末尾 12% 不采样，
   否则贴坡的标签会把自己的落点判成遮挡。垂直夸张要先除回模型空间再比高度。 */
function labelBlocked(w){
  const sy=group.scale.y||1, cam=camera.position;
  const ax=w.x, ay=w.y/sy, az=w.z;
  const cx=cam.x, cy=cam.y/sy, cz=cam.z;
  const dx=ax-cx, dy=ay-cy, dz=az-cz;
  const dist=Math.sqrt(dx*dx+dy*dy+dz*dz)||1;
  const N=Math.min(40,Math.max(10,Math.round(dist/40)));
  const hw=M.width_m/2, hh=M.height_m/2;
  for(let k=2;k<N;k++){
    const t=k/N;
    if(t>0.88)continue;
    const px=cx+dx*t, py=cy+dy*t, pz=cz+dz*t;
    if(Math.abs(px)>hw||Math.abs(pz)>hh)continue;      /* 视线还在模型范围外 */
    if(demAtLocal(px,pz)>py+2.5)return true;           /* 地表高过视线 → 被挡住 */
  }
  return false;
}
function updateLabels(){
  syncGrow();                          /* 轨迹生长：按 PLAY 时刻刷 drawRange */
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
  const sy=group.scale.y, placed=[], cand=[];
  /* 有画幅裁切时标签必须落在框内：原来按整窗换算，竖屏下标签会跑到框外 */
  const VX=CUTF?CUTF.x:0, VY=CUTF?CUTF.y:0,
        VW=CUTF?CUTF.w:innerWidth, VH=CUTF?CUTF.h:innerHeight;
  for(const o of labels){
    const w=o.p.clone(); w.y*=sy;                    /* 标签锚点的世界坐标 */
    const v=w.clone().project(camera);
    let vis=v.z<1 && el('showTrk').checked
      && (o.kind==='wp' ? WP.label
          : (o.el.classList.contains('end') ? el('showEnds').checked
                                            : el('showMk').checked));
    /* 透视默认关闭：被山体挡住的标签不画，低角度掠视时不会挤成一团 */
    if(vis && !WP.occ) vis=!labelBlocked(w);
    /* 轨迹生长未到该点的标签先透明（淡入过渡在 CSS 里）：
       避免片头一堆悬空地名，也不影响后续避让计算 */
    let gok=true;
    if(vis && GRW.on && o.t!==null && o.t!==undefined && GROWP<0.999)
      gok = (o.t<=GROWP+0.004);
    o.el.style.display=(vis?'block':'none');
    o.el.style.opacity=(gok?'1':'0');
    if(!vis || !gok)continue;
    cand.push({o:o, v:v, d:w.distanceToSquared(camera.position)});
  }
  /* 近的先占位。撞车的标签最多上移一格，再撞就直接不画——
     以前是循环上移 8 次，低角度时几个标签会叠成一串，看着像挤在一起。 */
  cand.sort((a,b)=>a.d-b.d);
  for(const c of cand){
    const o=c.o, v=c.v;
    let x=VX+(v.x*.5+.5)*VW, y=VY+(-v.y*.5+.5)*VH-6;
    /* 锚点已经出画的标签不必占位，否则会把画面内的标签顶开 */
    if(x<VX-60 || x>VX+VW+60 || y<VY-60 || y>VY+VH+60){
      o.el.style.display='none'; o.el.style.opacity='1'; continue;
    }
    const hw=Math.max(o.el.offsetWidth,60)/2+8;
    for(let k=0;k<2;k++){
      if(!placed.some(p=>Math.abs(p.x-x)<p.hw+hw && Math.abs(p.y-y)<17))break;
      y-=20;
    }
    if(placed.some(p=>Math.abs(p.x-x)<p.hw+hw && Math.abs(p.y-y)<17)){
      o.el.style.display='none'; continue;           /* 还是撞：让位给更近的标签 */
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

page = page.replace("</style>", CSS + CAM_CSS + EXP_CSS + "</style>")
page = page.replace("<div class=\"src\" id=\"src\"></div>",
                    "<div class=\"src\" id=\"src\"></div>\n"
                    + TRACK_HTML.replace("__PROFILE__", "<div id=\"profile\"></div>" + STYLE_HTML))
page = page.replace("<label class=\"chk\"><input type=\"checkbox\" id=\"autoRot\">自动旋转</label>",
                    CTRL_HTML + CAM_HTML +
                    "\n<label class=\"chk\"><input type=\"checkbox\" id=\"autoRot\">自动旋转</label>")
page = page.replace('<div class="panel ctrl">', '<div class="panel ctrl">' + EXP_HTML)
page = page.replace("<div class=\"btns\"><button id=\"reset\">重置视角</button></div>",
                    "<div class=\"btns\"><button id=\"focusTrk\">聚焦轨迹</button>"
                    "<button id=\"trkGlb\">导出轨迹 GLB</button></div>\n"
                    "<div class=\"btns\"><button id=\"reset\">重置视角</button></div>")
page = page.replace("el('status').style.display='none';",
                    TRACK_JS.replace("__TRACK__", json.dumps(data, ensure_ascii=False,
                                                             separators=(",", ":")))
                    .replace("__CAM_CORE__", CAM_CORE)
                    .replace("__CAM_UI__", CAM_UI)
                    + EXP_JS
                    + "\nel('status').style.display='none';")
page = page.replace(
    "(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();",
    "(function loop(){requestAnimationFrame(loop);if(window.__exporting)return;"
    # 框外不重绘会留残影：先把整屏清一次，再按画幅视口画（CUTF.y 是距顶，GL 要翻过来）
    "if(CUTF){renderer.setScissorTest(false);"
    "renderer.setViewport(0,0,innerWidth,innerHeight);renderer.clear();"
    "renderer.setScissorTest(true);"
    "renderer.setViewport(CUTF.x,innerHeight-CUTF.y-CUTF.h,CUTF.w,CUTF.h);"
    "renderer.setScissor(CUTF.x,innerHeight-CUTF.y-CUTF.h,CUTF.w,CUTF.h);}"
    "controls.update();updateLabels();renderer.render(scene,camera);})();")
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
              "window.__camProbe", "&shots=", "&cam=",
              "id=\"expVideo\"", "id=\"expRes\"", "window.__exportRun",
              "if(window.__exporting)return;", "expDrawLabels", "#expMask"):
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
