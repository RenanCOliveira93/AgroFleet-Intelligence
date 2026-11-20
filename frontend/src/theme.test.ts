import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, getEffectiveTheme, readThemePref, resolveTheme, setThemePref, subscribeTheme, THEME_EVENT, THEME_KEY } from "./theme";

// armazenamento em memória (o localStorage do jsdom/Node pode não existir sem URL de origem)
class MemStorage { m = new Map<string, string>(); getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; } setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); } clear() { this.m.clear(); } }
let store = new MemStorage();
Object.defineProperty(window, "localStorage", { configurable: true, get: () => store });
const localStorage = { clear: () => store.clear(), getItem: (k: string) => store.getItem(k), setItem: (k: string, v: string) => store.setItem(k, v) };

function mockSystem(dark: boolean) {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({ matches: dark, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as any;
}

describe("tema", () => {
  beforeEach(() => { store = new MemStorage(); delete document.documentElement.dataset.theme; mockSystem(true); });
  afterEach(() => vi.restoreAllMocks());

  it("sem preferência salva é automático e segue o sistema", () => {
    expect(readThemePref()).toBe("auto");
    expect(getEffectiveTheme()).toBe("dark");
    mockSystem(false);
    expect(getEffectiveTheme()).toBe("light");
  });

  it("valor inválido no armazenamento vira automático", () => {
    localStorage.setItem(THEME_KEY, "roxo");
    expect(readThemePref()).toBe("auto");
  });

  it("persiste a escolha e aplica no <html>; automático remove o atributo", () => {
    expect(setThemePref("light")).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(readThemePref()).toBe("light");
    expect(setThemePref("auto")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem(THEME_KEY)).toBe("auto");
  });

  it("resolve o tema efetivo", () => {
    expect(resolveTheme("auto", "light")).toBe("light");
    expect(resolveTheme("auto", "dark")).toBe("dark");
    expect(resolveTheme("dark", "light")).toBe("dark");
    expect(resolveTheme("light", "dark")).toBe("light");
  });

  it("emite o evento com o tema efetivo e avisa os assinantes", () => {
    const seen: string[] = [];
    const ev = vi.fn();
    window.addEventListener(THEME_EVENT, ev);
    const off = subscribeTheme((eff, pref) => seen.push(`${pref}:${eff}`));
    applyTheme("light");
    mockSystem(false);
    applyTheme("auto");
    off();
    applyTheme("dark");
    window.removeEventListener(THEME_EVENT, ev);
    expect(seen).toEqual(["light:light", "auto:light"]);
    expect(ev).toHaveBeenCalledTimes(3);
  });

  it("armazenamento bloqueado não quebra (modo privado)", () => {
    vi.spyOn(store, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(store, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(readThemePref()).toBe("auto");
    expect(setThemePref("dark")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
