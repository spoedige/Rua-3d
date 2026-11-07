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
import warnings
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform

GEOSAMPA_WFS = "https://wms.geosampa.prefeitura.sp.gov.br/geoserver/ows"
LIDAR_LAZ = "https://laz-m3dc-pmsp.s3.sa-east-1.amazonaws.com/MDS_color_{q}.laz"
OFM_TILEJSON = "https://tiles.openfreemap.org/planet"
MS_LINKS = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
warnings.filterwarnings("ignore", category=DeprecationWarning)
UA = {"User-Agent": "rua-3d/0.1"}

# edificações descartadas por id (ver CLAUDE.md)
EXCLUDE_IDS = {
    "ms/333509_7385277",  # antigo ms/13: Microsoft, 1.134 m², funde várias construções e fica quase todo fora da faixa
}

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


# ---------- OSM via vector tiles do OpenFreeMap ----------
def tile_xy(lat, lon, z):
    sn = math.sin(math.radians(lat))
    return int((lon + 180) / 360 * 2**z), int((0.5 - math.log((1 + sn) / (1 - sn)) / (4 * math.pi)) * 2**z)


def ofm_layers(bbox, z=14, layers=("transportation", "transportation_name", "building")):
    """Decodifica os tiles z14 que cobrem a bbox e devolve {camada: [(id, props, geom_wgs84)]}."""
    import mapbox_vector_tile as mvt
    from shapely.affinity import affine_transform
    s, w, n, e = bbox
    x0, y0 = tile_xy(n, w, z)
    x1, y1 = tile_xy(s, e, z)
    url = requests.get(OFM_TILEJSON, headers=UA, timeout=30).json()["tiles"][0]
    out = {k: [] for k in layers}
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            r = requests.get(url.format(z=z, x=tx, y=ty), headers=UA, timeout=60)
            r.raise_for_status()
            d = mvt.decode(r.content, default_options={"y_coord_down": True})
            for k in layers:
                L = d.get(k)
                if not L:
                    continue
                ext = L["extent"]
                for f in L["features"]:
                    g = shape(f["geometry"])
                    # coords do tile -> pixel mundial -> lon/lat
                    g = affine_transform(g, [1 / ext, 0, 0, 1 / ext, tx, ty])
                    g = transform(lambda X, Y, Z=None: _merc_to_lonlat(X, Y, z), g)
                    out[k].append((f.get("id"), f["properties"], g))
    return out


def _merc_to_lonlat(X, Y, z):
    X, Y = np.asarray(X, float), np.asarray(Y, float)
    lon = X / 2**z * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * Y / 2**z))))
    return lon, lat


def merge_by_id(items):
    """Une pedaços de uma mesma feição cortada entre tiles."""
    from shapely.ops import unary_union, linemerge
    groups = {}
    for fid, props, g in items:
        key = fid if fid is not None else id(g)
        groups.setdefault(key, (props, []))[1].append(g)
    res = []
    for key, (props, gs) in groups.items():
        g = unary_union(gs)
        if g.geom_type == "MultiLineString":
            g = linemerge(g)
        res.append((key, props, g))
    return res


def num(v):
    if v is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group().replace(",", ".")) if m else None


def height_of(render_height):
    """render_height do OpenMapTiles = height do OSM, ou levels*3,66, ou 5 m padrão (arredondado)."""
    if render_height is None or render_height <= 5:
        return 6.0, "default"
    return float(render_height), "osm_tiles"


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


# ---------- GeoSampa ----------
def geosampa_wfs(layer, bbox):
    s, w, n, e = bbox
    r = requests.get(GEOSAMPA_WFS, headers=UA, timeout=120, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": f"geoportal:{layer}",
        "outputFormat": "application/json", "srsName": "EPSG:4326", "bbox": f"{w},{s},{e},{n},EPSG:4326"})
    r.raise_for_status()
    return r.json()["features"]


def lidar_points(bbox, area_utm, cache, pad=40.0):
    """Pontos LiDAR 2017 (LAZ por folha, AWS Open Data) num retângulo UTM em volta da faixa."""
    import laspy
    x0, y0, x1, y1 = area_utm.buffer(pad).bounds
    cache.mkdir(parents=True, exist_ok=True)
    X, Y, Z, C = [], [], [], []
    for f in geosampa_wfs("quadricula_folha_mds", bbox):
        q = f["properties"]["cd_quadricula"]
        path = cache / f"MDS_color_{q}.laz"
        if not path.exists():
            print(f"baixando LiDAR {q}...")
            r = requests.get(LIDAR_LAZ.format(q=q), headers=UA, timeout=600)
            r.raise_for_status()
            path.write_bytes(r.content)
        las = laspy.read(path)
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x > x0) & (x < x1) & (y > y0) & (y < y1)
        X.append(x[m]); Y.append(y[m]); Z.append(np.asarray(las.z)[m]); C.append(np.asarray(las.classification)[m])
    return (x0, y0, x1, y1), *map(np.concatenate, (X, Y, Z, C))


