import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./map.css";
import { Layers, Maximize, Minimize, Minus, Navigation, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ORIGIN_COLOR, STATE_COLOR, ORIGIN_LABEL, STATE_LABEL } from "./lib";
import {
  ALL_VISIBLE, LAYER_GROUPS, MAP_ENV, OVERLAY_SOURCE_KEY, OVERLAY_SOURCES, TRACTOR_SVG, attributionCompact, baseStyle, boundsKey, composeStyle, computeBounds, effectiveTheme,
  fieldBounds, groupLayerIds, hasMap, hasSatellite, initialBasemap, isStyleJson, machineTitle, metersPerPixel, niceScale, overlayData, overlayLayers, shouldRefit,
  styleUrlFor, toFeatureCollection, type BasemapKind, type Bounds, type GeoInput, type LayerVisibility, type MapData, type MapTheme, type MapVariant,
} from "./mapConfig";

export type { Marker, MapData, MachinePos, BasemapKind, MapVariant } from "./mapConfig";

const STORE_KEY = "agf-basemap";
const UNAVAILABLE = "Mapa-base indisponível";
const FICTITIOUS = "Local fictício: o talhão simulado não corresponde à imagem de fundo";

const readStored = () => { try { return localStorage.getItem(STORE_KEY); } catch { return null; } };
const writeStored = (v: BasemapKind) => { try { localStorage.setItem(STORE_KEY, v); } catch { /* armazenamento indisponível: só não lembra a escolha */ } };
const reducedMotion = () => { try { return !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches; } catch { return false; } };

// Estilos vetoriais baixados uma vez por URL (falha é descartada do cache para tentar de novo na próxima troca).
const styleCache = new Map<string, Promise<maplibregl.StyleSpecification>>();
function loadStyle(url: string): Promise<maplibregl.StyleSpecification> {
  let p = styleCache.get(url);
  if (!p) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 10000);
    p = fetch(url, { signal: ctl.signal, credentials: "omit" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j) => { if (!isStyleJson(j)) throw new Error("estilo inválido"); return j; })
      .finally(() => clearTimeout(timer));
    p.catch(() => styleCache.delete(url));
    styleCache.set(url, p);
  }
  return p;
}

