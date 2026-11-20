/**
 * Configuração e lógica pura do mapa (sem MapLibre em tempo de execução, para poder ser testada no jsdom).
 *
 * Mapas-base (decisão A da Etapa 8, ver frontend/.env.example):
 * - "map": estilo vetorial (padrão OpenFreeMap liberty; variante escura para o tema escuro) ou o raster legado
 *   VITE_BASEMAP_TILES (compatibilidade);
 * - "satellite": raster XYZ (padrão EOxCloudless 2016, CC BY 4.0, uso comercial permitido);
 * - "none": fundo liso na cor do tema (offline). Também é o recuo automático quando o estilo não carrega.
 * Nenhuma imagem é sintetizada: sem mapa-base, o fundo fica vazio e as camadas da operação continuam funcionando.
 */
import type { LayerSpecification, SourceSpecification, StyleSpecification } from "maplibre-gl";

export type BasemapKind = "map" | "satellite" | "none";
export type MapTheme = "light" | "dark";
export type MapVariant = "default" | "operation";

export interface Marker { id: string; lon: number; lat: number; state: string; origin: string; label?: string }
export interface MachinePos { lon: number; lat: number; heading_deg?: number | null; state: string; label?: string; origin: string; id?: string }
/** Geometria aceita pelo mapa: FeatureCollection, Feature ou Geometry GeoJSON (a API de geometria devolve Geometry). */
export type GeoInput = GeoJSON.FeatureCollection | GeoJSON.Feature | GeoJSON.Geometry | null | undefined;
export interface MapData {
  fields?: GeoInput;
  tracks?: GeoInput;
  union?: GeoInput;
  overlap?: GeoInput;
  /** Área trabalhada dentro do talhão (GET /operations/{id}/geometry → worked). */
  worked?: GeoInput;
  /** Área pendente do talhão (→ pending). */
  pending?: GeoInput;
  /** Cobertura fora do talhão (→ outside). Extensão opcional do contrato C. */
  outside?: GeoInput;
  markers?: Marker[];
  /** Posição da máquina da operação (marcador em pino). */
  machine?: MachinePos | null;
}

// ---------------------------------------------------------------- ambiente (Vite)

export const DEFAULT_MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
export const DEFAULT_MAP_STYLE_DARK = "https://tiles.openfreemap.org/styles/dark";   // verificado: HTTP 200 (também existem positron, bright, fiord)
export const DEFAULT_SATELLITE_TILES = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg";
export const DEFAULT_SATELLITE_ATTRIBUTION =
  '<a href="https://cloudless.eox.at" target="_blank" rel="noopener">EOxCloudless 2016</a> by <a href="https://eox.at" target="_blank" rel="noopener">EOX IT Services GmbH</a> ' +
  '(Contains modified Copernicus Sentinel data 2016) · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">CC BY 4.0</a>';
export const DEFAULT_SATELLITE_LABEL = "Sentinel-2 · 2016 · 10 m";
export const OSM_ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>';

export interface MapEnvConfig {
  /** Estilo vetorial do "Mapa" no tema claro e no escuro (null = sem estilo vetorial). */
  mapStyleUrl: string | null;
  mapStyleUrlDark: string | null;
  /** Raster legado do "Mapa" (VITE_BASEMAP_TILES), usado quando não há estilo vetorial. */
  legacyTiles: string | null;
  legacyAttribution: string;
  satellite: { tiles: string; attribution: string; maxzoom: number; tileSize: number; label: string } | null;
  defaultBasemap: BasemapKind;
}

type Env = Record<string, string | boolean | undefined>;
const str = (env: Env, k: string): string | undefined => {   // undefined = não definida; "" = definida vazia (desliga)
  const v = env[k];
  return typeof v === "string" ? v.trim() : undefined;
};
const off = (v: string | undefined) => v !== undefined && (v === "" || v.toLowerCase() === "none" || v.toLowerCase() === "off");
const num = (v: string | undefined, dflt: number, min: number, max: number) => {
  const n = v === undefined || v === "" ? NaN : Number(v);
  return Number.isFinite(n) && n >= min && n <= max ? Math.round(n) : dflt;
};