def ndsm(extent, X, Y, Z, C, res=0.5):
    """MDS (Z máximo por célula, sem ruído) menos MDT (solo, classe 2, interpolado)."""
    from scipy.interpolate import griddata
    x0, y0, x1, y1 = extent
    nx, ny = int((x1 - x0) / res), int((y1 - y0) / res)
    keep = ~np.isin(C, (7, 18))
    ix = ((X[keep] - x0) / res).astype(int).clip(0, nx - 1)
    iy = ((Y[keep] - y0) / res).astype(int).clip(0, ny - 1)
    mds = np.full((ny, nx), -np.inf)
    np.maximum.at(mds, (iy, ix), Z[keep])
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * res, y0 + (np.arange(ny) + 0.5) * res)
    g = C == 2
    pts = np.c_[X[g], Y[g]]
    mdt = griddata(pts, Z[g], (gx, gy), method="linear")
    hole = np.isnan(mdt)
    mdt[hole] = griddata(pts, Z[g], (gx[hole], gy[hole]), method="nearest")
    diff = np.where(np.isfinite(mds), mds - mdt, np.nan)
    return gx, gy, diff


def lidar_height(g_utm, gx, gy, diff, min_cells=8):
    import shapely
    m = shapely.contains_xy(g_utm, gx, gy) & np.isfinite(diff)
    if m.sum() < min_cells:
        return None
    v = diff[m]
    return float(np.median(v)), float(np.percentile(v, 20)), float(np.percentile(v, 90))


def read_table(path):
    """Lê CSV do jeito que vier: separador , ou ; (Excel BR) e UTF-8, UTF-8 com BOM ou Windows-1252.
    Devolve (linhas, formato) para que o arquivo possa ser regravado no mesmo formato."""
    import csv
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    head = text.splitlines()[0] if text else ""
    delim = ";" if head.count(";") > head.count(",") else ","
    rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(text.splitlines(), delimiter=delim)]
    fmt = {"delimiter": delim, "encoding": "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") or enc == "cp1252" else "utf-8"}
    return rows, fmt


