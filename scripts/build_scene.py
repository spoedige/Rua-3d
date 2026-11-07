#!/usr/bin/env python3
"""Gera os assets da cena 3D (etapa 3a) em public/data/scene/ a partir do LiDAR 2017 e dos GeoJSON.

Saídas (coordenadas locais em metros: x = leste, y = norte, z = cota; origem em meta.origin_utm):
  meta.json        origem, extensão, resolução, fonte da textura
  dtm.bin          MDT float32 (linhas de sul para norte), classe 2 interpolada, 0,5 m
  ortho.jpg        textura vista de cima (cor do ponto mais alto por célula), mesma extensão do MDT
  buildings.json   paredes e telhados de duas águas já triangulados
  lines.json       vias e contorno da faixa, com cota do terreno

Uso: python scripts/build_scene.py   (depois de fetch_data.py; reaproveita data_cache/lidar)
"""
import argparse
import json
import math
import sys
import warnings
from pathlib import Path

import laspy
import numpy as np
import shapely
from PIL import Image
from scipy.interpolate import griddata
from scipy.ndimage import distance_transform_edt, gaussian_filter, maximum_filter
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import split, transform

sys.path.insert(0, str(Path(__file__).parent))
from fetch_data import geosampa_wfs, to_utm  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

BBOX = (-23.635394, -46.633096, -23.633486, -46.632204)  # S, W, N, E
MAX_PITCH = math.radians(40)


def load_points(cache, rect):
    x0, y0, x1, y1 = rect
    X, Y, Z, C, R, G, B = [], [], [], [], [], [], []
    for path in sorted(Path(cache).glob("*.laz")):
        las = laspy.read(path)
        x, y = np.asarray(las.x), np.asarray(las.y)
        m = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        if not m.any():
            continue
        X.append(x[m]); Y.append(y[m]); Z.append(np.asarray(las.z)[m])
        C.append(np.asarray(las.classification)[m])
        for arr, k in ((R, "red"), (G, "green"), (B, "blue")):
            arr.append(np.asarray(getattr(las, k))[m])
    return [np.concatenate(a) for a in (X, Y, Z, C, R, G, B)]


def dtm_grid(rect, res, X, Y, Z, C):
    x0, y0, x1, y1 = rect
    nx, ny = int(round((x1 - x0) / res)) + 1, int(round((y1 - y0) / res)) + 1
    gx, gy = np.meshgrid(x0 + np.arange(nx) * res, y0 + np.arange(ny) * res)
    g = C == 2
    pts = np.c_[X[g], Y[g]]
    z = griddata(pts, Z[g], (gx, gy), method="linear")
    hole = np.isnan(z)
    if hole.any():
        z[hole] = griddata(pts, Z[g], (gx[hole], gy[hole]), method="nearest")
    return z.astype(np.float32), nx, ny


def ortho_image(rect, res, X, Y, Z, C, R, G, B):
    """Cor do ponto mais alto de cada célula (ortofoto verdadeira a partir do LiDAR)."""
    x0, y0, x1, y1 = rect
    nx, ny = int(math.ceil((x1 - x0) / res)), int(math.ceil((y1 - y0) / res))
    keep = ~np.isin(C, (7, 18))
    ix = ((X[keep] - x0) / res).astype(int).clip(0, nx - 1)
    iy = ((Y[keep] - y0) / res).astype(int).clip(0, ny - 1)
    z = Z[keep]
    order = np.argsort(z)  # o último escrito (mais alto) vence
    img = np.zeros((ny, nx, 3), np.uint8)
    filled = np.zeros((ny, nx), bool)
    rgb = np.c_[R[keep], G[keep], B[keep]] / 256
    img[iy[order], ix[order]] = rgb[order].clip(0, 255).astype(np.uint8)
    filled[iy, ix] = True
    # preenche células vazias com o vizinho mais próximo
    _, (ii, jj) = distance_transform_edt(~filled, return_indices=True)
    img = img[ii, jj]
    return Image.fromarray(img[::-1])  # linha 0 = norte


