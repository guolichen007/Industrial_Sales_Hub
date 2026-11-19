import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { ApiError } from "./api";

type Toast = { id: number; text: string; error?: boolean };
const ToastCtx = createContext<{ notify: (t: string) => void; fail: (e: unknown) => void }>(null as any);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, error }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), error ? 5200 : 3000);
  }, []);
  const notify = useCallback((t: string) => push(t), [push]);
  const fail = useCallback((e: unknown) => push(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e), true), [push]);
  return (
    <ToastCtx.Provider value={{ notify, fail }}>
      {children}
      <div style={{ position: "fixed", bottom: 26, right: 26, zIndex: 200, display: "flex", flexDirection: "column", gap: 8 }}>
        {items.map((t) => (
          <div key={t.id} className={"toast" + (t.error ? " error" : "")} style={{ position: "static" }} role="status">
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** 加载数据：返回 [data, reload, loading, error] */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | undefined, () => void, boolean, string] {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => alive && (setData(d), setErr("")))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [data, () => setTick((t) => t + 1), loading, err];
}

export function PageHead({ k, title, sub, children }: { k: string; title: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="pagehead">
      <div>
        <div className="eyebrow">{k}</div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div className="actions">{children}</div>
    </div>
  );
}

const CHIP: Record<string, string> = {
  投标中: "amber", 报价中: "amber", 结果待定: "amber", 技术交流: "gray", 线索发现: "gray", 初步沟通: "gray", 失标: "red", 成交: "",
  执行中: "blue", 未启动: "gray", 暂停: "amber", 已验收: "", 已关闭: "gray",
  草稿: "gray", 已生成: "blue", 已发送: "blue", 已接受: "", 已失标: "red", 已取消: "gray",
  待核实: "amber", 已确认: "", 已撤销: "red", 待确认: "amber",
  未开始: "gray", 进行中: "blue", 已完成: "", 延期: "red", 不适用: "gray",
};
export function Chip({ s, cls }: { s?: string | null; cls?: string }) {
  if (!s) return null;
  return <span className={"chip " + (cls ?? CHIP[s] ?? "")}>{s}</span>;
}

export function Modal({ title, onClose, children, foot, wide }: { title: string; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modalback" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { width: "min(1000px,100%)" } : undefined} role="dialog" aria-label={title}>
        <div className="modalhead">
          <h2>{title}</h2>
          <button className="close" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="modalbody">{children}</div>
        {foot && <div className="modalfoot">{foot}</div>}
      </div>
    </div>
  );
}

export function Field({ label, children, full, err, hint }: { label: string; children: ReactNode; full?: boolean; err?: string; hint?: string }) {
  return (
    <div className={"formfield" + (full ? " full" : "")}>
      <label>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
      {err && <span className="fielderr">{err}</span>}
    </div>
  );
}

export function Loading({ err }: { err?: string }) {
  return <div className="loading">{err ? <span className="err">{err}</span> : "加载中…"}</div>;
}

export function Empty({ text = "暂无数据" }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

/** 通用表单状态 */
export function useForm<T extends Record<string, any>>(init: T) {
  const [v, setV] = useState<T>(init);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const bind = (k: keyof T) => ({
    value: (v[k] ?? "") as any,
    onChange: (e: { target: { value: string } }) => setV((x) => ({ ...x, [k]: e.target.value })),
  });
  const catchErr = (e: unknown) => {
    if (e instanceof ApiError) setErrs(e.fieldErrors);
  };
  return { v, setV, bind, errs, setErrs, catchErr };
}