/** Lê as variáveis VITE_* do mapa com os padrões documentados em .env.example. Variável vazia ("") desliga a opção. */
export function readMapEnv(env: Env): MapEnvConfig {
  const styleRaw = str(env, "VITE_MAP_STYLE_URL");
  const legacyRaw = str(env, "VITE_BASEMAP_TILES");
  const legacyTiles = legacyRaw && !off(legacyRaw) ? legacyRaw : null;
  // Ordem: VITE_MAP_STYLE_URL explícita > raster legado VITE_BASEMAP_TILES > OpenFreeMap (só se VITE_MAP_STYLE_URL não foi definida).
  let mapStyleUrl: string | null;
  if (styleRaw !== undefined) mapStyleUrl = off(styleRaw) ? null : styleRaw;
  else mapStyleUrl = legacyTiles ? null : DEFAULT_MAP_STYLE;
  const darkRaw = str(env, "VITE_MAP_STYLE_URL_DARK");
  let mapStyleUrlDark: string | null = mapStyleUrl;
  if (mapStyleUrl && darkRaw && !off(darkRaw)) mapStyleUrlDark = darkRaw;
  else if (mapStyleUrl === DEFAULT_MAP_STYLE && darkRaw === undefined) mapStyleUrlDark = DEFAULT_MAP_STYLE_DARK;   // estilo próprio: não adivinha a variante escura

  const legacyAttrRaw = str(env, "VITE_BASEMAP_ATTRIBUTION");
  const legacyAttribution = legacyAttrRaw || (legacyTiles && isOsmTileServer(legacyTiles) ? OSM_ATTRIBUTION : "");

  const satRaw = str(env, "VITE_SATELLITE_TILES");
  const satTiles = satRaw === undefined ? DEFAULT_SATELLITE_TILES : off(satRaw) ? null : satRaw;
  const isDefaultSat = satTiles === DEFAULT_SATELLITE_TILES;
  const satellite = satTiles
    ? {
        tiles: satTiles,
        attribution: str(env, "VITE_SATELLITE_ATTRIBUTION") || (isDefaultSat ? DEFAULT_SATELLITE_ATTRIBUTION : ""),
        maxzoom: num(str(env, "VITE_SATELLITE_MAXZOOM"), 14, 0, 24),
        tileSize: num(str(env, "VITE_SATELLITE_TILESIZE"), 256, 64, 1024),
        label: str(env, "VITE_SATELLITE_LABEL") ?? (isDefaultSat ? DEFAULT_SATELLITE_LABEL : ""),
      }
    : null;
  const cfg: MapEnvConfig = { mapStyleUrl, mapStyleUrlDark, legacyTiles, legacyAttribution, satellite, defaultBasemap: "map" };
  const dflt = parseBasemap(str(env, "VITE_BASEMAP_DEFAULT")) ?? "map";
  cfg.defaultBasemap = resolveBasemap(dflt, cfg);
  return cfg;
}