def sample(dtm, rect, res, x, y):
    x0, y0, _, _ = rect
    ny, nx = dtm.shape
    fx = np.clip((np.asarray(x) - x0) / res, 0, nx - 1.001)
    fy = np.clip((np.asarray(y) - y0) / res, 0, ny - 1.001)
    i, j = fx.astype(int), fy.astype(int)
    tx, ty = fx - i, fy - j
    return (dtm[j, i] * (1 - tx) * (1 - ty) + dtm[j, i + 1] * tx * (1 - ty)
            + dtm[j + 1, i] * (1 - tx) * ty + dtm[j + 1, i + 1] * tx * ty)


def ridge_frame(poly):
    """Eixo da cumeeira = lado maior do retângulo mínimo; devolve centro, direção, normal e meia-largura."""
    mrr = poly.minimum_rotated_rectangle
    c = np.array(mrr.exterior.coords[:4])
    e1, e2 = c[1] - c[0], c[2] - c[1]
    along, across = (e1, e2) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, e1)
    u = along / np.linalg.norm(along)
    n = across / np.linalg.norm(across)
    center = c.mean(axis=0)
    return center, u, n, np.linalg.norm(across) / 2, np.linalg.norm(along)


def triangulate(poly):
    """Triângulos (lista de 3 coords) de um polígono, respeitando furos e concavidades."""
    tris = shapely.constrained_delaunay_triangles(poly)
    return [list(t.exterior.coords)[:3] for t in tris.geoms]


def building_mesh(poly, base, eave, ridge, origin):
    """Paredes até o beiral/telhado e telhado de duas águas sobre o footprint."""
    ox, oy = origin
    poly = shapely.make_valid(poly)
    polys = [p for p in getattr(poly, "geoms", [poly]) if p.geom_type == "Polygon" and p.area > 1]
    walls, roofs = [], []
    for p in polys:
        p = p.simplify(0.05)
        center, u, n, half, length = ridge_frame(p)
        rise = max(0.0, ridge - eave)
        rise = min(rise, half * math.tan(MAX_PITCH))
        flat = rise < 0.3 or half < 1.0

        def roof_z(x, y):
            if flat:
                return base + (eave + ridge) / 2 if rise < 0.3 else base + eave
            d = abs((x - center[0]) * n[0] + (y - center[1]) * n[1])
            return base + eave + rise * max(0.0, 1 - d / half)

        if flat:
            parts = [p]
        else:
            a, b = center - u * length, center + u * length
            res = split(p, LineString([a, b]))
            parts = [g for g in res.geoms if g.geom_type == "Polygon"]
        # telhado
        for part in parts:
            for t in triangulate(part):
                # orientação anti-horária vista de cima
                (x1, y1), (x2, y2), (x3, y3) = t
                if (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1) < 0:
                    t = [t[0], t[2], t[1]]
                for x, y in t:
                    roofs += [round(x - ox, 2), round(y - oy, 2), round(roof_z(x, y), 2)]
        # paredes: contorno com os pontos onde a cumeeira corta a borda
        edge_src = shapely.union_all(parts) if not flat else p
        rings = []
        for g in getattr(edge_src, "geoms", [edge_src]):
            g = shapely.orient_polygons(g) if hasattr(shapely, "orient_polygons") else shapely.geometry.polygon.orient(g)
            rings.append(list(g.exterior.coords))
            rings += [list(r.coords) for r in g.interiors]
        for ring in rings:
            for (xa, ya), (xb, yb) in zip(ring[:-1], ring[1:]):
                za, zb = roof_z(xa, ya), roof_z(xb, yb)
                A = (xa - ox, ya - oy)
                Bp = (xb - ox, yb - oy)
                lo = base - 1.0  # enterra a base para não flutuar em terreno inclinado
                quad = [(A, lo), (Bp, lo), (Bp, zb), (A, lo), (Bp, zb), (A, za)]
                for (x, y), z in quad:
                    walls += [round(x, 2), round(y, 2), round(z, 2)]
    return walls, roofs


