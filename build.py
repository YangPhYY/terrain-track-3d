import json, math, os, io, struct, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HERE = os.environ.get("TERRAIN_HOME") or os.path.dirname(os.path.abspath(__file__))
GEO = r"E:\下载\默认数据表.geojson"
os.makedirs(HERE, exist_ok=True)

PI = 3.1415926535897932384626
A = 6378245.0
EE = 0.00669342162296594323
# 建模窗口 = 徒步轨迹外接矩形 + 每边 BUFFER 缓冲，建模原点平移到轨迹中心。
# 三种口径见 --box=（auto / geo / 显式矩形）；--buffer= 可临时改外扩；--dry-run 只算不建。
# 历史版本：1800×1800 m 正方形 → 蓝线外接+100 m（1428×1106）。
BUFFER = 80.0
GRID_M = 18.0         # 目标格距（m）；NX/NY 按新窗口尺寸动态取整
Z_DEM, Z_SAT = 13, 16
BASE = -22.0        # 沙盘底座厚度：插画风要薄侧壁，旧值 -60 会形成大块深色悬崖

# ---- 卫星影像源（命令行可覆盖，默认保持现状）----
#   python3 build.py                               默认 Esri，清晰度最高（仅本机/自用）
#   python3 build.py --sat=tianditu                天地图 img_w（合规，可对外发布）
#   python3 build.py --sat=esri --z-sat=18         Esri 提到 z18
#   python3 build.py --sat=tianditu --sat-only     只拉贴图自检，不改任何模型文件
SAT_SOURCE = "esri"                  # esri | tianditu
TK_FILE = r"D:\agent\svg3d\tk.txt"   # 天地图 tk：仅本机读取，绝不写入页面/模型
SAT_ONLY = False
WIN_BOX = "auto"      # auto=轨迹外接框 | geo=区域轮廓外接框 | 显式 "minlon,minlat,maxlon,maxlat"
DRY_RUN = False       # 只算窗口并打印，不拉瓦片、不写任何文件
GEO_ARG = None
for _arg in sys.argv[1:]:
    if _arg.startswith("--sat="):
        SAT_SOURCE = _arg.split("=", 1)[1].lower()
    elif _arg.startswith("--z-sat="):
        Z_SAT = int(_arg.split("=", 1)[1])
    elif _arg.startswith("--buffer="):
        BUFFER = float(_arg.split("=", 1)[1])
        if BUFFER < 0:
            raise SystemExit("--buffer 不能为负")
    elif _arg.startswith("--box="):
        WIN_BOX = _arg.split("=", 1)[1].strip()
    elif _arg.startswith("--geo="):
        GEO_ARG = _arg.split("=", 1)[1].strip()
    elif _arg == "--dry-run":
        DRY_RUN = True
    elif _arg == "--sat-only":
        SAT_ONLY = True
    else:
        raise SystemExit("unknown argument: " + _arg)
if SAT_SOURCE not in ("esri", "tianditu"):
    raise SystemExit("SAT_SOURCE must be esri or tianditu")


def _tlat(x, y):
    r = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    r += (20*math.sin(6*x*PI) + 20*math.sin(2*x*PI))*2/3
    r += (20*math.sin(y*PI) + 40*math.sin(y/3*PI))*2/3
    r += (160*math.sin(y/12*PI) + 320*math.sin(y*PI/30))*2/3
    return r


def _tlon(x, y):
    r = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    r += (20*math.sin(6*x*PI) + 20*math.sin(2*x*PI))*2/3
    r += (20*math.sin(x*PI) + 40*math.sin(x/3*PI))*2/3
    r += (150*math.sin(x/12*PI) + 300*math.sin(x/30*PI))*2/3
    return r


def wgs2gcj(lon, lat):
    dlat, dlon = _tlat(lon-105, lat-35), _tlon(lon-105, lat-35)
    rl = lat/180*PI
    m = 1 - EE*math.sin(rl)**2
    s = math.sqrt(m)
    return (lon + (dlon*180)/(A/s*math.cos(rl)*PI),
            lat + (dlat*180)/((A*(1-EE))/(m*s)*PI))


