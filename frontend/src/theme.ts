/** Tema global da interface (decisão D): claro, escuro ou automático (segue o sistema).
 *
 * - Preferência persistida em localStorage "agf-theme" (leitura/escrita protegidas: modo privado ou bloqueio não quebram a tela).
 * - Aplicação: `document.documentElement.dataset.theme = "light" | "dark"`; automático = sem atributo (o CSS usa
 *   `@media (prefers-color-scheme)` em `:root:not([data-theme])`).
 * - Quem precisa do tema EFETIVO (ex.: MapView, para trocar o estilo do mapa) usa `useTheme().effective`,
 *   `getEffectiveTheme()` ou `subscribeTheme(cb)`; toda mudança (escolha do usuário ou do sistema no modo automático)
 *   também dispara o evento `window` "agf-theme" com `detail = { pref, effective }`.
 */
import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "auto";
export type EffectiveTheme = "light" | "dark";
export const THEME_KEY = "agf-theme";
export const THEME_EVENT = "agf-theme";
export const THEME_LABEL: Record<ThemePref, string> = { light: "Claro", dark: "Escuro", auto: "Automático" };
export interface ThemeEventDetail { pref: ThemePref; effective: EffectiveTheme }

const isPref = (v: unknown): v is ThemePref => v === "light" || v === "dark" || v === "auto";

export function readThemePref(): ThemePref {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return isPref(v) ? v : "auto";
  } catch { return "auto"; }
}

function saveThemePref(p: ThemePref) {
  try { window.localStorage.setItem(THEME_KEY, p); } catch { /* sem armazenamento: vale só nesta sessão */ }
}

const mql = (): MediaQueryList | null => {
  try { return typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null; } catch { return null; }
};

/** Tema do sistema operacional; sem suporte a media query, assume escuro (o padrão da referência). */
export function systemTheme(): EffectiveTheme {
  const m = mql();
  return m ? (m.matches ? "dark" : "light") : "dark";
}

export const resolveTheme = (pref: ThemePref, sys: EffectiveTheme = systemTheme()): EffectiveTheme => (pref === "auto" ? sys : pref);
export const getEffectiveTheme = (): EffectiveTheme => resolveTheme(readThemePref());

function emit(pref: ThemePref) {
  const detail: ThemeEventDetail = { pref, effective: resolveTheme(pref) };
  try { window.dispatchEvent(new CustomEvent<ThemeEventDetail>(THEME_EVENT, { detail })); } catch { /* ambiente sem CustomEvent */ }
  return detail.effective;
}

/** Aplica a preferência no <html> (sem persistir). Devolve o tema efetivo. */
export function applyTheme(pref: ThemePref): EffectiveTheme {
  const el = document.documentElement;
  if (pref === "auto") delete el.dataset.theme;
  else el.dataset.theme = pref;
  return emit(pref);
}

/** Escolha do usuário: persiste e aplica. */
export function setThemePref(pref: ThemePref): EffectiveTheme {
  saveThemePref(pref);
  return applyTheme(pref);
}

/** Avisa `cb` a cada mudança do tema efetivo (escolha do usuário ou troca do sistema no modo automático). Devolve o cancelamento. */
export function subscribeTheme(cb: (effective: EffectiveTheme, pref: ThemePref) => void): () => void {
  const onEvent = (e: Event) => { const d = (e as CustomEvent<ThemeEventDetail>).detail; if (d) cb(d.effective, d.pref); };
  hookSystem();
  window.addEventListener(THEME_EVENT, onEvent);
  return () => window.removeEventListener(THEME_EVENT, onEvent);
}

let systemHooked = false;
/** Um único ouvinte da media query do sistema: no modo automático, a troca do sistema vira um evento "agf-theme". */
function hookSystem() {
  if (systemHooked) return;
  const m = mql();
  if (!m?.addEventListener) return;
  systemHooked = true;
  m.addEventListener("change", () => { if (readThemePref() === "auto") emit("auto"); });
}

/** Hook: preferência, tema efetivo e o setter (persistente). */
export function useTheme(): { pref: ThemePref; effective: EffectiveTheme; setPref: (p: ThemePref) => void } {
  const [pref, setP] = useState<ThemePref>(readThemePref);
  const [effective, setEff] = useState<EffectiveTheme>(() => resolveTheme(readThemePref()));
  useEffect(() => subscribeTheme((eff, p) => { setEff(eff); setP(p); }), []);
  return { pref, effective, setPref: (p) => { setP(p); setEff(setThemePref(p)); } };
}