def detect_trees(rect, res, dtm, X, Y, Z, C, min_h=3.0, win=3.5):
    """Árvores a partir da classe 5: picos locais do modelo de altura da vegetação."""
    x0, y0, _, _ = rect
    ny, nx = dtm.shape
    v = C == 5
    ix = ((X[v] - x0) / res).astype(int)
    iy = ((Y[v] - y0) / res).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    top = np.full((ny, nx), -np.inf)
    np.maximum.at(top, (iy[ok], ix[ok]), Z[v][ok])
    chm = np.where(np.isfinite(top), top - dtm, 0.0).clip(0)
    sm = gaussian_filter(chm, 1.0)
    k = int(round(win / res)) | 1
    peaks = np.argwhere((sm == maximum_filter(sm, size=k)) & (sm >= min_h))
    if not len(peaks):
        return []
    veg = np.argwhere(chm > 2.0)
    tree = cKDTree(peaks)
    d, owner = tree.query(veg, distance_upper_bound=6.0 / res)
    counts = np.bincount(owner[np.isfinite(d)], minlength=len(peaks))
    out = []
    for (j, i), n in zip(peaks, counts):
        r = max(1.0, math.sqrt(n * res * res / math.pi))
        h = float(chm[max(0, j - 1):j + 2, max(0, i - 1):i + 2].max())
        out.append({"x": round(x0 + (i + 0.5) * res, 2), "y": round(y0 + (j + 0.5) * res, 2),
                    "z": round(float(dtm[j, i]), 2), "h": round(h, 2), "r": round(r, 2)})
    return out


def sidewalk_mesh(polys, rect, res, dtm, origin, road, cell=1.0, lift=0.04, skirt=0.25):
    """Calçadas do GeoSampa drapeadas no MDT, com face de meio-fio (saia) nas bordas."""
    ox, oy = origin
    tops, sides, info = [], [], []
    for pid, poly, props in polys:
        x0, y0, x1, y1 = poly.bounds
        pieces = []
        for gx in np.arange(math.floor(x0), x1, cell):
            for gy in np.arange(math.floor(y0), y1, cell):
                q = poly.intersection(shapely.box(gx, gy, gx + cell, gy + cell))
                pieces += [g for g in getattr(q, "geoms", [q]) if g.geom_type == "Polygon" and g.area > 1e-4]
        for pc in pieces:
            for t in triangulate(pc):
                (xa, ya), (xb, yb), (xc, yc) = t
                if (xb - xa) * (yc - ya) - (xc - xa) * (yb - ya) < 0:
                    t = [t[0], t[2], t[1]]
                for x, y in t:
                    z = float(sample(dtm, rect, res, x, y)) + lift
                    tops += [round(x - ox, 2), round(y - oy, 2), round(z, 2)]
        # altura do meio-fio: mediana dentro menos mediana numa faixa de 0,5-2 m fora
        # só o lado da rua (fora da quadra viária), não o lado do lote
        ring = poly.buffer(2.0).difference(poly.buffer(0.5)).intersection(road)
        def med(g):
            xs, ys = np.meshgrid(np.arange(x0 - 2, x1 + 2, res), np.arange(y0 - 2, y1 + 2, res))
            m = shapely.contains_xy(g, xs, ys)
            return float(np.median(sample(dtm, rect, res, xs[m], ys[m]))) if m.any() else None
        zin, zout = med(poly), med(ring)
        curb = round(zin - zout, 2) if zin is not None and zout is not None else None
        for r in [poly.exterior, *poly.interiors]:
            line = r.segmentize(1.0)
            cs = list(line.coords)
            for (xa, ya), (xb, yb) in zip(cs[:-1], cs[1:]):
                za = float(sample(dtm, rect, res, xa, ya)) + lift
                zb = float(sample(dtm, rect, res, xb, yb)) + lift
                A, Bp = (xa - ox, ya - oy), (xb - ox, yb - oy)
                for (x, y), z in [(A, za - skirt), (Bp, zb - skirt), (Bp, zb), (A, za - skirt), (Bp, zb), (A, za)]:
                    sides += [round(x, 2), round(y, 2), round(z, 2)]
        info.append({"id": pid, "curb_m": curb, **props})
    return tops, sides, info


