import { describe, expect, it } from "vitest";
import {
  ALL_VISIBLE, DEFAULT_MAP_STYLE, DEFAULT_MAP_STYLE_DARK, DEFAULT_SATELLITE_LABEL, DEFAULT_SATELLITE_TILES, OSM_ATTRIBUTION, OVERLAY_SOURCES, TRACTOR_SVG,
  attributionCompact, baseStyle, boundsKey, composeStyle, computeBounds, effectiveTheme, fieldBounds, groupLayerIds, initialBasemap, isOsmTileServer, isStyleJson,
  machineTitle, metersPerPixel, niceScale, overlayData, overlayLayers, readMapEnv, resolveBasemap, shouldRefit, styleUrlFor, toFeatureCollection, type MapData,
} from "./mapConfig";

describe("readMapEnv", () => {
  it("padrões: OpenFreeMap (liberty/dark) e EOX 2016", () => {
    const c = readMapEnv({});
    expect(c.mapStyleUrl).toBe(DEFAULT_MAP_STYLE);
    expect(c.mapStyleUrlDark).toBe(DEFAULT_MAP_STYLE_DARK);
    expect(c.legacyTiles).toBeNull();
    expect(c.satellite).toMatchObject({ tiles: DEFAULT_SATELLITE_TILES, maxzoom: 14, tileSize: 256, label: DEFAULT_SATELLITE_LABEL });
    expect(c.satellite!.attribution).toContain("Copernicus Sentinel data 2016");
    expect(c.satellite!.attribution).toContain('href="https://cloudless.eox.at"');
    expect(c.satellite!.attribution).toContain("CC BY 4.0");
    expect(c.defaultBasemap).toBe("map");
  });
  it("variável vazia desliga; estilo próprio não adivinha a variante escura", () => {
    const c = readMapEnv({ VITE_MAP_STYLE_URL: "", VITE_SATELLITE_TILES: "" });
    expect(c.mapStyleUrl).toBeNull(); expect(c.satellite).toBeNull(); expect(c.defaultBasemap).toBe("none");
    const d = readMapEnv({ VITE_MAP_STYLE_URL: "https://x.example/style.json" });
    expect(d.mapStyleUrlDark).toBe("https://x.example/style.json");
    const e = readMapEnv({ VITE_MAP_STYLE_URL: "https://x.example/a.json", VITE_MAP_STYLE_URL_DARK: "https://x.example/b.json" });
    expect(styleUrlFor(e, "light")).toBe("https://x.example/a.json"); expect(styleUrlFor(e, "dark")).toBe("https://x.example/b.json");
    expect(readMapEnv({ VITE_MAP_STYLE_URL_DARK: "" }).mapStyleUrlDark).toBe(DEFAULT_MAP_STYLE);
  });
  it("compatibilidade: VITE_BASEMAP_TILES vira o 'Mapa' raster quando VITE_MAP_STYLE_URL não foi definida", () => {
    const c = readMapEnv({ VITE_BASEMAP_TILES: "https://tile.openstreetmap.org/{z}/{x}/{y}.png" });
    expect(c.mapStyleUrl).toBeNull();
    expect(c.legacyTiles).toContain("tile.openstreetmap.org");
    expect(c.legacyAttribution).toBe(OSM_ATTRIBUTION);
    expect(readMapEnv({ VITE_BASEMAP_TILES: "https://t/{z}/{x}/{y}.png", VITE_BASEMAP_ATTRIBUTION: "© Meu" }).legacyAttribution).toBe("© Meu");
    // estilo vetorial explícito vence o raster legado
    expect(readMapEnv({ VITE_BASEMAP_TILES: "https://t/{z}/{x}/{y}.png", VITE_MAP_STYLE_URL: "https://s" }).mapStyleUrl).toBe("https://s");
  });
  it("satélite personalizado: números validados, sem rótulo/atribuição inventados", () => {
    const c = readMapEnv({ VITE_SATELLITE_TILES: "https://tiles.versatiles.org/tiles/satellite/{z}/{x}/{y}", VITE_SATELLITE_MAXZOOM: "12", VITE_SATELLITE_TILESIZE: "512" });
    expect(c.satellite).toMatchObject({ maxzoom: 12, tileSize: 512, label: "", attribution: "" });
    expect(readMapEnv({ VITE_SATELLITE_MAXZOOM: "abc", VITE_SATELLITE_TILESIZE: "-1" }).satellite).toMatchObject({ maxzoom: 14, tileSize: 256 });
    expect(readMapEnv({ VITE_SATELLITE_LABEL: "Outro" }).satellite!.label).toBe("Outro");
  });
  it("VITE_BASEMAP_DEFAULT respeita o que está configurado", () => {
    expect(readMapEnv({ VITE_BASEMAP_DEFAULT: "satellite" }).defaultBasemap).toBe("satellite");
    expect(readMapEnv({ VITE_BASEMAP_DEFAULT: "none" }).defaultBasemap).toBe("none");
    expect(readMapEnv({ VITE_BASEMAP_DEFAULT: "xpto" }).defaultBasemap).toBe("map");
    expect(readMapEnv({ VITE_BASEMAP_DEFAULT: "satellite", VITE_SATELLITE_TILES: "" }).defaultBasemap).toBe("map");
  });
});

