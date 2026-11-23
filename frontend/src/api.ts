/** 后端 API 客户端：Session + CSRF，同源。所有权限以服务端为准。 */
export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: Record<string, string>;
  constructor(status: number, code: string, message: string, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

function cookie(name: string): string {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}

// ---- 连接状态（侧栏显示“服务器连接正常/已断开/维护中”） ----
type ConnState = { online: boolean; maintenance: boolean; checkedAt: number };
let conn: ConnState = { online: true, maintenance: false, checkedAt: 0 };
const listeners = new Set<(c: ConnState) => void>();
function emit() {
  listeners.forEach((l) => l(conn));
}
export function setOnline(v: boolean) {
  if (conn.online !== v) {
    conn = { ...conn, online: v, checkedAt: Date.now() };
    emit();
  }
}
export function setMaintenance(v: boolean) {
  if (conn.maintenance !== v) {
    conn = { ...conn, maintenance: v };
    emit();
  }
}
export function onConn(l: (c: ConnState) => void) {
  listeners.add(l);
  l(conn);
  return () => listeners.delete(l);
}
export async function pingServer() {
  try {
    const r = await fetch("/healthz", { cache: "no-store" });
    const d = await r.json();
    setOnline(r.ok);
    setMaintenance(!!d.maintenance);
  } catch {
    setOnline(false);
  }
}

export async function ensureCsrf() {
  if (!cookie("csrftoken")) await fetch("/api/auth/csrf/", { credentials: "same-origin" });
}

type Opts = { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string> };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  if (method !== "GET") {
    await ensureCsrf();
    headers["X-CSRFToken"] = cookie("csrftoken");
  }
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch("/api" + path, { method, headers, body, credentials: "same-origin" });
  } catch {
    setOnline(false);
    throw new ApiError(0, "offline", "无法连接服务器：请检查局域网连接，或联系管理员确认系统服务已启动。");
  }
  setOnline(true);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 403 && data && typeof data === "object" && data.code === "not_authenticated" && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event("ish:unauthorized"));
    }
    if (res.status === 503 && data && data.code === "maintenance") setMaintenance(true);
    if (data && typeof data === "object") throw new ApiError(res.status, data.code || "error", data.message || "请求失败", data.field_errors || {});
    throw new ApiError(res.status, "error", `请求失败（${res.status}）`);
  }
  return data as T;
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, body: unknown = {}) => api<T>(p, { method: "POST", body });
export const patch = <T = any>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

export function qs(params: Record<string, string | number | undefined | null | boolean>) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, String(v));
  });
  const s = u.toString();
  return s ? "?" + s : "";
}

export function money(v: string | number | null | undefined) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return "¥ " + n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function dt(v?: string | null, withTime = false) {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  const p = (n: number) => String(n).padStart(2, "0");
  const s = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withTime ? `${s} ${p(d.getHours())}:${p(d.getMinutes())}` : s;
}

/** 系统业务时区（与后端 TIME_ZONE 一致）。表单默认“今天”一律按此时区取日期，
 * 不能用 toISOString()（UTC）截取——北京时间 00:00–07:59 会得到前一天（5753b5f 审查 R76-P1-02）。 */
export const BUSINESS_TZ = "Asia/Shanghai";

export function todayBusinessDate(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: BUSINESS_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