def point_cloud(rect, zref, X, Y, Z, C, R, G, B):
    """Nuvem colorida compacta: int16 em cm (x, y, z relativos) + uint8 RGB."""
    keep = ~np.isin(C, (7, 18))
    xs = np.round((X[keep] - rect[0]) * 100).astype(np.int32)
    ys = np.round((Y[keep] - rect[1]) * 100).astype(np.int32)
    zs = np.round((Z[keep] - zref) * 100).astype(np.int32)
    inside = (xs >= 0) & (ys >= 0) & (xs < 32767) & (ys < 32767) & (zs > -32767) & (zs < 32767)
    pos = np.c_[xs[inside], ys[inside], zs[inside]].astype(np.int16)
    rgb = (np.c_[R[keep], G[keep], B[keep]][inside] / 256).clip(0, 255).astype(np.uint8)
    return pos.tobytes() + rgb.tobytes(), int(inside.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="public/data")
    ap.add_argument("--cache", default="data_cache/lidar")
    ap.add_argument("--res", type=float, default=0.5, help="resolução do MDT (m)")
    ap.add_argument("--tex", type=float, default=0.25, help="resolução da textura (m/px)")
    a = ap.parse_args()
    data = Path(a.data)
    out = data / "scene"
    out.mkdir(parents=True, exist_ok=True)

    s, w, n, e = BBOX
    bx0, by0 = to_utm(w, s)
    bx1, by1 = to_utm(e, n)
    blds = json.loads((data / "buildings.geojson").read_text())["features"]
    polys = [transform(to_utm, shape(f["geometry"])) for f in blds]
    # bbox estendida para cobrir prédios que cruzam a borda (entram inteiros)
    ux0, uy0, ux1, uy1 = shapely.union_all(polys).bounds
    rect = (math.floor(min(bx0, ux0 - 2)), math.floor(min(by0, uy0 - 2)),
            math.ceil(max(bx1, ux1 + 2)), math.ceil(max(by1, uy1 + 2)))
    origin = (rect[0], rect[1])

    X, Y, Z, C, R, G, B = load_points(a.cache, (rect[0] - 5, rect[1] - 5, rect[2] + 5, rect[3] + 5))
    dtm, nx, ny = dtm_grid(rect, a.res, X, Y, Z, C)
    (out / "dtm.bin").write_bytes(dtm.tobytes())
    tex = ortho_image(rect, a.tex, X, Y, Z, C, R, G, B)
    tex.save(out / "ortho.jpg", quality=82, optimize=True)

    walls, roofs, info = [], [], []
    for f, p in zip(blds, polys):
        pr = f["properties"]
        inside = shapely.contains_xy(p, *np.meshgrid(
            np.arange(rect[0], rect[2], a.res), np.arange(rect[1], rect[3], a.res)))
        gz = sample(dtm, rect, a.res, *np.meshgrid(np.arange(rect[0], rect[2], a.res),
                                                      np.arange(rect[1], rect[3], a.res)))
        base = float(np.median(gz[inside])) if inside.any() else float(sample(dtm, rect, a.res, p.centroid.x, p.centroid.y))
        eave = pr.get("height_eave") or pr["height"]
        ridge = pr.get("height_ridge") or pr["height"]
        w0, r0 = len(walls) // 3, len(roofs) // 3
        wv, rv = building_mesh(p, base, eave, ridge, origin)
        walls += wv; roofs += rv
        info.append({"id": pr["id"], "height_source": pr["height_source"], "height": pr["height"],
                     "eave": pr.get("height_eave"), "ridge": pr.get("height_ridge"),
                     "walls": [w0, len(walls) // 3 - w0], "roof": [r0, len(roofs) // 3 - r0]})
    (out / "buildings.json").write_text(json.dumps({"walls": walls, "roofs": roofs, "info": info},
                                                   separators=(",", ":")))

    def drape(geom, step=1.0):
        lines = []
        for ln in getattr(geom, "geoms", [geom]):
            ln = LineString(ln.exterior.coords) if ln.geom_type == "Polygon" else ln
            k = max(2, int(ln.length / step) + 1)
            pts = [ln.interpolate(t, normalized=True) for t in np.linspace(0, 1, k)]
            xs, ys = np.array([q.x for q in pts]), np.array([q.y for q in pts])
            zs = sample(dtm, rect, a.res, xs, ys)
            lines.append([v for x, y, z in zip(xs, ys, zs) for v in (round(x - origin[0], 2), round(y - origin[1], 2), round(float(z), 2))])
        return lines

    streets = json.loads((data / "streets.geojson").read_text())["features"]
    area = json.loads((data / "area.geojson").read_text())["features"][0]
    (out / "lines.json").write_text(json.dumps({
        "streets": [{"name": f["properties"].get("name"), "pts": l}
                    for f in streets for l in drape(transform(to_utm, shape(f["geometry"])))],
        "area": drape(transform(to_utm, shape(area["geometry"]))),
    }, separators=(",", ":")))

    # árvores (LiDAR classe 5) e conferência com o cadastro do GeoSampa
    trees = detect_trees(rect, a.res, dtm, X, Y, Z, C)
    cad = [transform(to_utm, shape(f["geometry"])) for f in geosampa_wfs("arvore", BBOX)]
    if trees and cad:
        tt = cKDTree([(t["x"], t["y"]) for t in trees])
        dist, _ = tt.query([(p.x, p.y) for p in cad])
        print(f"árvores cadastradas no GeoSampa: {len(cad)}, com árvore LiDAR a até 3 m: {(dist <= 3).sum()}")
    for t in trees:
        t["x"] = round(t["x"] - origin[0], 2); t["y"] = round(t["y"] - origin[1], 2)
    (out / "trees.json").write_text(json.dumps(trees, separators=(",", ":")))

    # calçadas (GeoSampa) recortadas no retângulo da cena
    clip = shapely.box(*rect)
    sw = []
    for f in geosampa_wfs("calcada", BBOX):
        g = transform(to_utm, shape(f["geometry"])).intersection(clip)
        p = f["properties"]
        for k, part in enumerate(gg for gg in getattr(g, "geoms", [g]) if gg.geom_type == "Polygon" and gg.area > 0.5):
            sw.append((f"sw/{p['cd_identificador_calcada']}" + (f"_{k}" if k else ""), part,
                       {"logradouro": p.get("nm_logradouro"), "largura_media_m": p.get("qt_largura_media_trecho")}))
    quadras = shapely.union_all([transform(to_utm, shape(f["geometry"])) for f in geosampa_wfs("quadra_viaria_editada", BBOX)])
    road = clip.difference(quadras)
    sw_top, sw_side, sw_info = sidewalk_mesh(sw, rect, a.res, dtm, origin, road)
    (out / "sidewalks.json").write_text(json.dumps({"top": sw_top, "side": sw_side, "info": sw_info},
                                                   separators=(",", ":")))

    pc, npts = point_cloud(rect, float(dtm.min()), X, Y, Z, C, R, G, B)
    (out / "points.bin").write_bytes(pc)

    meta = {"crs": "EPSG:31983", "origin_utm": origin, "size": [rect[2] - rect[0], rect[3] - rect[1]],
            "dtm": {"res": a.res, "nx": nx, "ny": ny, "min": round(float(dtm.min()), 2), "max": round(float(dtm.max()), 2)},
            "ortho": {"file": "ortho.jpg", "res": a.tex, "source": "lidar_2017_rgb",
                      "note": "cor do ponto LiDAR mais alto por célula; ortofoto GeoSampa sem download direto"},
            "points": {"file": "points.bin", "count": npts, "encoding": "int16 cm (x,y relativos à origem, z relativo a dtm.min) + uint8 RGB"},
            "trees": len(trees), "sidewalks": len(sw_info)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))

    curbs = [i["curb_m"] for i in sw_info if i["curb_m"] is not None]
    print(f"árvores LiDAR: {len(trees)} (altura {min(t['h'] for t in trees):.1f}-{max(t['h'] for t in trees):.1f} m)" if trees else "árvores: 0")
    print(f"calçadas: {len(sw_info)}, meio-fio mediano {np.median(curbs):.2f} m" if curbs else f"calçadas: {len(sw_info)}")
    print(f"nuvem: {npts} pontos")
    total = sum(f.stat().st_size for f in out.iterdir())
    print(f"extensão {meta['size'][0]} x {meta['size'][1]} m, MDT {nx}x{ny} @ {a.res} m, cota {meta['dtm']['min']}-{meta['dtm']['max']}")
    print(f"textura {tex.size[0]}x{tex.size[1]} @ {a.tex} m; prédios {len(info)}, triângulos paredes {len(walls)//9}, telhados {len(roofs)//9}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name}: {f.stat().st_size / 1024:.0f} KB")
    print(f"total cena: {total / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