export function parseBasemap(v: unknown): BasemapKind | null {
  return v === "map" || v === "satellite" || v === "none" ? v : null;
}
export function isOsmTileServer(url: string | null | undefined): boolean { return !!url && /(^|\/\/|\.)tile\.openstreetmap\.org\//i.test(url); }
export function hasMap(cfg: MapEnvConfig): boolean { return !!(cfg.mapStyleUrl || cfg.legacyTiles); }
export function hasSatellite(cfg: MapEnvConfig): boolean { return !!cfg.satellite; }

/** Troca um mapa-base pedido e não configurado por outro disponível (map ↔ satellite), senão "none". */
export function resolveBasemap(kind: BasemapKind, cfg: MapEnvConfig): BasemapKind {
  if (kind === "map") return hasMap(cfg) ? "map" : hasSatellite(cfg) ? "satellite" : "none";
  if (kind === "satellite") return hasSatellite(cfg) ? "satellite" : hasMap(cfg) ? "map" : "none";
  return "none";
}
/** Mapa-base inicial: prop do componente > escolha guardada (localStorage "agf-basemap") > VITE_BASEMAP_DEFAULT. */
export function initialBasemap(prop: BasemapKind | undefined, stored: string | null | undefined, cfg: MapEnvConfig): BasemapKind {
  return resolveBasemap(prop ?? parseBasemap(stored) ?? cfg.defaultBasemap, cfg);
}
/** Configuração efetiva deste build (import.meta.env). */
export const MAP_ENV: MapEnvConfig = readMapEnv((import.meta as any).env ?? {});

/** A atribuição só pode ficar recolhida ("i") quando o servidor de tiles do OSM não está em uso (política do OSMF). */
export const attributionCompact = (kind: BasemapKind, cfg: MapEnvConfig) => !(kind === "map" && !cfg.mapStyleUrl && isOsmTileServer(cfg.legacyTiles));

/** Tema efetivo: data-theme no <html> (definido pelo seletor de tema) ou, sem atributo, a preferência do sistema. */
export function effectiveTheme(attr: string | null | undefined, prefersDark: boolean): MapTheme {
  if (attr === "dark" || attr === "light") return attr;
  return prefersDark ? "dark" : "light";
}

// ---------------------------------------------------------------- geometria

export const EMPTY_FC: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export function toFeatureCollection(x: GeoInput): GeoJSON.FeatureCollection {
  if (!x || typeof x !== "object") return EMPTY_FC;
  if (x.type === "FeatureCollection") return { type: "FeatureCollection", features: (x.features ?? []).filter((f) => f && f.geometry) };
  if (x.type === "Feature") return x.geometry ? { type: "FeatureCollection", features: [x] } : EMPTY_FC;
  return { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: x as GeoJSON.Geometry }] };
}

export type Bounds = [[number, number], [number, number]];

function eachPosition(g: GeoJSON.Geometry | null | undefined, fn: (p: GeoJSON.Position) => void) {
  if (!g) return;
  const walk = (c: any) => {
    if (!Array.isArray(c)) return;
    if (typeof c[0] === "number") fn(c as GeoJSON.Position);
    else c.forEach(walk);
  };
  if (g.type === "GeometryCollection") g.geometries.forEach((x) => eachPosition(x, fn));
  else walk((g as any).coordinates);
}

class BoundsAcc {
  minX = Infinity; minY = Infinity; maxX = -Infinity; maxY = -Infinity;
  add(lon: unknown, lat: unknown) {
    if (typeof lon !== "number" || typeof lat !== "number" || !Number.isFinite(lon) || !Number.isFinite(lat)) return;
    if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return;
    this.minX = Math.min(this.minX, lon); this.maxX = Math.max(this.maxX, lon); this.minY = Math.min(this.minY, lat); this.maxY = Math.max(this.maxY, lat);
  }
  addGeo(x: GeoInput) { toFeatureCollection(x).features.forEach((f) => eachPosition(f.geometry, (p) => this.add(p[0], p[1]))); }
  result(): Bounds | null { return this.minX === Infinity ? null : [[this.minX, this.minY], [this.maxX, this.maxY]]; }
}

/** Retângulo que contém TODO o talhão (Polygon e MultiPolygon), as áreas trabalhada/pendente/fora, a trajetória, a cobertura e a máquina. */
export function computeBounds(data: MapData): Bounds | null {
  const b = new BoundsAcc();
  for (const k of ["fields", "worked", "pending", "outside", "tracks", "union", "overlap"] as const) b.addGeo(data[k]);
  (data.markers ?? []).forEach((m) => b.add(m.lon, m.lat));
  if (data.machine) b.add(data.machine.lon, data.machine.lat);
  return b.result();
}
/** Retângulo só do(s) talhão(ões). */
export function fieldBounds(data: MapData): Bounds | null {
  const b = new BoundsAcc();
  b.addGeo(data.fields);
  return b.result();
}
export const boundsKey = (b: Bounds | null) => (b ? b.flat().map((v) => v.toFixed(6)).join(",") : "");