describe("escolha do mapa-base", () => {
  const cfg = readMapEnv({});
  it("prop > localStorage > padrão; inválido ou indisponível cai para o disponível", () => {
    expect(initialBasemap(undefined, null, cfg)).toBe("map");
    expect(initialBasemap(undefined, "satellite", cfg)).toBe("satellite");
    expect(initialBasemap("none", "satellite", cfg)).toBe("none");
    expect(initialBasemap(undefined, "lixo", cfg)).toBe("map");
    const onlySat = readMapEnv({ VITE_MAP_STYLE_URL: "" });
    expect(resolveBasemap("map", onlySat)).toBe("satellite");
    const nothing = readMapEnv({ VITE_MAP_STYLE_URL: "", VITE_SATELLITE_TILES: "" });
    expect(resolveBasemap("satellite", nothing)).toBe("none");
  });
  it("atribuição recolhível exceto com tiles do OSM", () => {
    const osm = readMapEnv({ VITE_BASEMAP_TILES: "https://tile.openstreetmap.org/{z}/{x}/{y}.png" });
    expect(isOsmTileServer("https://tile.openstreetmap.org/{z}/{x}/{y}.png")).toBe(true);
    expect(isOsmTileServer("https://tiles.openfreemap.org/styles/liberty")).toBe(false);
    expect(attributionCompact("map", osm)).toBe(false);
    expect(attributionCompact("satellite", osm)).toBe(true);
    expect(attributionCompact("map", cfg)).toBe(true);
  });
  it("tema efetivo", () => {
    expect(effectiveTheme("dark", false)).toBe("dark");
    expect(effectiveTheme("light", true)).toBe("light");
    expect(effectiveTheme(null, true)).toBe("dark");
    expect(effectiveTheme(undefined, false)).toBe("light");
  });
});

const FIELD: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[-47.60, -16.70], [-47.55, -16.70], [-47.55, -16.66], [-47.60, -16.66], [-47.60, -16.70]]] };
const MULTI: GeoJSON.MultiPolygon = { type: "MultiPolygon", coordinates: [FIELD.coordinates, [[[-47.50, -16.72], [-47.49, -16.72], [-47.49, -16.71], [-47.50, -16.72]]]] };
const WORKED: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[-47.59, -16.69], [-47.58, -16.69], [-47.58, -16.68], [-47.59, -16.69]]] };