def gcj2wgs(lon, lat):
    a, b = lon, lat
    for _ in range(6):
        c, d = wgs2gcj(a, b)
        a, b = a-(c-lon), b-(d-lat)
    return a, b


def ll_pix(lon, lat, z):
    n = 2**z
    return ((lon+180)/360*n*256,
            (1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n*256)


def fetch(url):
    # 天地图 CloudWAF 会拒绝无 User-Agent 的请求（403），必须带浏览器 UA
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "--max-time", "40", "-A", UA, url],
                           capture_output=True)
        if r.stdout and r.stdout[:2] in (b"\x89P", b"\xff\xd8"):
            return r.stdout
        time.sleep(0.4*(attempt+1))
    # 不打印 query，避免把 tk 写进日志
    raise RuntimeError("bad tile after 3 attempts: " + url.split("?")[0] +
                       "  [check tk / UA / zoom coverage]")


def tile_mosaic(url_fmt, box, z, mode):
    p0 = ll_pix(box[0], box[3], z)
    p1 = ll_pix(box[2], box[1], z)
    x0, y0 = math.floor(p0[0]/256), math.floor(p0[1]/256)
    x1, y1 = math.floor(p1[0]/256), math.floor(p1[1]/256)
    jobs = [(tx, ty) for ty in range(y0, y1+1) for tx in range(x0, x1+1)]
    urls = [url_fmt.format(z=z, x=tx, y=ty, host=(tx+ty) % 8) for tx, ty in jobs]
    _workers = 4 if (mode == "satellite" and SAT_SOURCE == "tianditu") else 8
    with ThreadPoolExecutor(max_workers=_workers) as ex:
        raws = list(ex.map(fetch, urls))
    img = Image.new("RGB", ((x1-x0+1)*256, (y1-y0+1)*256))
    for (tx, ty), raw in zip(jobs, raws):
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.size != (256, 256):
            raise RuntimeError("unexpected tile size %s at z%d (x=%d y=%d)" %
                               (im.size, z, tx, ty))
        img.paste(im, ((tx-x0)*256, (ty-y0)*256))
    print("tiles z%d %dx%d = %d -> %s %dx%d" %
          (z, x1-x0+1, y1-y0+1, len(jobs), mode, img.width, img.height))
    return img, x0*256, y0*256


# 区域轮廓 GeoJSON 是**可选**的：只用来在模型上画蓝/橙边界线。
# 只给轨迹 KML 时跳过它，窗口完全由轨迹决定。
GEO = GEO_ARG or GEO
if GEO and os.path.isfile(GEO):
    geo = json.load(open(GEO, encoding="utf-8"))
    raw = geo["features"][0]["geometry"]["coordinates"]
    raw_poly = raw[:-1] if math.dist(raw[0], raw[-1]) < 1e-5 else raw[:]
    wgs_poly = [gcj2wgs(lon, lat) for lon, lat in raw_poly]
    lons = [p[0] for p in wgs_poly]
    lats = [p[1] for p in wgs_poly]
    print("outline: %s (%d points)" % (GEO, len(raw_poly)))
else:
    if WIN_BOX == "geo":
        raise SystemExit("--box=geo 需要可读的区域 GeoJSON，但没找到: %s" % GEO)
    raw_poly, wgs_poly, lons, lats = [], [], [], []
    print("outline: none（未提供 GeoJSON，跳过边界线，窗口只按轨迹定）")

# 建模区原点平移到「徒步轨迹外接框中心」。轨迹 WGS84 原始经纬度取自 track.json
# 的 segments[i][j][4],[5]（KML 原始坐标，与建模 bbox 无关，不构成循环依赖）。
_tj = json.load(open(os.path.join(HERE, "track.json"), encoding="utf-8"))
_tpts = [p for seg in _tj["segments"] for p in seg]
if len(_tpts) < 100:
    raise SystemExit("track.json 点数异常：%d" % len(_tpts))