/**
 * Decide se o mapa deve se reenquadrar. Regra: reenquadra quando `fitKey` muda (e há algo para enquadrar).
 * Exceção para o bug da Cobertura (docs/EVOLUCAO.md): se, com o mesmo `fitKey`, o polígono do talhão chegou ou mudou
 * depois do primeiro enquadramento, reenquadra uma vez para incluir o talhão inteiro. Atualizações ao vivo
 * (trajetória, área trabalhada, máquina) não reenquadram, para não brigar com o usuário.
 */
export function shouldRefit(prev: { key?: string; fieldKey: string } | null, fitKey: string | undefined, fieldKey: string, hasBounds: boolean): boolean {
  if (fitKey === undefined || !hasBounds) return false;
  if (!prev || prev.key !== fitKey) return true;
  return fieldKey !== "" && fieldKey !== prev.fieldKey;
}

// ---------------------------------------------------------------- escala gráfica

/** Metros por pixel CSS no MapLibre (tiles de 512 px) na latitude e no zoom dados. */
export const metersPerPixel = (lat: number, zoom: number) => (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom);

const fmtNum = (v: number) => v.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
/** Escala "0 — 250 — 500 m": comprimento redondo (1, 2, 2,5 ou 5 × 10ⁿ) que cabe em `maxPx`. */
export function niceScale(mpp: number, maxPx = 120): { px: number; labels: [string, string, string] } | null {
  if (!Number.isFinite(mpp) || mpp <= 0) return null;
  const maxM = mpp * maxPx;
  const pow = 10 ** Math.floor(Math.log10(maxM));
  const step = [5, 2.5, 2, 1].map((s) => s * pow).find((s) => s <= maxM) ?? pow;
  const km = step >= 1000;
  const u = km ? 1000 : 1;
  return { px: Math.round(step / mpp), labels: ["0", fmtNum(step / 2 / u), `${fmtNum(step / u)} ${km ? "km" : "m"}`] };
}

// ---------------------------------------------------------------- estilos

export const BG_COLOR: Record<MapTheme, string> = { light: "#e8eee4", dark: "#0E1A17" };
export const BASE_SOURCES = new Set(["base", "sat"]);   // além das fontes de um estilo vetorial carregado

export function isStyleJson(x: any): x is StyleSpecification {
  return !!x && typeof x === "object" && x.version === 8 && Array.isArray(x.layers) && !!x.sources && typeof x.sources === "object";
}

/** URL do estilo vetorial para o tema (null = "Mapa" usa o raster legado ou não existe). */
export const styleUrlFor = (cfg: MapEnvConfig, theme: MapTheme) => (theme === "dark" ? cfg.mapStyleUrlDark : cfg.mapStyleUrl);

/**
 * Estilo-base (sem as camadas da operação). `vector` é o JSON já baixado do estilo vetorial; quando o "Mapa" é vetorial
 * e o JSON não está disponível (falha), use kind "none".
 */
export function baseStyle(kind: BasemapKind, cfg: MapEnvConfig, theme: MapTheme, vector?: StyleSpecification | null): StyleSpecification {
  const bg: LayerSpecification = { id: "bg", type: "background", paint: { "background-color": BG_COLOR[theme] } };
  if (kind === "map" && vector) return { ...vector, sources: { ...vector.sources }, layers: [...vector.layers] };
  if (kind === "map" && cfg.legacyTiles) {
    return { version: 8, sources: { base: { type: "raster", tiles: [cfg.legacyTiles], tileSize: 256, maxzoom: 19, attribution: cfg.legacyAttribution } },
      layers: [bg, { id: "base", type: "raster", source: "base" }] };
  }
  if (kind === "satellite" && cfg.satellite) {
    const s = cfg.satellite;
    return { version: 8, sources: { sat: { type: "raster", tiles: [s.tiles], tileSize: s.tileSize, maxzoom: s.maxzoom, attribution: s.attribution } },
      layers: [bg, { id: "sat", type: "raster", source: "sat", paint: { "raster-fade-duration": 150 } }] };
  }
  return { version: 8, sources: {}, layers: [bg] };
}