describe("enquadramento (bug da cobertura parcial)", () => {
  it("inclui o talhão inteiro, não só a área coberta", () => {
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: FIELD }] };
    expect(computeBounds({ fields: fc, union: WORKED })).toEqual([[-47.60, -16.70], [-47.55, -16.66]]);
  });
  it("MultiPolygon, Geometry crua, trajetória, pendente e máquina", () => {
    const b = computeBounds({ fields: MULTI, worked: WORKED, pending: FIELD, tracks: { type: "MultiLineString", coordinates: [[[-47.61, -16.69], [-47.6, -16.69]]] },
      machine: { lon: -47.57, lat: -16.65, state: "working", origin: "simulated" } });
    expect(b).toEqual([[-47.61, -16.72], [-47.49, -16.65]]);
  });
  it("ignora coordenadas inválidas e devolve null sem nada", () => {
    expect(computeBounds({})).toBeNull();
    expect(computeBounds({ fields: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: null as any }] }, markers: [{ id: "a", lon: NaN, lat: 1, state: "x", origin: "y" }] })).toBeNull();
    expect(computeBounds({ markers: [{ id: "a", lon: -47, lat: -16, state: "x", origin: "y" }] })).toEqual([[-47, -16], [-47, -16]]);
    expect(computeBounds({ tracks: { type: "GeometryCollection", geometries: [WORKED] } })).toEqual([[-47.59, -16.69], [-47.58, -16.68]]);
  });
  it("reenquadra só quando fitKey muda, ou quando o talhão chega/muda depois (mesmo fitKey)", () => {
    const fk = boundsKey(fieldBounds({ fields: FIELD }));
    expect(shouldRefit(null, undefined, fk, true)).toBe(false);
    expect(shouldRefit(null, "a", "", false)).toBe(false);      // nada para enquadrar: espera os dados
    expect(shouldRefit(null, "a", "", true)).toBe(true);
    expect(shouldRefit({ key: "a", fieldKey: "" }, "a", "", true)).toBe(false);   // dados ao vivo mudaram: não briga com o usuário
    expect(shouldRefit({ key: "a", fieldKey: "" }, "a", fk, true)).toBe(true);    // o talhão chegou depois do 1º enquadramento
    expect(shouldRefit({ key: "a", fieldKey: fk }, "a", fk, true)).toBe(false);
    expect(shouldRefit({ key: "a", fieldKey: fk }, "b", fk, true)).toBe(true);
  });
});

