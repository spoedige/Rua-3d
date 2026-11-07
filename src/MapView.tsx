import { useEffect, useRef } from "react";
import maplibregl, { type MapGeoJSONFeature } from "maplibre-gl";

const DATA = `${import.meta.env.BASE_URL}data/`;

// cor por fonte da altura
export const HEIGHT_SOURCES: [string, string, string][] = [
  ["lidar_2017", "#2b6cb0", "LiDAR 2017 (MDS − MDT)"],
  ["manual", "#2f855a", "Manual"],
  ["geosampa_aero", "#d69e2e", "GeoSampa (aerofoto)"],
  ["osm_tiles", "#805ad5", "OSM"],
  ["ms_estimate", "#dd6b20", "Microsoft (estimativa)"],
  ["default", "#a0aec0", "Padrão 6 m"],
];

const fmt = (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)} m` : "—");

function popupHtml(f: MapGeoJSONFeature) {
  const p = f.properties as Record<string, unknown>;
  const label = HEIGHT_SOURCES.find(([k]) => k === p.height_source)?.[2] ?? String(p.height_source);
  const rows: [string, string][] = [
    ["id", String(p.id)],
    ["footprint", String(p.source)],
    ["mediana", fmt(p.height)],
    ["beiral (p20)", fmt(p.height_eave)],
    ["cumeeira (p90)", fmt(p.height_ridge)],
    ["fonte da altura", label],
    ["área", typeof p.area_m2 === "number" ? `${p.area_m2.toFixed(0)} m²` : "—"],
  ];
  return `<div class="popup"><table>${rows
    .map(([k, v]) => `<tr><td>${k}</td><td><b>${v}</b></td></tr>`)
    .join("")}</table></div>`;
}

export default function MapView() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const map = new maplibregl.Map({
      container: ref.current!,
      style: "https://tiles.openfreemap.org/styles/liberty",
      center: [-46.63265, -23.63444],
      zoom: 17.6,
      pitch: 55,
      bearing: -15,
      maxPitch: 75,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    map.on("load", () => {
      // esconde os prédios 3D do mapa base para não duplicar com os nossos
      for (const l of map.getStyle().layers ?? []) {
        if (l.type === "fill-extrusion" || ("source-layer" in l && l["source-layer"] === "building")) {
          map.setLayoutProperty(l.id, "visibility", "none");
        }
      }

      map.addSource("area", { type: "geojson", data: `${DATA}area.geojson` });
      map.addSource("streets", { type: "geojson", data: `${DATA}streets.geojson` });
      map.addSource("buildings", { type: "geojson", data: `${DATA}buildings.geojson`, promoteId: "id" });

      // máscara escurecendo o entorno + faixa destacada
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": "#ffd54f", "fill-opacity": 0.18 } });
      map.addLayer({
        id: "area-line", type: "line", source: "area",
        paint: { "line-color": "#e0a800", "line-width": 2.5, "line-dasharray": [2, 1.5] },
      });
      map.addLayer({
        id: "streets", type: "line", source: "streets",
        layout: { "line-cap": "round" },
        paint: { "line-color": "#e53e3e", "line-width": ["interpolate", ["linear"], ["zoom"], 15, 2, 19, 8], "line-opacity": 0.8 },
      });

      const color = [
        "match", ["get", "height_source"], ...HEIGHT_SOURCES.flatMap(([k, c]) => [k, c]), "#718096",
      ] as unknown as maplibregl.ExpressionSpecification;

      map.addLayer({
        id: "buildings", type: "fill-extrusion", source: "buildings",
        paint: {
          "fill-extrusion-color": [
            "case", ["boolean", ["feature-state", "hover"], false], "#ffffff", color,
          ] as maplibregl.ExpressionSpecification,
          // cumeeira (p90); sem LiDAR, cai para a altura disponível
          "fill-extrusion-height": ["coalesce", ["get", "height_ridge"], ["get", "height"]],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.9,
        },
      });

      let hovered: string | number | undefined;
      map.on("mousemove", "buildings", (e) => {
        map.getCanvas().style.cursor = "pointer";
        const id = e.features?.[0]?.id;
        if (hovered !== undefined) map.setFeatureState({ source: "buildings", id: hovered }, { hover: false });
        hovered = id;
        if (id !== undefined) map.setFeatureState({ source: "buildings", id }, { hover: true });
      });
      map.on("mouseleave", "buildings", () => {
        map.getCanvas().style.cursor = "";
        if (hovered !== undefined) map.setFeatureState({ source: "buildings", id: hovered }, { hover: false });
        hovered = undefined;
      });
      map.on("click", "buildings", (e) => {
        const f = e.features?.[0];
        if (f) new maplibregl.Popup({ maxWidth: "280px" }).setLngLat(e.lngLat).setHTML(popupHtml(f)).addTo(map);
      });
      map.on("click", "streets", (e) => {
        const p = e.features?.[0]?.properties;
        if (p && !map.queryRenderedFeatures(e.point, { layers: ["buildings"] }).length)
          new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<b>${p.name ?? "via sem nome"}</b><br/>${p.highway}`).addTo(map);
      });
    });

    return () => map.remove();
  }, []);

  return (
    <>
      <div id="map" ref={ref} />
      <div className="panel">
        <h1>Rua 3D · existente</h1>
        <small>Prédios extrudados pela cumeeira (p90 de MDS − MDT). Clique num prédio para ver as alturas.</small>
        <div style={{ marginTop: 8 }}>
          {HEIGHT_SOURCES.map(([k, c, label]) => (
            <div className="row" key={k}>
              <span className="sw" style={{ background: c }} />
              {label}
            </div>
          ))}
          <div className="row" style={{ marginTop: 4 }}>
            <span className="sw" style={{ background: "#ffd54f", outline: "2px dashed #e0a800" }} />
            Faixa de 30 m
          </div>
          <div className="row">
            <span className="sw" style={{ background: "#e53e3e", height: 4 }} />
            Vias (OSM)
          </div>
        </div>
      </div>
    </>
  );
}