export type LayerGroup = "track" | "worked" | "pending" | "outside";
export const LAYER_GROUPS: { id: LayerGroup; label: string }[] = [
  { id: "track", label: "Trajetória" }, { id: "worked", label: "Área trabalhada" }, { id: "pending", label: "Área pendente" }, { id: "outside", label: "Fora do talhão" },
];
export type LayerVisibility = Record<LayerGroup, boolean>;
export const ALL_VISIBLE: LayerVisibility = { track: true, worked: true, pending: true, outside: true };

/** Fontes GeoJSON das camadas próprias (sempre presentes, mesmo vazias). */
export const OVERLAY_SOURCES = ["fields", "pending", "worked", "outside", "union", "overlap", "tracks"] as const;
export type OverlaySource = (typeof OVERLAY_SOURCES)[number];
export const OVERLAY_SOURCE_KEY: Record<OverlaySource, keyof MapData> = { fields: "fields", pending: "pending", worked: "worked", outside: "outside", union: "union", overlap: "overlap", tracks: "tracks" };
export const overlayData = (data: MapData): Record<OverlaySource, GeoJSON.FeatureCollection> =>
  Object.fromEntries(OVERLAY_SOURCES.map((s) => [s, toFeatureCollection(data[OVERLAY_SOURCE_KEY[s]] as GeoInput)])) as Record<OverlaySource, GeoJSON.FeatureCollection>;

const NEON = "#4ADE3B";
type Layer = LayerSpecification & { metadata?: { group?: LayerGroup } };

/** Camadas próprias, de baixo para cima. `stateColors` colore a trajetória por estado na variante padrão. */
export function overlayLayers(variant: MapVariant, theme: MapTheme, vis: LayerVisibility, stateColors: Record<string, string> = {}): Layer[] {
  const v = (g: LayerGroup) => ({ visibility: vis[g] ? "visible" : "none" }) as const;
  const stateMatch = ["match", ["get", "state"], ...Object.entries(stateColors).flat(), theme === "dark" ? "#9BB0AA" : "#9e9e9e"] as any;
  if (variant === "operation") {
    const zw = (a: number, b: number) => ["interpolate", ["linear"], ["zoom"], 12, a, 17, b] as any;
    return [
      { id: "fields-fill", type: "fill", source: "fields", paint: { "fill-color": NEON, "fill-opacity": 0.06 } },
      { id: "pending-fill", type: "fill", source: "pending", layout: v("pending"), metadata: { group: "pending" }, paint: { "fill-color": "#DFF7D8", "fill-opacity": 0.07 } },
      { id: "worked-fill", type: "fill", source: "worked", layout: v("worked"), metadata: { group: "worked" }, paint: { "fill-color": "#8CF06F", "fill-opacity": 0.4 } },
      { id: "outside-fill", type: "fill", source: "outside", layout: v("outside"), metadata: { group: "outside" }, paint: { "fill-color": "#F5B800", "fill-opacity": 0.22 } },
      { id: "outside-line", type: "line", source: "outside", layout: v("outside"), metadata: { group: "outside" }, paint: { "line-color": "#F5B800", "line-width": 1, "line-opacity": 0.9 } },
      { id: "union-fill", type: "fill", source: "union", paint: { "fill-color": "#8CF06F", "fill-opacity": 0.35 } },
      { id: "overlap-fill", type: "fill", source: "overlap", paint: { "fill-color": "#EF4444", "fill-opacity": 0.55 } },
      { id: "tracks-line", type: "line", source: "tracks", layout: { ...v("track"), "line-join": "round", "line-cap": "round" }, metadata: { group: "track" },
        paint: { "line-color": "#F1FFEC", "line-opacity": 0.38, "line-width": zw(0.4, 1.2) } },
      { id: "fields-glow", type: "line", source: "fields", layout: { "line-join": "round" }, paint: { "line-color": NEON, "line-width": 10, "line-blur": 7, "line-opacity": 0.55 } },
      { id: "fields-line", type: "line", source: "fields", layout: { "line-join": "round" }, paint: { "line-color": NEON, "line-width": 3 } },
    ];
  }
  const dark = theme === "dark";
  return [
    { id: "fields-fill", type: "fill", source: "fields", paint: { "fill-color": dark ? "#9BB0AA" : "#ffffff", "fill-opacity": dark ? 0.12 : 0.35 } },
    { id: "pending-fill", type: "fill", source: "pending", layout: v("pending"), metadata: { group: "pending" }, paint: { "fill-color": "#ffffff", "fill-opacity": 0.08 } },
    { id: "worked-fill", type: "fill", source: "worked", layout: v("worked"), metadata: { group: "worked" }, paint: { "fill-color": "#2e7d32", "fill-opacity": 0.4 } },
    { id: "outside-fill", type: "fill", source: "outside", layout: v("outside"), metadata: { group: "outside" }, paint: { "fill-color": "#F5B800", "fill-opacity": 0.25 } },
    { id: "union-fill", type: "fill", source: "union", paint: { "fill-color": "#2e7d32", "fill-opacity": 0.35 } },
    { id: "overlap-fill", type: "fill", source: "overlap", paint: { "fill-color": "#c62828", "fill-opacity": 0.55 } },
    { id: "fields-line", type: "line", source: "fields", paint: { "line-color": dark ? "#C8D6CF" : "#455a64", "line-width": 1.5, "line-dasharray": [3, 2] } },
    { id: "tracks-line", type: "line", source: "tracks", layout: v("track"), metadata: { group: "track" },
      paint: { "line-width": ["match", ["get", "state"], "working", 2.4, 1.1] as any, "line-opacity": ["match", ["get", "state"], "working", 0.95, 0.4] as any, "line-color": stateMatch } },
  ];
}
/** Ids das camadas de cada grupo do painel "Camadas". */
export const groupLayerIds = (layers: Layer[], g: LayerGroup) => layers.filter((l) => l.metadata?.group === g).map((l) => l.id);