describe("estilos", () => {
  const cfg = readMapEnv({});
  const vector = { version: 8, sources: { openmaptiles: { type: "vector", url: "https://x" } }, layers: [{ id: "background", type: "background" }, { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water" }] } as any;
  it("valida JSON de estilo", () => {
    expect(isStyleJson(vector)).toBe(true);
    expect(isStyleJson({ version: 7, layers: [], sources: {} })).toBe(false);
    expect(isStyleJson("<html>")).toBe(false);
  });
  it("satélite: raster com maxzoom 14 (overzoom), atribuição e fundo do tema", () => {
    const s = baseStyle("satellite", cfg, "dark");
    expect(s.sources.sat).toMatchObject({ type: "raster", tileSize: 256, maxzoom: 14, tiles: [DEFAULT_SATELLITE_TILES] });
    expect((s.sources.sat as any).attribution).toContain("EOX");
    expect(s.layers.map((l) => l.id)).toEqual(["bg", "sat"]);
    expect((s.layers[0] as any).paint["background-color"]).toBe("#0E1A17");
  });
  it("offline e raster legado", () => {
    expect(baseStyle("none", cfg, "light").layers.map((l) => l.id)).toEqual(["bg"]);
    const legacy = readMapEnv({ VITE_BASEMAP_TILES: "https://t/{z}/{x}/{y}.png", VITE_BASEMAP_ATTRIBUTION: "© T" });
    expect(baseStyle("map", legacy, "light").sources.base).toMatchObject({ type: "raster", tiles: ["https://t/{z}/{x}/{y}.png"], attribution: "© T" });
    expect(baseStyle("map", cfg, "light", vector).layers).toHaveLength(2);
    expect(baseStyle("map", cfg, "light", null).layers.map((l) => l.id)).toEqual(["bg"]);   // estilo não carregou → fundo neutro
  });
  it("compõe: camadas próprias por cima, com dados, preservadas em qualquer mapa-base", () => {
    const data: MapData = { fields: FIELD, worked: WORKED };
    const layers = overlayLayers("operation", "dark", ALL_VISIBLE);
    for (const base of [baseStyle("map", cfg, "dark", vector), baseStyle("satellite", cfg, "dark"), baseStyle("none", cfg, "light")]) {
      const s = composeStyle(base, layers, overlayData(data));
      for (const src of OVERLAY_SOURCES) expect(s.sources[src]).toMatchObject({ type: "geojson" });
      expect(s.layers.slice(-layers.length).map((l) => l.id)).toEqual(layers.map((l) => l.id));
      expect(((s.sources.worked as any).data as GeoJSON.FeatureCollection).features[0].geometry).toEqual(WORKED);
    }
    expect(composeStyle(vector, layers, {}).layers.slice(0, 2).map((l) => l.id)).toEqual(["background", "water"]);
    expect(vector.layers).toHaveLength(2);   // não altera o estilo baixado (cache)
  });
  it("variante operação: contorno neon com brilho, trabalhada ~40 %, grupos de camadas", () => {
    const layers = overlayLayers("operation", "dark", { ...ALL_VISIBLE, pending: false });
    const ids = layers.map((l) => l.id);
    expect(ids.indexOf("fields-glow")).toBeLessThan(ids.indexOf("fields-line"));
    const line = layers.find((l) => l.id === "fields-line") as any;
    expect(line.paint).toMatchObject({ "line-color": "#4ADE3B", "line-width": 3 });
    expect((layers.find((l) => l.id === "fields-glow") as any).paint["line-blur"]).toBeGreaterThan(0);
    expect((layers.find((l) => l.id === "worked-fill") as any).paint["fill-opacity"]).toBe(0.4);
    expect((layers.find((l) => l.id === "pending-fill") as any).layout.visibility).toBe("none");
    expect(groupLayerIds(layers, "outside")).toEqual(["outside-fill", "outside-line"]);
    expect(groupLayerIds(layers, "track")).toEqual(["tracks-line"]);
  });
  it("variante padrão mantém trajetória por estado", () => {
    const l = overlayLayers("default", "light", ALL_VISIBLE, { working: "#2e7d32" }).find((x) => x.id === "tracks-line") as any;
    expect(JSON.stringify(l.paint["line-color"])).toContain("#2e7d32");
  });
});

describe("utilitários", () => {
  it("toFeatureCollection aceita Geometry, Feature e FeatureCollection", () => {
    expect(toFeatureCollection(null).features).toHaveLength(0);
    expect(toFeatureCollection(FIELD).features[0].geometry).toEqual(FIELD);
    expect(toFeatureCollection({ type: "Feature", properties: {}, geometry: FIELD }).features).toHaveLength(1);
    expect(toFeatureCollection({ type: "Feature", properties: {}, geometry: null as any }).features).toHaveLength(0);
  });
  it("escala gráfica redonda", () => {
    expect(metersPerPixel(0, 0)).toBeCloseTo(78271.5, 0);
    expect(niceScale(500 / 100, 110)).toEqual({ px: 100, labels: ["0", "250", "500 m"] });
    expect(niceScale(10, 110)).toEqual({ px: 100, labels: ["0", "0,5", "1 km"] });
    expect(niceScale(0.3, 110)!.labels[2]).toBe("25 m");
    expect(niceScale(0)).toBeNull();
  });
  it("marcador: título com máquina, estado, origem e rumo; ícone estático", () => {
    expect(machineTitle({ lon: 0, lat: 0, state: "working", origin: "simulated", label: "SIM-TR-320", heading_deg: 415 }, { working: "aplicando" }, { simulated: "SIMULADO" }))
      .toBe("SIM-TR-320 · aplicando · SIMULADO · rumo 55°");
    expect(machineTitle({ lon: 0, lat: 0, state: "x", origin: "real" })).toBe("Máquina · x · real");
    expect(TRACTOR_SVG.startsWith("<svg")).toBe(true);
  });
});
