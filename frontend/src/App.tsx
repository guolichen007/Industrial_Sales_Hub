import { useState, type FormEvent } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { ApiError } from "./api";
import { Loading } from "./ui";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import Customers from "./pages/Customers";
import Products from "./pages/Products";
import Quotes from "./pages/Quotes";
import QuoteEditor from "./pages/QuoteEditor";
import Execution from "./pages/Execution";
import Performance from "./pages/Performance";
import Cases from "./pages/Cases";
import Settings from "./pages/Settings";

type NavItem = { to: string; label: string; glyph: string; cap?: string };
const NAV: NavItem[] = [
  { to: "/", label: "经营工作台", glyph: "◈" },
  { to: "/projects", label: "项目中心", glyph: "▦" },
  { to: "/customers", label: "客户资料库", glyph: "♧" },
  { to: "/products", label: "产品型号库", glyph: "▤", cap: "product.write" },
  { to: "/quotes", label: "报价中心", glyph: "▣", cap: "quote.access" },
  { to: "/execution", label: "项目执行", glyph: "◷" },
  { to: "/performance", label: "业绩台账", glyph: "▥" },
  { to: "/cases", label: "对外案例", glyph: "▧", cap: "case.read" },
  { to: "/settings", label: "系统设置", glyph: "⚙" },
];

function Login() {
  const { login } = useAuth();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await login(u, p);
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "无法连接服务器");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <form className="loginbox" onSubmit={submit}>
        <div className="brand-logo">工</div>
        <h1>工业销售管理系统</h1>
        <div className="sub">项目 · 报价 · 执行 · 业绩 · 案例</div>
        <div className="formfield">
          <label htmlFor="u">用户名</label>
          <input id="u" className="input" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus />
        </div>
        <div className="formfield">
          <label htmlFor="p">密码</label>
          <input id="p" className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn primary" disabled={busy || !u || !p}>{busy ? "登录中…" : "登录"}</button>
      </form>
    </div>
  );
}

function Guard({ cap, children }: { cap?: string; children: JSX.Element }) {
  const { can } = useAuth();
  if (cap && !can(cap)) return <div className="container"><div className="warnnote">当前角色无权访问该模块。</div></div>;
  return children;
}

export default function App() {
  const { me, loading, logout, can } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading />;
  if (!me) return <Login />;
  const current = NAV.find((n) => (n.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.to)));
  return (
    <>
      <aside>
        <div className="brand">
          <div className="brand-logo">工</div>
          <div>
            <strong>工业销售管理</strong>
            <small>SALES HUB V2</small>
          </div>
        </div>
        <div className="aside-title">业务导航</div>
        <nav className="nav">
          {NAV.filter((n) => !n.cap || can(n.cap)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => "navitem" + (isActive ? " active" : "")}>
              <span className="glyph">{n.glyph}</span>
              <span className="text">{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div><span className="statusdot" />云端数据 · 实时共享</div>
          <div>{me.name} · {me.role_label}</div>
          <button onClick={logout}>退出登录</button>
        </div>
      </aside>
      <main>
        <div className="topbar">
          <div>
            <div className="crumb">工业销售管理系统 / {current?.label ?? "详情"}</div>
            <div className="top-name">{current?.label ?? "项目工作台"}</div>
          </div>
          <div className="top-right">
            <div className="rolebox">当前身份 <b style={{ color: "#19344a" }}>{me.role_label}</b></div>
            <div className="user-icon" title={me.name}>{me.name.slice(0, 1)}</div>
          </div>
        </div>
        <div className="container">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:id" element={<ProjectDetail />} />
            <Route path="/customers" element={<Customers />} />
            <Route path="/products" element={<Guard cap="product.write"><Products /></Guard>} />
            <Route path="/quotes" element={<Guard cap="quote.access"><Quotes /></Guard>} />
            <Route path="/quotes/:id" element={<Guard cap="quote.access"><QuoteEditor /></Guard>} />
            <Route path="/execution" element={<Execution />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/cases" element={<Guard cap="case.read"><Cases /></Guard>} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </div>
      </main>
    </>
  );
}
