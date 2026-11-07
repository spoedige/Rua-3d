#!/usr/bin/env python3
"""Baixa e prepara os dados da faixa da rua em public/data/.

Uso:
  python scripts/fetch_data.py --start -23.635124 -46.632498 --end -23.633756 -46.632802 --width 30
"""
import argparse
import gzip
import io
import json
import math
import re
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform

OVERPASS = "https://overpass-api.de/api/interpreter"
MS_LINKS = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
UA = {"User-Agent": "rua-3d/0.1"}

to_utm = Transformer.from_crs(4326, 31983, always_xy=True).transform
to_wgs = Transformer.from_crs(31983, 4326, always_xy=True).transform


def r6(geom):
    """Arredonda coordenadas a 7 casas (~1 cm)."""
    return json.loads(json.dumps(mapping(geom)), parse_float=lambda s: round(float(s), 7))


def fc(features):
    return {"type": "FeatureCollection", "features": features}


# ---------- área ----------
def build_area(start, end, width):
    axis = LineString([(start[1], start[0]), (end[1], end[0])])
    poly_utm = transform(to_utm, axis).buffer(width, cap_style="flat")
    return transform(to_wgs, poly_utm), poly_utm


# ---------- OSM ----------
def overpass(bbox):
    s, w, n, e = bbox
    q = f"""[out:json][timeout:60];
(way["highway"]({s},{w},{n},{e});
 way["building"]({s},{w},{n},{e});
 relation["building"]({s},{w},{n},{e}););
out tags geom;"""
    r = requests.post(OVERPASS, data={"data": q}, headers=UA, timeout=120)
    r.raise_for_status()
    return r.json()["elements"]


def way_geom(el, closed):
    coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
    if closed:
        from shapely.geometry import Polygon
        return Polygon(coords) if len(coords) >= 4 else None
    return LineString(coords) if len(coords) >= 2 else None


def relation_geom(el):
    from shapely.geometry import Polygon
    from shapely.ops import polygonize, unary_union
    outer = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
             for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
    inner = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
             for m in el.get("members", []) if m.get("role") == "inner" and m.get("geometry")]
    if not outer:
        return None
    g = unary_union(list(polygonize(unary_union(outer))))
    if inner:
        g = g.difference(unary_union(list(polygonize(unary_union(inner)))))
    return g if not g.is_empty else None


def num(v):
    if v is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group().replace(",", ".")) if m else None


def height_of(tags):
    h = num(tags.get("height"))
    if h:
        return h, "osm_height"
    lv = num(tags.get("building:levels"))
    if lv:
        return lv * 3.0, "osm_levels"
    return 6.0, "default"


# ---------- Microsoft Building Footprints ----------
def quadkey(lat, lon, z):
    x = int((lon + 180) / 360 * 2**z)
    s = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * 2**z)
    qk = ""
    for i in range(z, 0, -1):
        d, m = 0, 1 << (i - 1)
        d += 1 if x & m else 0
        d += 2 if y & m else 0
        qk += str(d)
    return qk


def ms_buildings(lat, lon, area):
    links = requests.get(MS_LINKS, headers=UA, timeout=60).text.splitlines()
    qk = quadkey(lat, lon, 9)
    urls = [l.split(",")[2] for l in links[1:] if l.split(",")[0] == "Brazil" and l.split(",")[1] == qk]
    out = []
    for url in urls:
        raw = requests.get(url, headers=UA, timeout=300).content
        for line in gzip.GzipFile(fileobj=io.BytesIO(raw)):
            f = json.loads(line)
            g = shape(f["geometry"])
            if g.intersects(area):
                h = (f.get("properties") or {}).get("height")
                out.append((g, h if h and h > 0 else None))
    return out


