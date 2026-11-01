export type Role = "viewer" | "analyst" | "admin";
let token: string | null = null;
export const setToken = (t: string | null) => { token = t; };
export const getToken = () => token;

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) { super(typeof detail === "string" ? detail : JSON.stringify(detail)); }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData; params?: Record<string, string | number | boolean | undefined | null> } = {}): Promise<T> {
  const qs = opts.params ? "?" + new URLSearchParams(Object.entries(opts.params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString() : "";
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  const r = await fetch(`/api${path}${qs}`, { method: opts.method ?? "GET", headers, body });
  const ct = r.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(r.status, (data as any)?.detail ?? data);
  return data as T;
}

export async function download(path: string, params: Record<string, string>, filename: string) {
  const qs = new URLSearchParams(params).toString();
  const r = await fetch(`/api${path}?${qs}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url);
}

export function openWs(onMsg: (m: any) => void, onState: (s: "connecting" | "open" | "closed") => void): () => void {
  let ws: WebSocket | null = null, stop = false, retry = 1000;
  const connect = () => {
    onState("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws`);
    ws.onopen = () => ws!.send(JSON.stringify({ type: "auth", token }));
    ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === "hello") { onState("open"); retry = 1000; } onMsg(m); };
    ws.onclose = () => { onState("closed"); if (!stop) setTimeout(connect, retry = Math.min(retry * 2, 15000)); };
  };
  connect();
  return () => { stop = true; ws?.close(); };
}