_tlo = [p[4] for p in _tpts]
_tla = [p[5] for p in _tpts]
TRACK_BOX = [min(_tlo), min(_tla), max(_tlo), max(_tla)]

# ---- 窗口三种口径（--box= / --buffer=）----
#   auto（默认）：轨迹外接框 + BUFFER/边 —— 贴着轨迹裁，主体最大
#   geo         ：区域轮廓外接框 + BUFFER/边 —— 保住整块地，轨迹偏在一边
#   minlon,...  ：直接给 WGS84 矩形 —— 圈住整座山或人工微调
if WIN_BOX == "auto":
    lon0, lat0 = (TRACK_BOX[0]+TRACK_BOX[2])/2, (TRACK_BOX[1]+TRACK_BOX[3])/2
    rad = math.radians(lat0)
    M_LAT = 111132.92 - 559.82*math.cos(2*rad) + 1.175*math.cos(4*rad)
    M_LON = 111412.84*math.cos(rad) - 93.5*math.cos(3*rad)
    dlo, dla = BUFFER/M_LON, BUFFER/M_LAT
    box = [TRACK_BOX[0]-dlo, TRACK_BOX[1]-dla,
           TRACK_BOX[2]+dlo, TRACK_BOX[3]+dla]
    CROP_NOTE = "track bbox + %.0f m/edge" % BUFFER
elif WIN_BOX == "geo":
    _gb = [min(lons), min(lats), max(lons), max(lats)]
    lon0, lat0 = (_gb[0]+_gb[2])/2, (_gb[1]+_gb[3])/2
    rad = math.radians(lat0)
    M_LAT = 111132.92 - 559.82*math.cos(2*rad) + 1.175*math.cos(4*rad)
    M_LON = 111412.84*math.cos(rad) - 93.5*math.cos(3*rad)
    dlo, dla = BUFFER/M_LON, BUFFER/M_LAT
    box = [_gb[0]-dlo, _gb[1]-dla, _gb[2]+dlo, _gb[3]+dla]
    CROP_NOTE = "geo outline bbox + %.0f m/edge" % BUFFER
else:
    _v = [float(x) for x in WIN_BOX.split(",")]
    if len(_v) != 4:
        raise SystemExit("--box 需要 4 个数：minlon,minlat,maxlon,maxlat")
    box = _v
    lon0, lat0 = (box[0]+box[2])/2, (box[1]+box[3])/2
    rad = math.radians(lat0)
    M_LAT = 111132.92 - 559.82*math.cos(2*rad) + 1.175*math.cos(4*rad)
    M_LON = 111412.84*math.cos(rad) - 93.5*math.cos(3*rad)
    CROP_NOTE = "explicit box"
WIDTH = (box[2]-box[0])*M_LON
HEIGHT = (box[3]-box[1])*M_LAT
NX, NY = max(8, round(WIDTH/GRID_M)), max(8, round(HEIGHT/GRID_M))
_cov = sum(1 for p in _tpts if box[0] <= p[4] <= box[2] and box[1] <= p[5] <= box[3])
print("track bbox %.1f x %.1f m -> window %.1f x %.1f m "
      "(%s, grid %dx%d, cell %.1f x %.1f m, track in window %.1f%%)" %
      ((TRACK_BOX[2]-TRACK_BOX[0])*M_LON, (TRACK_BOX[3]-TRACK_BOX[1])*M_LAT,
       WIDTH, HEIGHT, CROP_NOTE, NX, NY, WIDTH/NX, HEIGHT/NY,
       100.0*_cov/len(_tpts)))
if DRY_RUN:
    print("dry-run: 不拉瓦片、不写文件。确认窗口后去掉 --dry-run 重跑。")
    sys.exit(0)
offset = ((wgs_poly[0][0]-raw_poly[0][0])*M_LON,
          (wgs_poly[0][1]-raw_poly[0][1])*M_LAT) if raw_poly else (0.0, 0.0)