/** Tema efetivo observado diretamente no <html data-theme> e no prefers-color-scheme (desacoplado de theme.ts). */
function useEffectiveTheme(): MapTheme {
  const read = () => effectiveTheme(document.documentElement.getAttribute("data-theme"), (() => { try { return !!window.matchMedia?.("(prefers-color-scheme: dark)").matches; } catch { return false; } })());
  const [t, setT] = useState<MapTheme>(read);
  useEffect(() => {
    const on = () => setT(read());
    const mo = new MutationObserver(on);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    let mq: MediaQueryList | undefined;
    try { mq = window.matchMedia?.("(prefers-color-scheme: dark)"); mq?.addEventListener?.("change", on); } catch { /* sem matchMedia */ }
    return () => { mo.disconnect(); mq?.removeEventListener?.("change", on); };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  return t;
}

function pinElement(): HTMLDivElement {
  const d = document.createElement("div");
  d.className = "agf-pin";
  d.setAttribute("role", "img");
  d.dataset.testid = "machine-pin";
  d.innerHTML = `<span class="agf-pin-head">${TRACTOR_SVG}</span><span class="agf-pin-heading" aria-hidden="true"><i></i></span><span class="agf-pin-tip" aria-hidden="true"></span><span class="agf-pin-dot" aria-hidden="true"></span>`;
  return d;
}

export interface MapViewProps {
  data: MapData;
  /** Muda → reenquadra (talhão inteiro + áreas + trajetória + máquina). */
  fitKey?: string;
  height?: number;
  /** Mapa-base inicial/forçado; o usuário ainda pode trocar pelos controles. */
  basemap?: BasemapKind;
  variant?: MapVariant;
  /** Talhão simulado em local fictício: avisa que a imagem de fundo não corresponde a ele. */
  fictitious?: boolean;
  /** Controles próprios (Mapa | Satélite, zoom, localizar, camadas, tela cheia, escala). Padrão: só na variante "operation". */
  controls?: boolean;
  /** Ocupa 100% da altura do elemento pai (ignora `height`). */
  fill?: boolean;
  ariaLabel?: string;
}

export function MapView({ data, fitKey, height = 480, basemap: basemapProp, variant = "default", fictitious = false, controls, fill = false, ariaLabel }: MapViewProps) {
  const showControls = controls ?? variant === "operation";
  const theme = useEffectiveTheme();
  const wrap = useRef<HTMLDivElement>(null);
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<Map<string, maplibregl.Marker>>(new Map());
  const pin = useRef<maplibregl.Marker | null>(null);
  const attrib = useRef<{ ctl: maplibregl.AttributionControl; compact: boolean } | null>(null);
  const dataRef = useRef<MapData>(data);
  const lastRaw = useRef<Record<string, unknown>>({});
  const styleReady = useRef(false);
  const gen = useRef(0);
  const baseErrors = useRef(0);
  const fitState = useRef<{ key?: string; fieldKey: string } | null>(null);
  const [basemap, setBasemap] = useState<BasemapKind>(() => initialBasemap(basemapProp, readStored(), MAP_ENV));
  const [shown, setShown] = useState<BasemapKind>("none");
  const [notice, setNotice] = useState<string | null>(null);
  const [vis, setVis] = useState<LayerVisibility>(ALL_VISIBLE);
  const visRef = useRef(vis);
  const [layersOpen, setLayersOpen] = useState(false);
  const [scale, setScale] = useState<{ px: number; labels: [string, string, string] } | null>(null);
  const [fs, setFs] = useState(false);
  const [pseudoFs, setPseudoFs] = useState(false);
  dataRef.current = data;

  useEffect(() => { if (basemapProp) setBasemap(initialBasemap(basemapProp, null, MAP_ENV)); }, [basemapProp]);

  /** Envia aos GeoJSON sources só o que mudou (ou tudo, com force). Protegido contra a janela de troca de estilo. */
  const applySources = useCallback((force: boolean) => {
    const m = map.current;
    if (!m || !styleReady.current) return;
    const d = dataRef.current;
    for (const s of OVERLAY_SOURCES) {
      const raw = d[OVERLAY_SOURCE_KEY[s]];
      if (!force && lastRaw.current[s] === raw) continue;
      const src = m.getSource(s) as maplibregl.GeoJSONSource | undefined;
      if (!src || typeof src.setData !== "function") continue;
      src.setData(toFeatureCollection(raw as GeoInput));
      lastRaw.current[s] = raw;
    }
  }, []);

  const updateScale = useCallback(() => {
    const m = map.current;
    if (!m) return;
    const s = niceScale(metersPerPixel(m.getCenter().lat, m.getZoom()), 110);
    setScale((p) => (p && s && p.px === s.px && p.labels[2] === s.labels[2] ? p : s));
  }, []);

  // Mapa (uma vez). Começa no fundo neutro; o efeito de estilo abaixo aplica o mapa-base escolhido.
  useEffect(() => {
    const style = composeStyle(baseStyle("none", MAP_ENV, theme), overlayLayers(variant, theme, visRef.current, STATE_COLOR), {});
    const m = new maplibregl.Map({ container: el.current!, style, center: [-47.6, -16.7], zoom: 12, maxZoom: 19,
      attributionControl: false, dragRotate: false, pitchWithRotate: false, touchPitch: false });   // sem rotação: o rumo do marcador fica correto e o norte, para cima
    m.touchZoomRotate.disableRotation();
    const compact = attributionCompact(basemap, MAP_ENV);
    const ctl = new maplibregl.AttributionControl({ compact });
    m.addControl(ctl, "bottom-right");
    attrib.current = { ctl, compact };
    if (!showControls) {
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    }
    m.on("style.load", () => { styleReady.current = true; applySources(true); });
    m.on("error", (e: any) => {
      const sid: string | undefined = e?.sourceId ?? e?.source?.id;
      if (sid && !(OVERLAY_SOURCES as readonly string[]).includes(sid)) {   // tiles/estilo do mapa-base: as camadas próprias seguem
        baseErrors.current += 1;
        if (baseErrors.current >= 4) setNotice(UNAVAILABLE);
        return;
      }
      console.warn("[mapa]", e?.error?.message ?? e);
    });
    m.on("move", updateScale);
    m.on("load", updateScale);
    map.current = m;
    return () => {
      markers.current.forEach((x) => x.remove()); markers.current.clear();
      pin.current?.remove(); pin.current = null;
      m.remove(); map.current = null; styleReady.current = false;
    };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // Mapa-base / tema / variante → recompõe o estilo inteiro (as fontes próprias vão já com os dados atuais; marcadores são DOM e ficam).
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const g = ++gen.current;
    const apply = (kind: BasemapKind, vector: maplibregl.StyleSpecification | null, failed: boolean) => {
      if (g !== gen.current || !map.current) return;
      const style = composeStyle(baseStyle(kind, MAP_ENV, theme, vector), overlayLayers(variant, theme, visRef.current, STATE_COLOR), overlayData(dataRef.current));
      styleReady.current = false;
      baseErrors.current = 0;
      lastRaw.current = {};
      m.setStyle(style, { diff: false });
      setShown(kind);
      setNotice(failed ? UNAVAILABLE : null);
    };
    const url = basemap === "map" ? styleUrlFor(MAP_ENV, theme) : null;
    if (url) loadStyle(url).then((v) => apply("map", v, false), () => apply("none", null, true));
    else apply(basemap, null, false);
  }, [basemap, theme, variant]);

  // Atribuição: recolhível ("i"), exceto com tiles do tile.openstreetmap.org (política do OSMF).
  useEffect(() => {
    const m = map.current, a = attrib.current;
    const compact = attributionCompact(shown, MAP_ENV);
    if (!m || !a || a.compact === compact) return;
    m.removeControl(a.ctl);
    const ctl = new maplibregl.AttributionControl({ compact });
    m.addControl(ctl, "bottom-right");
    attrib.current = { ctl, compact };
  }, [shown]);

  // Visibilidade dos grupos do painel "Camadas" (sem recarregar o estilo).
  useEffect(() => {
    visRef.current = vis;
    const m = map.current;
    if (!m || !styleReady.current) return;
    const layers = overlayLayers(variant, theme, vis);
    for (const g of LAYER_GROUPS) for (const id of groupLayerIds(layers, g.id)) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", vis[g.id] ? "visible" : "none");
  }, [vis]);   // eslint-disable-line react-hooks/exhaustive-deps

  const fitTo = useCallback((b: Bounds, animate: boolean) => {
    const m = map.current;
    if (!m) return;
    const padding = showControls ? { top: 64, left: 72, right: 32, bottom: 48 } : 40;
    m.fitBounds(b, { padding, duration: animate && !reducedMotion() ? 600 : 0, maxZoom: 17 });
  }, [showControls]);

  // Dados → fontes, marcadores e enquadramento.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    applySources(false);

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

    const mc = data.machine;
    if (mc && Number.isFinite(mc.lon) && Number.isFinite(mc.lat)) {
      if (!pin.current) pin.current = new maplibregl.Marker({ element: pinElement(), anchor: "bottom", offset: [0, 7] }).setLngLat([mc.lon, mc.lat]).addTo(m);
      pin.current.setLngLat([mc.lon, mc.lat]);
      const d = pin.current.getElement();
      const t = machineTitle(mc, STATE_LABEL, ORIGIN_LABEL);
      d.title = t; d.setAttribute("aria-label", t);
      d.dataset.state = mc.state; d.dataset.origin = mc.origin;
      const h = d.querySelector<HTMLElement>(".agf-pin-heading");
      const hasHeading = typeof mc.heading_deg === "number" && Number.isFinite(mc.heading_deg);
      if (h) { h.style.display = hasHeading ? "" : "none"; if (hasHeading) h.style.transform = `rotate(${mc.heading_deg}deg)`; }
    } else if (pin.current) { pin.current.remove(); pin.current = null; }

    const b = computeBounds(data);
    const fieldKey = boundsKey(fieldBounds(data));
    if (b && shouldRefit(fitState.current, fitKey, fieldKey, true)) { fitTo(b, false); fitState.current = { key: fitKey, fieldKey }; }
  }, [data, fitKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Tela cheia (Fullscreen API; sem ela, um modo "tela cheia" por CSS).
  useEffect(() => {
    const on = () => { setFs(document.fullscreenElement === wrap.current); setTimeout(() => map.current?.resize(), 50); };
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  useEffect(() => {
    setTimeout(() => map.current?.resize(), 50);
    if (!pseudoFs) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setPseudoFs(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [pseudoFs]);
  const toggleFs = () => {
    const w = wrap.current as (HTMLDivElement & { webkitRequestFullscreen?: () => void }) | null;
    if (!w) return;
    if (pseudoFs) { setPseudoFs(false); return; }
    if (document.fullscreenElement) { document.exitFullscreen?.().catch(() => undefined); return; }
    if (w.requestFullscreen) w.requestFullscreen().catch(() => setPseudoFs(true));
    else if (w.webkitRequestFullscreen) w.webkitRequestFullscreen();
    else setPseudoFs(true);
  };

  // Fecha o painel de camadas com Esc ou clique fora.
  const layersBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!layersOpen) return;
    const down = (e: PointerEvent) => { if (!layersBox.current?.contains(e.target as Node)) setLayersOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setLayersOpen(false); };
    document.addEventListener("pointerdown", down); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", down); document.removeEventListener("keydown", key); };
  }, [layersOpen]);

  const choose = (k: BasemapKind) => { setBasemap(k); writeStored(k); };
  const locate = () => {
    const m = map.current;
    if (!m) return;
    const mc = dataRef.current.machine;
    if (mc && Number.isFinite(mc.lon) && Number.isFinite(mc.lat)) {
      const opts = { center: [mc.lon, mc.lat] as [number, number], zoom: Math.max(m.getZoom(), 16) };
      if (reducedMotion()) m.jumpTo(opts); else m.flyTo({ ...opts, duration: 800 });
      return;
    }
    const b = fieldBounds(dataRef.current) ?? computeBounds(dataRef.current);
    if (b) fitTo(b, true);
  };

  const fullscreen = fs || pseudoFs;
  const sat = MAP_ENV.satellite;
  const cls = ["agf-map", `agf-map--${variant}`, `agf-map--${theme}`, showControls ? "agf-map--controls" : "", pseudoFs ? "agf-map--fs-fallback" : ""].filter(Boolean).join(" ");
  return (
    <div ref={wrap} className={cls} style={fill ? { height: "100%" } : { height }} data-basemap={shown}>
      <div ref={el} className="map" data-testid="map" role="img" aria-label={ariaLabel ?? (variant === "operation" ? "Mapa da operação" : "Mapa da frota")} />
      {showControls && (<>
        <div className="agf-seg" role="group" aria-label="Mapa-base">
          <button type="button" aria-pressed={basemap === "map"} disabled={!hasMap(MAP_ENV)} onClick={() => choose("map")}
            title={hasMap(MAP_ENV) ? "Mapa digital (OpenStreetMap)" : "Mapa digital não configurado (VITE_MAP_STYLE_URL)"}>Mapa</button>
          <button type="button" aria-pressed={basemap === "satellite"} disabled={!hasSatellite(MAP_ENV)} onClick={() => choose("satellite")}
            title={sat ? `Imagem de satélite${sat.label ? ` (${sat.label})` : ""}` : "Satélite indisponível (configure VITE_SATELLITE_TILES)"}>Satélite</button>
        </div>
        <div className="agf-tools">
          <button type="button" className="agf-ctl" aria-label="Aproximar" title="Aproximar" onClick={() => map.current?.zoomIn()}><Plus size={20} aria-hidden /></button>
          <button type="button" className="agf-ctl" aria-label="Afastar" title="Afastar" onClick={() => map.current?.zoomOut()}><Minus size={20} aria-hidden /></button>
          <button type="button" className="agf-ctl" aria-label={data.machine ? "Centralizar na máquina" : "Enquadrar o talhão"} title={data.machine ? "Centralizar na máquina" : "Enquadrar o talhão"} onClick={locate}>
            <Navigation size={18} aria-hidden /></button>
          <div className="agf-layers" ref={layersBox}>
            <button type="button" className="agf-ctl" aria-label="Camadas" title="Camadas" aria-expanded={layersOpen} aria-controls="agf-layers-pop" onClick={() => setLayersOpen((o) => !o)}>
              <Layers size={18} aria-hidden /></button>
            {layersOpen && (
              <fieldset className="agf-pop" id="agf-layers-pop">
                <legend>Camadas</legend>
                {LAYER_GROUPS.map((g) => (
                  <label key={g.id}><input type="checkbox" checked={vis[g.id]} onChange={(e) => setVis((v) => ({ ...v, [g.id]: e.target.checked }))} /> {g.label}</label>))}
              </fieldset>)}
          </div>
          <button type="button" className="agf-ctl" aria-label={fullscreen ? "Sair da tela cheia" : "Tela cheia"} title={fullscreen ? "Sair da tela cheia" : "Tela cheia"} aria-pressed={fullscreen} onClick={toggleFs}>
            {fullscreen ? <Minimize size={18} aria-hidden /> : <Maximize size={18} aria-hidden />}</button>
        </div>
        {scale && (
          <div className="agf-scale" aria-label={`Escala: ${scale.labels[2]} = ${scale.px} pixels`} role="img">
            <div className="agf-scale-labels" style={{ width: scale.px }}><span>{scale.labels[0]}</span><span>{scale.labels[1]}</span><span>{scale.labels[2]}</span></div>
            <div className="agf-scale-bar" style={{ width: scale.px }} />
          </div>)}
      </>)}
      <div className="agf-chips">
        {shown === "satellite" && sat?.label && <span className="agf-chip" title="Data e resolução da imagem de satélite (não é a condição atual do talhão)">{sat.label}</span>}
        {fictitious && (shown === "map" || shown === "satellite") && <span className="agf-chip agf-chip--warn" role="note" data-testid="map-fictitious">{FICTITIOUS}</span>}
        {notice && <span className="agf-chip agf-chip--notice" role="status" data-testid="map-notice" title="O fundo ficou neutro; as camadas da operação continuam no mapa.">{notice}</span>}
      </div>
    </div>
  );
}

export function Legend() {
  return (
    <div className="legend" aria-label="legenda">
      {Object.keys(STATE_COLOR).filter((k) => k !== "off").map((k) => (<span key={k}><i style={{ background: STATE_COLOR[k] }} />{STATE_LABEL[k]}</span>))}
      <span className="legend-origin">contorno do marcador: <b style={{ color: ORIGIN_COLOR.simulated }}>âmbar = simulado</b> · <b style={{ color: ORIGIN_COLOR.real }}>verde = real declarado</b> · <b style={{ color: ORIGIN_COLOR.unknown }}>cinza = não declarado</b></span>
    </div>
  );
}
