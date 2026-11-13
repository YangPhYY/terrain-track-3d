# -*- coding: utf-8 -*-
"""新区域引导：只从 KML 算轨迹外接框，写一份最小 track.json，供 build.py 定建模窗口。

为什么需要它：build.py 必须读 track.json 的轨迹经纬度来决定窗口，
而 track.py 又必须读 mesh.json 才能贴地 —— 新目录里两者都不存在，会卡死。

用法：
    python3 bootstrap_bbox.py --kml "E:\\下载\\新区域轨迹.kml"
    python3 build.py --sat=esri --z-sat=18
    python3 track.py                      # 用真实窗口重新生成完整 track.json

注意：本脚本写的 track.json 只含经纬度，其余字段是占位；build.py 只用 p[4]/p[5]。
      跑完 track.py 后会被完整数据覆盖，属正常。
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def read_kml(path):
    kml = open(path, encoding="utf-8-sig").read()
    pts = []
    if "<gx:Track>" in kml:                      # 两步路：lon lat alt
        block = re.search(r"<gx:Track>(.*?)</gx:Track>", kml, re.S).group(1)
        for c in re.findall(r"<gx:coord>(.*?)</gx:coord>", block):
            v = c.split()
            if len(v) >= 2:
                pts.append((float(v[0]), float(v[1]), float(v[2]) if len(v) > 2 else 0.0))
    else:                                        # 奥维：lon,lat,alt
        for blk in re.findall(r"<LineString>\s*<coordinates>(.*?)</coordinates>", kml, re.S):
            for c in blk.split():
                v = c.split(",")
                if len(v) >= 2:
                    pts.append((float(v[0]), float(v[1]),
                                float(v[2]) if len(v) > 2 and v[2] else 0.0))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kml", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "track.json"))
    a = ap.parse_args()

    if not os.path.isfile(a.kml):
        raise SystemExit("KML 不存在: " + a.kml)
    pts = read_kml(a.kml)
    if len(pts) < 100:
        raise SystemExit("轨迹点数异常：%d（build.py 要求 >=100）" % len(pts))

    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    lon0, lat0 = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    rad = math.radians(lat0)
    m_lat = 111132.92 - 559.82 * math.cos(2 * rad) + 1.175 * math.cos(4 * rad)
    m_lon = 111412.84 * math.cos(rad) - 93.5 * math.cos(3 * rad)
    w = (max(lons) - min(lons)) * m_lon
    h = (max(lats) - min(lats)) * m_lat

    # 占位段：x/y/gps/dem/time 都为 0，只保证 [4]=lon [5]=lat 真实
    seg = [[0, 0, 0, 0, round(p[0], 7), round(p[1], 7), 0, 0, ""] for p in pts]
    data = {"segments": [seg], "meta": {"bootstrap": True, "points": len(pts),
                                        "note": "占位 track.json，供 build.py 定窗口"}}
    open(a.out, "w", encoding="utf-8").write(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    print("KML 点数 = %d" % len(pts))
    print("轨迹外接 %.0f x %.0f m   bbox=%.6f,%.6f,%.6f,%.6f"
          % (w, h, min(lons), min(lats), max(lons), max(lats)))
    print("已写 %s（占位）。下一步：python3 build.py --sat=esri --z-sat=18" % a.out)
    print("注意：若轨迹有一条以上互不相连的线，本脚本合并为一段，仅影响窗口估算。")


if __name__ == "__main__":
    main()
