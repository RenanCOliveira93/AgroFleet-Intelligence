import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { ORIGIN_COLOR, STATE_COLOR, ORIGIN_LABEL, STATE_LABEL } from "./lib";

export interface Marker { id: string; lon: number; lat: number; state: string; origin: string; label?: string }
export interface MapData {
  fields?: GeoJSON.FeatureCollection | null;
  tracks?: GeoJSON.FeatureCollection | null;
  union?: GeoJSON.Geometry | null;
  overlap?: GeoJSON.Geometry | null;
  markers?: Marker[];
}
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const BASEMAP = import.meta.env.VITE_BASEMAP_TILES as string | undefined;   // opcional (ex.: um servidor de tiles seu); padrão: sem mapa-base

export function MapView({ data, fitKey, height = 480 }: { data: MapData; fitKey?: string; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<Map<string, maplibregl.Marker>>(new Map());
  const ready = useRef(false);
  const lastFit = useRef<string | undefined>();

  useEffect(() => {
    const sources: maplibregl.StyleSpecification["sources"] = {};
    const layers: maplibregl.StyleSpecification["layers"] = [{ id: "bg", type: "background", paint: { "background-color": "#e8eee4" } }];
    if (BASEMAP) { sources.base = { type: "raster", tiles: [BASEMAP], tileSize: 256, attribution: (import.meta.env.VITE_BASEMAP_ATTRIBUTION as string) ?? "" }; layers.push({ id: "base", type: "raster", source: "base" }); }
    const m = new maplibregl.Map({ container: el.current!, style: { version: 8, sources, layers }, center: [-47.6, -16.7], zoom: 12, attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    m.on("load", () => {
      m.addSource("fields", { type: "geojson", data: EMPTY }); m.addSource("union", { type: "geojson", data: EMPTY });
      m.addSource("overlap", { type: "geojson", data: EMPTY }); m.addSource("tracks", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "fields-fill", type: "fill", source: "fields", paint: { "fill-color": "#ffffff", "fill-opacity": 0.35 } });
      m.addLayer({ id: "union-fill", type: "fill", source: "union", paint: { "fill-color": "#2e7d32", "fill-opacity": 0.35 } });
      m.addLayer({ id: "overlap-fill", type: "fill", source: "overlap", paint: { "fill-color": "#c62828", "fill-opacity": 0.55 } });
      m.addLayer({ id: "fields-line", type: "line", source: "fields", paint: { "line-color": "#455a64", "line-width": 1.5, "line-dasharray": [3, 2] } });
      m.addLayer({ id: "tracks-line", type: "line", source: "tracks", paint: { "line-width": ["match", ["get", "state"], "working", 2.4, 1.1] as any, "line-opacity": ["match", ["get", "state"], "working", 0.95, 0.4] as any,
        "line-color": ["match", ["get", "state"], ...Object.entries(STATE_COLOR).flat(), "#9e9e9e"] as any } });
      ready.current = true;
      m.fire("agf-ready" as any);
    });
    map.current = m;
    return () => { markers.current.forEach((x) => x.remove()); markers.current.clear(); m.remove(); ready.current = false; };
  }, []);

  useEffect(() => {
    const m = map.current!;
    const apply = () => {
      const fc = (g?: GeoJSON.Geometry | null): GeoJSON.FeatureCollection => (g ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: g }] } : EMPTY);
      (m.getSource("fields") as maplibregl.GeoJSONSource).setData(data.fields ?? EMPTY);
      (m.getSource("tracks") as maplibregl.GeoJSONSource).setData(data.tracks ?? EMPTY);
      (m.getSource("union") as maplibregl.GeoJSONSource).setData(fc(data.union));
      (m.getSource("overlap") as maplibregl.GeoJSONSource).setData(fc(data.overlap));
      const seen = new Set<string>();
      for (const k of data.markers ?? []) {
        const key = `${k.id}|${k.origin}`; seen.add(key);
        let mk = markers.current.get(key);
        if (!mk) {
          const d = document.createElement("div"); d.className = "mk"; d.dataset.testid = `marker-${k.id}`;
          mk = new maplibregl.Marker({ element: d }).setLngLat([k.lon, k.lat]).addTo(m); markers.current.set(key, mk);
        }
        mk.setLngLat([k.lon, k.lat]);
        const d = mk.getElement();
        d.style.background = STATE_COLOR[k.state] ?? STATE_COLOR.unknown;
        d.style.borderColor = ORIGIN_COLOR[k.origin] ?? ORIGIN_COLOR.unknown;
        d.title = `${k.id} · ${STATE_LABEL[k.state] ?? k.state} · ${ORIGIN_LABEL[k.origin] ?? k.origin}`;
        d.textContent = (k.label ?? k.id.replace(/^\D*/, "")).slice(0, 3);
      }
      markers.current.forEach((mk, key) => { if (!seen.has(key)) { mk.remove(); markers.current.delete(key); } });
      if (fitKey !== undefined && fitKey !== lastFit.current) {
        const b = new maplibregl.LngLatBounds();
        const add = (c: any) => Array.isArray(c[0]) ? c.forEach(add) : b.extend(c as [number, number]);
        data.fields?.features.forEach((f) => add((f.geometry as any).coordinates));
        data.tracks?.features.forEach((f) => add((f.geometry as any).coordinates));
        if (data.union) add((data.union as any).coordinates);
        (data.markers ?? []).forEach((k) => b.extend([k.lon, k.lat]));
        if (!b.isEmpty()) { m.fitBounds(b, { padding: 40, duration: 0, maxZoom: 17 }); lastFit.current = fitKey; }
      }
    };
    if (ready.current) apply(); else m.once("agf-ready" as any, apply);
  }, [data, fitKey]);

  return <div ref={el} className="map" style={{ height }} data-testid="map" role="img" aria-label="Mapa da frota" />;
}

export function Legend() {
  return (
    <div className="legend" aria-label="legenda">
      {Object.keys(STATE_COLOR).filter((k) => k !== "off").map((k) => (<span key={k}><i style={{ background: STATE_COLOR[k] }} />{STATE_LABEL[k]}</span>))}
      <span className="legend-origin">contorno do marcador: <b style={{ color: ORIGIN_COLOR.simulated }}>âmbar = simulado</b> · <b style={{ color: ORIGIN_COLOR.real }}>verde = real declarado</b> · <b style={{ color: ORIGIN_COLOR.unknown }}>cinza = não declarado</b></span>
    </div>
  );
}