/**
 * Estilo final = estilo-base + fontes GeoJSON próprias (já com os dados atuais) + camadas próprias por cima.
 * Ids próprios que colidirem com o estilo-base são substituídos (as camadas da operação sempre vencem).
 */
export function composeStyle(base: StyleSpecification, overlays: Layer[], data: Record<string, GeoJSON.FeatureCollection>): StyleSpecification {
  const own = new Set(overlays.map((l) => l.id));
  const sources: Record<string, SourceSpecification> = { ...base.sources };
  for (const s of OVERLAY_SOURCES) sources[s] = { type: "geojson", data: data[s] ?? EMPTY_FC };
  return { ...base, sources, layers: [...base.layers.filter((l) => !own.has(l.id) && !(OVERLAY_SOURCES as readonly string[]).includes((l as any).source)), ...overlays] };
}

// ---------------------------------------------------------------- marcador da máquina

/** Ícone "tractor" do Lucide (ISC, https://lucide.dev), como SVG estático para o marcador HTML. */
export const TRACTOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
  '<path d="m10 11 11 .9a1 1 0 0 1 .8 1.1l-.665 4.158a1 1 0 0 1-.988.842H20"/><path d="M16 18h-5"/><path d="M18 5a1 1 0 0 0-1 1v5.573"/>' +
  '<path d="M3 4h8.129a1 1 0 0 1 .99.863L13 11.246"/><path d="M4 11V4"/><path d="M7 15h.01"/><path d="M8 10.1V4"/>' +
  '<circle cx="18" cy="18" r="2"/><circle cx="7" cy="15" r="5"/></svg>';

/** Texto de title/aria-label do marcador da máquina (identificação, estado e origem). */
export function machineTitle(m: MachinePos, stateLabel: Record<string, string> = {}, originLabel: Record<string, string> = {}): string {
  const who = m.label || m.id || "Máquina";
  const head = typeof m.heading_deg === "number" && Number.isFinite(m.heading_deg) ? ` · rumo ${Math.round(((m.heading_deg % 360) + 360) % 360)}°` : "";
  return `${who} · ${stateLabel[m.state] ?? m.state} · ${originLabel[m.origin] ?? m.origin}${head}`;
}