def manual_heights(path):
    if not path.exists():
        return {}
    res = {}
    for row in read_table(path)[0]:
        alt = num(row.get("altura_m"))
        pav = num(row.get("pavimentos"))
        if alt:
            res[str(row["id"])] = alt
        elif pav:
            res[str(row["id"])] = pav * 3.0
    return res


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
    ap.add_argument("--cache", default="data_cache/lidar", help="pasta dos LAZ baixados")
    ap.add_argument("--manual", default="public/data/heights_manual.csv",
                    help="CSV id,pavimentos,altura_m com prioridade sobre as demais fontes")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    area, area_utm = build_area(a.start, a.end, a.width)
    bbox = tuple(a.bbox)

    (out / "area.geojson").write_text(json.dumps(fc([{
        "type": "Feature", "geometry": r6(area),
        "properties": {"width_m": a.width, "area_m2": round(area_utm.area, 1)}}])))

    L = ofm_layers(bbox)
    names = [(p.get("name"), g) for _, p, g in merge_by_id(L["transportation_name"])]
    names_utm = [(nm, transform(to_utm, g)) for nm, g in names if nm]

    streets = []
    for fid, p, g in merge_by_id(L["transportation"]):
        if not g.geom_type.endswith("LineString") or not g.intersects(area):
            continue
        clip = g.intersection(area)
        gu = transform(to_utm, clip)
        # nome = linha de transportation_name com maior trecho a até 3 m da via
        name, best = None, 0.0
        for nm, gn in names_utm:
            ov = gn.intersection(gu.buffer(3)).length
            if ov > best:
                best, name = ov, nm
        streets.append({"type": "Feature", "geometry": r6(clip), "properties": {
            "osm_id": fid, "highway": p.get("subclass") or p.get("class"), "class": p.get("class"),
            "name": name, "width": None, "lanes": None, "oneway": p.get("oneway")}})
    (out / "streets.geojson").write_text(json.dumps(fc(streets)))

    # footprints: GeoSampa (aerofotogrametria) > OSM > Microsoft, sem sobreposição > 30%
    cands = []  # (id, source, geom, props, altura_fallback)
    for f in geosampa_wfs("edificacao", bbox):
        g = shape(f["geometry"])
        if g.intersects(area):
            p = f["properties"]
            cands.append((f"gs/{p['cd_identificador']}", "geosampa", g,
                          {"escala": p.get("tx_escala")}, (p.get("qt_altura_edificacao"), "geosampa_aero")))
    for fid, p, g in merge_by_id(L["building"]):
        if g.geom_type in ("Polygon", "MultiPolygon") and g.intersects(area):
            h, src = height_of(p.get("render_height"))
            cands.append((f"osm/{fid}", "osm", g, {}, (h if src != "default" else None, src)))
    for i, (g, h) in enumerate(ms_buildings(area.centroid.y, area.centroid.x, area)):
        cands.append((f"ms/{i}", "microsoft", g, {}, (h, "ms_estimate")))

    # id estável: prefixo da fonte + centroide UTM arredondado a 1 m (não depende da ordem dos arquivos)
    stable, seen = [], set()
    for legacy, source, g, props, fb in cands:
        cu = transform(to_utm, g).centroid
        sid = f"{legacy.split('/')[0]}/{cu.x:.0f}_{cu.y:.0f}"
        k = 2
        while sid in seen:
            sid = f"{legacy.split('/')[0]}/{cu.x:.0f}_{cu.y:.0f}_{k}"
            k += 1
        seen.add(sid)
        stable.append((sid, source, g, {**props, "src_id": legacy}, fb))
    cands = stable

    from shapely.ops import unary_union
    kept, covered = [], None
    for c in cands:
        if c[0] in EXCLUDE_IDS or c[3]["src_id"] in EXCLUDE_IDS:
            if c[0] not in EXCLUDE_IDS:
                print(f"aviso: EXCLUDE_IDS usa id antigo {c[3]['src_id']}; troque por {c[0]}")
            print(f"excluído: {c[0]}")
            continue
        gu = transform(to_utm, c[2])
        if covered is not None and gu.intersection(covered).area > 0.3 * gu.area:
            continue
        kept.append((*c, gu))
        covered = gu if covered is None else unary_union([covered, gu])

    extent, X, Y, Z, C = lidar_points(bbox, area_utm, Path(a.cache))
    gx, gy, diff = ndsm(extent, X, Y, Z, C)
    manual = manual_heights(Path(a.manual))

    buildings = []
    for bid, source, g, props, (hfb, srcfb), gu in kept:
        lid = lidar_height(gu, gx, gy, diff)
        eave = ridge = None
        if lid and lid[0] >= 2.0:
            eave, ridge = round(lid[1], 2), round(lid[2], 2)
        mkey = bid if bid in manual else props["src_id"] if props["src_id"] in manual else None
        if mkey and mkey != bid:
            print(f"aviso: heights_manual.csv usa id antigo {mkey}; troque por {bid}")
        if mkey:
            h, src = manual[mkey], "manual"
        elif eave is not None:
            h, src = lid[0], "lidar_2017"
        else:
            h, src = (hfb, srcfb) if hfb and hfb > 0 else (6.0, "default")
        buildings.append({"type": "Feature", "geometry": r6(g), "properties": {
            "id": bid, "source": source, "height": round(h, 2), "height_eave": eave, "height_ridge": ridge,
            "height_source": src, "area_m2": round(gu.area, 1), **props}})
    (out / "buildings.geojson").write_text(json.dumps(fc(buildings)))

    t = terrain(bbox, res=a.res)
    (out / "terrain.json").write_text(json.dumps(t))

    srcs = [b["properties"]["height_source"] for b in buildings]
    real = sum(s in ("lidar_2017", "manual", "osm_tiles") for s in srcs)
    print(f"vias: {len(streets)}")
    fontes = [b["properties"]["source"] for b in buildings]
    print(f"edificações: {len(buildings)} { {k: fontes.count(k) for k in sorted(set(fontes))} }")
    hs = sorted(b["properties"]["height"] for b in buildings)
    print(f"alturas: min {hs[0]} / mediana {hs[len(hs) // 2]} / max {hs[-1]} m")
    print(f"altura por fonte: { {k: srcs.count(k) for k in sorted(set(srcs))} }")
    print(f"% com altura medida (LiDAR/manual/OSM): {100 * real / max(len(buildings), 1):.1f}%")
    print(f"terreno: {t['width']}x{t['height']} @ {a.res} m, elevação {t['min']} a {t['max']} m")


if __name__ == "__main__":
    main()