DEM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
if SAT_SOURCE == "tianditu":
    if not os.path.isfile(TK_FILE):
        raise SystemExit("天地图 tk 文件不存在: " + TK_FILE)
    _tk = open(TK_FILE, encoding="utf-8").read().strip()
    if len(_tk) != 32:
        raise SystemExit("tk 格式异常（应为 32 位）")
    if Z_SAT > 18:
        print("WARN: 该区域天地图 img_w 在 z>18 无影像，会返回 1x1 空瓦片")
    SAT_URL = ("https://t{host}.tianditu.gov.cn/DataServer?"
               "T=img_w&x={x}&y={y}&l={z}&tk=" + _tk)
    IMAGERY = "天地图·影像底图 img_w z%d（自然资源部）" % Z_SAT
else:
    SAT_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    IMAGERY = "Esri World Imagery, z%d" % Z_SAT
SAT_QUALITY = 94
print("satellite source=%s z=%d" % (SAT_SOURCE, Z_SAT))
dem_img, dem_px, dem_py = tile_mosaic(DEM_URL, box, Z_DEM, "DEM")
sat_img, sat_px, sat_py = tile_mosaic(SAT_URL, box, Z_SAT, "satellite")
if SAT_ONLY:
    _out = os.path.join(HERE, "test_tdt", "mosaic_%s_z%d.jpg" % (SAT_SOURCE, Z_SAT))
    os.makedirs(os.path.dirname(_out), exist_ok=True)
    sat_img.save(_out, "JPEG", quality=94)
    print("sat-only -> %s (%dx%d), no model file touched" % (_out, sat_img.width, sat_img.height))
    sys.exit(0)
sat_path = os.path.join(HERE, "satellite.jpg")
sat_img.save(sat_path, "JPEG", quality=SAT_QUALITY)
SW, SH = sat_img.width, sat_img.height


def dem_value(px, py):
    r, g, b = dem_img.getpixel((px, py))
    return r*256 + g + b/256 - 32768


def elevation(lon, lat):
    gx, gy = ll_pix(lon, lat, Z_DEM)
    x, y = gx-dem_px, gy-dem_py
    x = max(0.0, min(dem_img.width-1.001, x))
    y = max(0.0, min(dem_img.height-1.001, y))
    x0, y0 = int(x), int(y)
    dx, dy = x-x0, y-y0
    x1, y1 = min(x0+1, dem_img.width-1), min(y0+1, dem_img.height-1)
    a = dem_value(x0, y0)*(1-dx) + dem_value(x1, y0)*dx
    b = dem_value(x0, y1)*(1-dx) + dem_value(x1, y1)*dx
    return a*(1-dy) + b*dy


def uv_of(lon, lat):
    gx, gy = ll_pix(lon, lat, Z_SAT)
    u = (gx-sat_px)/SW
    v = (gy-sat_py)/SH
    return (u, 1-v, v)  # u, three.js v, glTF v


def to_m(lon, lat):
    # three.js 是右手系且 y 必须朝上：x=东、y=高程、-z=北。
    # 若把北直接映射到 +z，整个场景会变成左手系（俯视时东西镜像）。
    return ((lon-lon0)*M_LON, (lat0-lat)*M_LAT)


def to_ll(x, z):
    return (lon0+x/M_LON, lat0-z/M_LAT)


verts, tris, uvs_js, uvs_gltf, elevs = [], [], [], [], []


def add(x, y, z, lon, lat, e):
    i = len(verts)
    verts.append((round(x, 2), round(y, 2), round(z, 2)))
    u = uv_of(lon, lat)
    uvs_js.append((round(u[0], 6), round(u[1], 6)))
    uvs_gltf.append((round(u[0], 6), round(u[2], 6)))
    elevs.append(round(e, 2))
    return i


def idx(i, j):
    # 未被调用的辅助函数。grid 顶点按 i 外层 / j 内层 append，实际顺序为 i*(NY+1)+j
    return i*(NY+1)+j