# ---------- terreno ----------
def lonlat_to_px(lon, lat, z):
    n = 256 * 2**z
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def terrain(bbox, res=5.0, z=15):
    s, w, n, e = bbox
    x0, y0 = lonlat_to_px(w, n, z)
    x1, y1 = lonlat_to_px(e, s, z)
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256))
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            r = requests.get(TERRARIUM.format(z=z, x=tx, y=ty), headers=UA, timeout=60)
            r.raise_for_status()
            a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"), dtype=float)
            elev = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = elev

    # grade regular em metros (UTM) cobrindo a bbox
    ex0, ny0 = to_utm(w, s)
    ex1, ny1 = to_utm(e, n)
    nx = int(math.ceil((ex1 - ex0) / res)) + 1
    ny = int(math.ceil((ny1 - ny0) / res)) + 1
    grid = np.zeros((ny, nx))
    for j in range(ny):  # linha 0 = norte
        for i in range(nx):
            lon, lat = to_wgs(ex0 + i * res, ny1 - j * res)
            px, py = lonlat_to_px(lon, lat, z)
            px, py = px - tx0 * 256 - 0.5, py - ty0 * 256 - 0.5
            ix, iy = int(math.floor(px)), int(math.floor(py))
            fx, fy = px - ix, py - iy
            q = mosaic[iy:iy + 2, ix:ix + 2]
            grid[j, i] = (q[0, 0] * (1 - fx) * (1 - fy) + q[0, 1] * fx * (1 - fy)
                          + q[1, 0] * (1 - fx) * fy + q[1, 1] * fx * fy)
    return {
        "crs": "EPSG:31983",
        "origin_utm": [round(ex0, 2), round(ny1, 2)],
        "resolution_m": res,
        "width": nx,
        "height": ny,
        "bbox_wgs84": [w, s, e, n],
        "source": f"AWS Terrain Tiles Terrarium z{z}",
        "row_order": "north_to_south",
        "min": round(float(grid.min()), 2),
        "max": round(float(grid.max()), 2),
        "elevations": [round(float(v), 2) for v in grid.ravel()],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", nargs=2, type=float, default=[-23.635124, -46.632498], metavar=("LAT", "LON"))
    ap.add_argument("--end", nargs=2, type=float, default=[-23.633756, -46.632802], metavar=("LAT", "LON"))
    ap.add_argument("--width", type=float, default=30.0, help="metros para cada lado do eixo")
    ap.add_argument("--bbox", nargs=4, type=float, default=[-23.635394, -46.633096, -23.633486, -46.632204],
                    metavar=("S", "W", "N", "E"), help="bbox de download (SO/NE)")
    ap.add_argument("--res", type=float, default=5.0, help="resolução do terreno em metros")
    ap.add_argument("--out", default="public/data")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    area, area_utm = build_area(a.start, a.end, a.width)
    bbox = tuple(a.bbox)

    (out / "area.geojson").write_text(json.dumps(fc([{
        "type": "Feature", "geometry": r6(area),
        "properties": {"width_m": a.width, "area_m2": round(area_utm.area, 1)}}])))

    els = overpass(bbox)

    streets = []
    for el in els:
        t = el.get("tags", {})
        if el["type"] != "way" or "highway" not in t:
            continue
        g = way_geom(el, closed=False)
        if g is None or not g.intersects(area):
            continue
        streets.append({"type": "Feature", "geometry": r6(g.intersection(area)), "properties": {
            "osm_id": el["id"], "highway": t.get("highway"), "name": t.get("name"),
            "width": num(t.get("width")), "lanes": num(t.get("lanes"))}})
    (out / "streets.geojson").write_text(json.dumps(fc(streets)))

    buildings, osm_geoms = [], []
    for el in els:
        t = el.get("tags", {})
        if "building" not in t or t.get("building") == "no":
            continue
        g = way_geom(el, closed=True) if el["type"] == "way" else relation_geom(el)
        if g is None or not g.is_valid or not g.intersects(area):
            continue
        h, src = height_of(t)
        osm_geoms.append(g)
        buildings.append({"type": "Feature", "geometry": r6(g), "properties": {
            "source": "osm", "osm_id": f"{el['type']}/{el['id']}", "building": t.get("building"),
            "height": round(h, 2), "height_source": src}})

    lat_c, lon_c = area.centroid.y, area.centroid.x
    for g, h in ms_buildings(lat_c, lon_c, area):
        overlap = sum(g.intersection(o).area for o in osm_geoms if g.intersects(o))
        if overlap > 0.3 * g.area:
            continue
        buildings.append({"type": "Feature", "geometry": r6(g), "properties": {
            "source": "microsoft", "height": round(h, 2) if h else 6.0,
            "height_source": "ms_estimate" if h else "default"}})
    (out / "buildings.geojson").write_text(json.dumps(fc(buildings)))

    t = terrain(bbox, res=a.res)
    (out / "terrain.json").write_text(json.dumps(t))

    srcs = [b["properties"]["height_source"] for b in buildings]
    real = sum(s in ("osm_height", "osm_levels") for s in srcs)
    print(f"vias: {len(streets)}")
    print(f"edificações: {len(buildings)} (osm {len(osm_geoms)}, microsoft {len(buildings) - len(osm_geoms)})")
    print(f"altura por fonte: { {k: srcs.count(k) for k in sorted(set(srcs))} }")
    print(f"% com altura real (OSM): {100 * real / max(len(buildings), 1):.1f}%")
    print(f"terreno: {t['width']}x{t['height']} @ {a.res} m, elevação {t['min']} a {t['max']} m")


if __name__ == "__main__":
    main()
