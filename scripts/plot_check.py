#!/usr/bin/env python3
"""PNG de conferência: faixa, footprints por fonte e rótulo de altura (mediana / p20-p90)."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

data = Path(sys.argv[1] if len(sys.argv) > 1 else "public/data")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/conferencia.png")
to_utm = Transformer.from_crs(4326, 31983, always_xy=True).transform
load = lambda n: json.loads((data / n).read_text())["features"]
COR = {"geosampa": "#4C78A8", "osm": "#54A24B", "microsoft": "#F58518"}

fig, ax = plt.subplots(figsize=(9, 14), dpi=150)
for f in load("area.geojson"):
    g = transform(to_utm, shape(f["geometry"]))
    ax.fill(*g.exterior.xy, color="#FFE08A", alpha=0.35, zorder=0)
    ax.plot(*g.exterior.xy, color="#B8860B", lw=1.2, ls="--", zorder=1)
for f in load("streets.geojson"):
    g = transform(to_utm, shape(f["geometry"]))
    for ln in getattr(g, "geoms", [g]):
        ax.plot(*ln.xy, color="#888", lw=3, zorder=1)
for f in load("buildings.geojson"):
    p = f["properties"]
    g = transform(to_utm, shape(f["geometry"]))
    destaque = p["source"] == "microsoft" and abs(p.get("area_m2", g.area) - 1134) < 15
    for poly in getattr(g, "geoms", [g]):
        ax.fill(*poly.exterior.xy, color=COR.get(p["source"], "#999"), alpha=0.6, zorder=2)
        ax.plot(*poly.exterior.xy, color="red" if destaque else "#333",
                lw=2.5 if destaque else 0.5, zorder=3)
    c = g.representative_point()
    txt = f"{p['height']:.1f}"
    if p.get("height_eave") is not None:
        txt += f"\n{p['height_eave']:.1f}-{p['height_ridge']:.1f}"
    if p["height_source"] != "lidar_2017":
        txt += f"\n({p['height_source']})"
    ax.text(c.x, c.y, txt, fontsize=5.5, ha="center", va="center", zorder=4)
    if destaque:
        ax.annotate(f"Microsoft {p['area_m2']:.0f} m² ({p['id']})", (c.x, c.y), xytext=(25, 25),
                    textcoords="offset points", color="red", fontsize=8,
                    arrowprops=dict(arrowstyle="->", color="red"), zorder=5)
for k, v in COR.items():
    ax.fill([], [], color=v, alpha=0.6, label=k)
ax.legend(loc="lower left", fontsize=8, title="footprint")
ax.set_title("Conferência: altura mediana (m) / p20-p90 (beiral-cumeeira)", fontsize=10)
ax.set_aspect("equal")
ax.set_xlabel("E (m, EPSG:31983)"); ax.set_ylabel("N (m)")
ax.tick_params(labelsize=7)
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out, bbox_inches="tight")
print(out)