grid = [[None]*(NY+1) for _ in range(NX+1)]
for i in range(NX+1):
    for j in range(NY+1):
        lon = box[0]+(box[2]-box[0])*i/NX
        lat = box[1]+(box[3]-box[1])*j/NY
        x, z = to_m(lon, lat)
        e = elevation(lon, lat)
        grid[i][j] = add(x, e, z, lon, lat, e)

emin = min(elevs)
emax = max(elevs)
for k, (x, y, z) in enumerate(verts):
    verts[k] = (x, round(y-emin, 2), z)

top_tris = []
for i in range(NX):
    for j in range(NY):
        a, b, c, d = grid[i][j], grid[i+1][j], grid[i+1][j+1], grid[i][j+1]
        top_tris.append((a, b, c))
        top_tris.append((a, c, d))


def ny(t):
    a, b, c = (verts[i] for i in t)
    return (b[2]-a[2])*(c[0]-a[0]) - (b[0]-a[0])*(c[2]-a[2])


def normal(t):
    a, b, c = (verts[i] for i in t)
    u = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
    v = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
    nx, nyy, nz = u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]
    L = math.sqrt(nx*nx+nyy*nyy+nz*nz) or 1
    return (nx/L, nyy/L, nz/L)


top_tris = [(t[0], t[2], t[1]) if ny(t) < 0 else t for t in top_tris]

ring = []
for j in range(NY+1):
    ring.append(grid[0][j])
for i in range(1, NX+1):
    ring.append(grid[i][NY])
for j in range(NY-1, -1, -1):
    ring.append(grid[NX][j])
for i in range(NX-1, 0, -1):
    ring.append(grid[i][0])
R = len(ring)

bot = []
for k in ring:
    x, _, z = verts[k]
    lon, lat = to_ll(x, z)
    bot.append(add(x, BASE, z, lon, lat, emin+BASE))
botc = add(0, BASE, 0, lon0, lat0, emin+BASE)
uvs_js[botc] = (0.5, 0.5)
uvs_gltf[botc] = (0.5, 0.5)

shell = []
for k in range(R):
    j = (k+1) % R
    shell.append((ring[k], bot[k], bot[j]))
    shell.append((ring[k], bot[j], ring[j]))
    shell.append((botc, bot[j], bot[k]))
fixed = []
for k, t in enumerate(shell):
    N = normal(t)
    a, b, c = (verts[i] for i in t)
    fx, fz = (a[0]+b[0]+c[0])/3, (a[2]+b[2]+c[2])/3
    ok = N[1] < 0 if k % 3 == 2 else (N[0]*fx+N[2]*fz > 0)
    fixed.append(t if ok else (t[0], t[2], t[1]))
shell = fixed
tris = top_tris + shell


def boundary_points(poly):
    out = []
    for lon, lat in poly:
        x, z = to_m(lon, lat)
        out.append([round(x, 2), round(elevation(lon, lat)-emin+4, 2), round(z, 2)])
    return out


meta = {"bbox_wgs84": [round(v, 7) for v in box], "center": [round(lon0, 7), round(lat0, 7)],
        "width_m": round(WIDTH, 1), "height_m": round(HEIGHT, 1),
        "expand_m": BUFFER, "half_ew": round(WIDTH/2, 1), "half_ns": round(HEIGHT/2, 1),
        "crop": CROP_NOTE, "base_m": BASE,
        "offset_m": [round(offset[0], 1), round(offset[1], 1)],
        "elev_min": round(emin, 1), "elev_max": round(emax, 1),
        "dem": "Terrarium DEM ~30 m (SRTM/ASTER), z13",
        "imagery": IMAGERY,
        "vertices": len(verts), "triangles": len(tris), "top_triangles": len(top_tris),
        "grid": [NX, NY], "sat_px": [SW, SH],
        "coord": "GCJ02 -> WGS84",
        "axes": "x=East, y=Up, -z=North (right-handed)"}
data = {"meta": meta, "verts": verts, "uv": uvs_js, "elev": elevs,
        "top_tris": top_tris, "shell_tris": shell,
        "boundary_wgs84": boundary_points(wgs_poly),
        "boundary_gcj02": boundary_points(raw_poly)}
