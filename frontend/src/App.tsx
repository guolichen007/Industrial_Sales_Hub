import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { ApiError, get, onConn, pingServer } from "./api";
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
import Inquiries from "./pages/Inquiries";

type NavItem = { to: string; label: string; glyph: string; cap?: string };
// RC3-G0：首层只有两个业务工作区（商务 / 销售）；同一页面按角色能力出现在对应工作区，技术设置放在页尾
const BUSINESS_NAV: NavItem[] = [
  { to: "/", label: "工作台", glyph: "◈" },
  { to: "/inquiries", label: "询价登记", glyph: "✉", cap: "inquiry.read" },
  { to: "/quotes", label: "报价中心", glyph: "▣", cap: "quote.access" },
  { to: "/projects", label: "项目台账", glyph: "▦", cap: "project.edit_any" },
  { to: "/execution", label: "交付与回款进度", glyph: "◷", cap: "delivery.write" },
  { to: "/performance", label: "业绩台账", glyph: "▥", cap: "performance.write" },
  { to: "/customers", label: "客户资料库", glyph: "♧", cap: "project.edit_any" },
  { to: "/products", label: "产品型号库", glyph: "▤", cap: "product.write" },
  { to: "/cases", label: "对外案例", glyph: "▧", cap: "case.withdraw" },
];
const SALES_NAV: NavItem[] = [
  { to: "/", label: "工作台", glyph: "◈" },
  { to: "/projects", label: "售前项目", glyph: "▦", cap: "project.read" },
  { to: "/execution", label: "项目进度查询", glyph: "◷", cap: "project.read" },
  { to: "/customers", label: "客户资料库", glyph: "♧", cap: "project.read" },
  { to: "/performance", label: "我的业绩", glyph: "▥", cap: "performance.read" },
  { to: "/cases", label: "对外案例", glyph: "▧", cap: "case.read" },
];
const TECH_NAV: NavItem[] = [{ to: "/settings", label: "系统设置", glyph: "⚙" }];
const NAV: NavItem[] = [...BUSINESS_NAV, ...SALES_NAV, ...TECH_NAV];

function ConnBadge() {
  const [c, setC] = useState({ online: true, maintenance: false, checkedAt: 0 });
  useEffect(() => {
    const off = onConn(setC);
    pingServer();
    const t = setInterval(pingServer, 30000);
    return () => {
      off();
      clearInterval(t);
    };
  }, []);
  if (!c.online) return <div style={{ color: "#ff9b8f" }}><span className="statusdot" style={{ background: "#ef6b5f" }} />服务器连接已断开</div>;
  if (c.maintenance) return <div style={{ color: "#f5c66b" }}><span className="statusdot" style={{ background: "#f5c66b" }} />系统维护/备份中（只读）</div>;
  return <div><span className="statusdot" />局域网 · 共享数据 · 连接正常</div>;
}

function Login() {
  const { login } = useAuth();
  const [info, setInfo] = useState<{ mode: string; https: boolean; initialized: boolean; version: string; maintenance: boolean } | null>(null);
  useEffect(() => {
    get("/public-info/").then(setInfo).catch(() => setInfo(null));
  }, []);
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
        {info && !info.initialized && <div className="warnnote" style={{ marginTop: 14 }}>系统尚未初始化管理员。请在服务器上运行“01-安装部署”完成初始化。</div>}
        {info && info.mode === "LAN" && !info.https && <div className="hint" style={{ marginTop: 12 }}>提示：当前为局域网 HTTP 试用连接，传输未加密，请勿在公共网络中使用。</div>}
        {info && info.mode === "DEMO" && <div className="hint" style={{ marginTop: 12 }}>演示模式：仅本机可访问，数据为演示数据。</div>}
        {info && info.maintenance && <div className="hint" style={{ marginTop: 12 }}>系统正在备份/维护，暂时只读。</div>}
        {info && <div className="hint" style={{ marginTop: 6 }}>版本 {info.version}</div>}
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
        {(() => {
          // 商务工作区：负责人/商务/财务（有询价或项目全量编辑能力）；销售工作区：销售；管理员只有技术设置
          const board = me.role === "SALES" ? { title: "销售工作区", items: SALES_NAV } : me.role === "ADMIN" ? { title: "技术管理", items: [] as NavItem[] } : { title: "商务工作区", items: BUSINESS_NAV };
          const items = board.items.filter((n) => !n.cap || can(n.cap));
          const link = (n: NavItem) => (
            <NavLink key={n.to + n.label} to={n.to} end={n.to === "/"} className={({ isActive }) => "navitem" + (isActive ? " active" : "")}>
              <span className="glyph">{n.glyph}</span>
              <span className="text">{n.label}</span>
            </NavLink>
          );
          return (
            <>
              <div className="aside-title">{board.title}</div>
              <nav className="nav">
                {items.map(link)}
                {me.role === "ADMIN" && link({ to: "/", label: "工作台", glyph: "◈" })}
              </nav>
              <div className="aside-title">技术设置</div>
              <nav className="nav">{TECH_NAV.map(link)}</nav>
            </>
          );
        })()}
        <div className="sidebar-bottom">
          <ConnBadge />
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
            <Route path="/projects" element={<Guard cap="project.read"><Projects /></Guard>} />
            <Route path="/projects/:id" element={<Guard cap="project.read"><ProjectDetail /></Guard>} />
            <Route path="/customers" element={<Guard cap="project.read"><Customers /></Guard>} />
            <Route path="/products" element={<Guard cap="product.write"><Products /></Guard>} />
            <Route path="/inquiries" element={<Guard cap="inquiry.read"><Inquiries /></Guard>} />
            <Route path="/quotes" element={<Guard cap="quote.access"><Quotes /></Guard>} />
            <Route path="/quotes/:id" element={<Guard cap="quote.access"><QuoteEditor /></Guard>} />
            <Route path="/execution" element={<Guard cap="project.read"><Execution /></Guard>} />
            <Route path="/performance" element={<Guard cap="performance.read"><Performance /></Guard>} />
            <Route path="/cases" element={<Guard cap="case.read"><Cases /></Guard>} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="warnnote">页面不存在。<a href="/">返回工作台</a></div>} />
          </Routes>
        </div>
      </main>
    </>
  );
}