open(os.path.join(HERE, "mesh.json"), "w", encoding="utf-8").write(json.dumps(data, separators=(",", ":")))

edges = {}
for t in tris:
    for i in range(3):
        e = tuple(sorted((t[i], t[(i+1) % 3])))
        edges[e] = edges.get(e, 0)+1
bad = sum(1 for c in edges.values() if c != 2)
print("bbox_wgs84=%s  size=%.0f x %.0f m  offset=(%.0f, %.0f) m" %
      (meta["bbox_wgs84"], WIDTH, HEIGHT, offset[0], offset[1]))
print("elevation=%.1f..%.1f m  relief=%.1f m  V=%d T=%d non_manifold=%d" %
      (emin, emax, emax-emin, len(verts), len(tris), bad))

# binary STL
stl = bytearray(struct.pack("<80sI", b"GCJ02-corrected right-handed terrain + imagery, three.js r187", len(tris)))
for t in tris:
    stl += struct.pack("<12fH", *normal(t), *verts[t[0]], *verts[t[1]], *verts[t[2]], 0)
open(os.path.join(HERE, "terrain.stl"), "wb").write(stl)

# GLB with embedded satellite texture
jpg = open(sat_path, "rb").read()
pos, nor, uv, idxb = bytearray(), bytearray(), bytearray(), bytearray()
mins = [float("inf")]*3
maxs = [float("-inf")]*3
for k, t in enumerate(tris):
    N = normal(t)
    for vi in t:
        p = verts[vi]
        pos += struct.pack("<3f", *p)
        nor += struct.pack("<3f", *N)
        uv += struct.pack("<2f", *uvs_gltf[vi])
        for j in range(3):
            mins[j], maxs[j] = min(mins[j], p[j]), max(maxs[j], p[j])
        idxb += struct.pack("<H", 3*k+t.index(vi))
while len(idxb) % 4:
    idxb += b"\x00"
while len(jpg) % 4:
    jpg += b"\x00"
bin_chunk = bytes(pos)+bytes(nor)+bytes(uv)+bytes(idxb)+jpg
count = len(tris)*3
off = [0, len(pos), len(pos)+len(nor), len(pos)+len(nor)+len(uv),
       len(pos)+len(nor)+len(uv)+len(idxb)]
gltf = {"asset": {"version": "2.0", "generator": "geojson-terrain-hd"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "satellite_terrain"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                                    "indices": 3, "material": 0}]}],
        "materials": [{"name": SAT_SOURCE + "_imagery", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                "baseColorFactor": [1, 1, 1, 1],
                                                "metallicFactor": 0, "roughnessFactor": .95}}],
        "textures": [{"sampler": 0, "source": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": 4, "mimeType": "image/jpeg", "name": "satellite"}],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": off[0], "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": off[1], "byteLength": len(nor), "target": 34962},
            {"buffer": 0, "byteOffset": off[2], "byteLength": len(uv), "target": 34962},
            {"buffer": 0, "byteOffset": off[3], "byteLength": count*2, "target": 34963},
            {"buffer": 0, "byteOffset": off[4], "byteLength": len(jpg)}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": count, "type": "VEC3", "min": mins, "max": maxs},
            {"bufferView": 1, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": count, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": count, "type": "SCALAR"}]}
js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
while len(js) % 4:
    js += b" "
glb = bytearray(b"glTF"+struct.pack("<II", 2, 12+8+len(js)+8+len(bin_chunk)))
glb += struct.pack("<I4s", len(js), b"JSON")+js
glb += struct.pack("<I4s", len(bin_chunk), b"BIN\x00")+bin_chunk
open(os.path.join(HERE, "terrain.glb"), "wb").write(glb)

tpl = open(os.path.join(HERE, "index.template.html"), encoding="utf-8").read()
html = tpl.replace("__DATA__", json.dumps(data, separators=(",", ":")))
open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(html)
print("satellite.jpg=%d B  terrain.stl=%d B  terrain.glb=%d B  index.html=%d B" %
      (len(jpg), len(stl), len(glb), len(html)))
