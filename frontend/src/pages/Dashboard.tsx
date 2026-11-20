import { Link, useNavigate } from "react-router-dom";
import { dt, get } from "../api";
import { useAuth } from "../auth";
import { msLabel } from "../options";
import { Chip, Loading, PageHead, useLoad } from "../ui";

function Kpis({ items }: { items: [string, any, string][] }) {
  return (
    <div className="grid cols4" style={{ marginBottom: 20 }}>
      {items.map(([l, v, dlt]) => (
        <div className="card kpi" key={l}>
          <div className="orb" />
          <div className="label">{l}</div>
          <div className="value">{v ?? 0}</div>
          <div className="delta">{dlt}</div>
        </div>
      ))}
    </div>
  );
}

function ProjectTable({ projects, title, empty }: { projects: any[] | undefined; title: string; empty: string }) {
  return (
    <div className="card cardpad">
      <div className="sectionhead">
        <h2>{title}</h2>
        <Link className="btn mini" to="/projects">进入项目中心 →</Link>
      </div>
      <div className="tablewrap">
        <table className="data">
          <thead><tr><th>项目 / 编号</th><th>售前阶段</th><th>执行阶段</th><th>执行进度</th><th>销售</th></tr></thead>
          <tbody>
            {(projects || []).slice(0, 8).map((p) => (
              <tr key={p.id}>
                <td><Link className="link" to={`/projects/${p.id}`}>{p.name}</Link><div className="micro">{p.code}</div></td>
                <td><Chip s={p.presale_phase} /></td>
                <td><Chip s={p.execution_phase} /></td>
                <td className="micro">{p.milestone_total ? `${p.milestone_done}/${p.milestone_total}` : "—"}</td>
                <td>{p.sales_owner_name || "—"}</td>
              </tr>
            ))}
            {projects && projects.length === 0 && <tr><td colSpan={5} className="empty-cell">{empty}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Timeline({ d }: { d: any }) {
  return (
    <div className="card cardpad">
      <div className="sectionhead"><h2>最近项目动态</h2><small>每 30 秒自动刷新</small></div>
      <div className="timeline">
        {d.recent_updates.map((u: any) => (
          <div className="timeitem" key={u.id}>
            <div className="time">{dt(u.at, true)} · {u.by} · {u.stream === "PRESALE" ? "售前" : "执行"}</div>
            <b>{u.title}</b>
            <Link className="sub" to={`/projects/${u.project_id}`}>{u.project_name}</Link>
          </div>
        ))}
        {d.recent_updates.length === 0 && <div className="sub">暂无动态</div>}
      </div>
    </div>
  );
}

function Todos({ d }: { d: any }) {
  return (
    <div className="card cardpad">
      <div className="sectionhead"><h2>近两周待办</h2><small>来自跟进记录的“下一步”</small></div>
      {d.todos.map((t: any, i: number) => (
        <div className="designline" key={i}>
          <span className="chip amber">{dt(t.date)}</span>
          <div><Link to={`/projects/${t.project_id}`}><b style={{ fontSize: 12 }}>{t.project_name}</b></Link><div className="sub">{t.next_action}</div></div>
        </div>
      ))}
      {d.todos.length === 0 && <div className="sub">暂无待办</div>}
    </div>
  );
}

function Alerts({ d, title = "执行提醒" }: { d: any; title?: string }) {
  return (
    <div className="card cardpad">
      <div className="sectionhead"><h2>{title}</h2><small>延期或已过计划日期的节点</small></div>
      {d.milestone_alerts.map((m: any, i: number) => (
        <div className="designline" key={i}>
          <Chip s={m.state} cls="red" />
          <div><Link to={`/projects/${m.project_id}`}><b style={{ fontSize: 12 }}>{m.project_name}</b></Link><div className="sub">{m.stage} · 计划 {dt(m.planned_date)}</div></div>
        </div>
      ))}
      {d.milestone_alerts.length === 0 && <div className="sub">暂无延期节点</div>}
    </div>
  );
}

function AdminHome() {
  const [s, , loading, err, status] = useLoad(() => get("/system/status/"), [], 30000);
  if (loading && !s) return <Loading err={err} status={status} />;
  if (!s) return <Loading err={err} status={status} />;
  const b = s.backup;
  return (
    <>
      <PageHead k="SYSTEM ADMINISTRATION" title="系统管理" sub="管理员负责账号、基础配置与运行状态；不参与报价、金额与业绩等业务数据。">
        <Link className="btn primary" to="/settings">账号与角色 →</Link>
      </PageHead>
      <Kpis items={[
        ["运行模式", s.mode, s.https ? "HTTPS" : "HTTP（隔离内网试用）"],
        ["启用账号", s.users.active, `管理员 ${s.users.admins} 个`],
        ["磁盘剩余", `${s.disk.free_gb} GB`, `共 ${s.disk.total_gb} GB`],
        ["最近备份", b && b.ok ? dt(b.at, true) : "无", b ? (b.ok ? `${b.size_mb} MB` : `失败：${b.error}`) : "请配置每日备份"],
      ]} />
      <div className="grid cols2">
        <div className="card cardpad">
          <div className="sectionhead"><h2>运行状态</h2>{s.maintenance ? <span className="chip amber">维护中</span> : <span className="chip">正常</span>}</div>
          <div className="kv">
            <span>版本</span><span>{s.version}</span>
            <span>数据库</span><span>{s.database.vendor} · {s.database.version}</span>
            <span>访问主机名</span><span>{s.allowed_hosts.join("、")}</span>
            <span>项目可见范围</span><span>{s.project_visibility === "ALL" ? "销售可查看全团队项目公共进度" : "销售仅查看本人项目"}</span>
            <span>业绩确认规则</span><span>{s.performance_confirm_mode === "OR" ? "合同/订单 或 负责人确认" : "合同/订单 且 负责人确认"}</span>
            <span>DEBUG</span><span>{s.debug ? "开启（仅演示）" : "关闭"}</span>
          </div>
        </div>
        <div className="card cardpad">
          <div className="sectionhead"><h2>运维操作</h2></div>
          <div className="notice">备份、恢复、升级、启动/停止只能在服务器主机上通过安装目录中的脚本执行（04-立即备份、05-恢复备份、07-升级程序 等），网页不提供任何命令执行能力。</div>
          {!b && <div className="warnnote" style={{ marginTop: 10 }}>尚无备份记录：请在服务器运行“04-立即备份”与“08-配置每日备份”。</div>}
          {b && b.ok && !b.copy && <div className="warnnote" style={{ marginTop: 10 }}>未配置异盘/异机备份副本，硬盘损坏时无法恢复。</div>}
        </div>
      </div>
    </>
  );
}

export default function Dashboard() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const role = me!.role;
  const [d, , loading, err, status] = useLoad(() => (role === "ADMIN" ? Promise.resolve(null) : get("/dashboard/")), [role], 30000);
  const [projects] = useLoad(() => (role === "ADMIN" ? Promise.resolve([]) : get<any[]>("/projects/" + (role === "SALES" ? "?mine=1" : ""))), [role], 30000);
  const [team] = useLoad(() => (role === "SALES" ? get<any[]>("/projects/?execution_phase=执行中") : Promise.resolve([])), [role], 30000);
  if (role === "ADMIN") return <AdminHome />;
  if (loading && !d) return <Loading err={err} status={status} />;
  if (!d) return <Loading err={err} status={status} />;

  if (role === "SALES")
    return (
      <>
        <PageHead k="MY WORKSPACE" title="我的销售工作台" sub="我的项目与跟进、全团队执行进度、我的业绩与可公开案例。销售不处理报价与金额。">
          <button className="btn primary" onClick={() => nav("/projects?new=1")}>+ 登记新项目</button>
        </PageHead>
        <Kpis items={[["我负责/参与的项目", d.my_projects, "可编辑"], ["全团队活跃项目", d.active_projects, "可查看公共进度"], ["执行中项目", d.executing_projects, "商务/财务更新，随时可看"], ["我的已确认业绩", d.performance_confirmed, "内部业绩按归属授权"]]} />
        <div className="split">
          <ProjectTable projects={projects} title="我的项目" empty="还没有项目，点击右上角登记" />
          <Todos d={d} />
        </div>
        <div className="grid cols2" style={{ marginTop: 18 }}>
          <ProjectTable projects={team} title="全团队执行中项目（公共进度）" empty="暂无执行中项目" />
          <div className="grid" style={{ alignContent: "start" }}>
            <Timeline d={d} />
            <div className="card cardpad"><div className="sectionhead"><h2>案例与业绩</h2></div><div className="actions"><Link className="btn" to="/performance">我的业绩</Link><Link className="btn" to="/cases">搜索可公开案例</Link></div></div>
          </div>
        </div>
      </>
    );

  if (role === "FINANCE")
    return (
      <>
        <PageHead k="CONTRACT & DELIVERY" title="合同确认与交付执行" sub="财务维护合同/订单确认状态与执行交付节点；本期不提供独立回款/开票/金额台账。" />
        <Kpis items={[["待确认合同/订单", d.orders_pending, "确认后自动建立执行节点"], ["执行中项目", d.executing_projects, "交付节点由商务/财务更新"], ["逾期/延期节点", d.milestone_alerts.length, "需跟进"], ["已确认业绩", d.performance_confirmed, "安全视图（无金额）"]]} />
        <div className="split">
          <div className="grid">
            <div className="card cardpad"><div className="sectionhead"><h2>待处理</h2></div><div className="actions"><Link className="btn primary" to="/execution">合同/订单确认与执行节点 →</Link></div></div>
            <ProjectTable projects={(projects || []).filter((p) => p.execution_phase !== "未启动")} title="交付执行项目" empty="暂无执行项目" />
          </div>
          <Alerts d={d} title="逾期节点" />
        </div>
      </>
    );

  const leader = role === "LEADER";
  return (
    <>
      <PageHead k={leader ? "MANAGEMENT OVERVIEW" : "BUSINESS ASSISTANT"} title={leader ? "经营与项目总览" : "商务协作工作台"} sub={leader ? "全部业务、异常项目、确认与撤销、业绩汇总、出具主体与公开案例管理" : "待报价与即将过期报价、合同确认、执行节点、业绩补录"}>
        {can("project.create") && <button className="btn" onClick={() => nav("/projects?new=1")}>+ 创建项目</button>}
        {can("quote.access") && <button className="btn primary" onClick={() => nav("/quotes?new=1")}>+ 新建报价</button>}
      </PageHead>
      <Kpis items={[
        ["草稿报价", d.quotes_draft, `共 ${d.quote_count} 份报价`],
        ["7 天内到期报价", (d.quotes_expiring || []).length, "已生成/已发送"],
        ["待确认合同/订单", d.orders_pending, "确认后生成唯一业绩"],
        leader ? ["待核实业绩", d.performance_pending ?? 0, "手工补录/导入需负责人确认"] : ["执行中项目", d.executing_projects, "执行节点维护"],
      ]} />
      <div className="split">
        <ProjectTable projects={projects} title="项目与进度" empty="暂无项目" />
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="card cardpad">
            <div className="sectionhead"><h2>即将过期报价</h2></div>
            {(d.quotes_expiring || []).map((q: any) => (
              <div className="designline" key={q.code}><span className="chip amber">{dt(q.valid_until)}</span><div><Link to={`/quotes/${q.quote_id}`}><b style={{ fontSize: 12 }}>{q.code}</b></Link><div className="sub">{q.project_name}</div></div></div>
            ))}
            {(d.quotes_expiring || []).length === 0 && <div className="sub">7 天内没有到期的报价</div>}
          </div>
          {(d.quotes_overdue || []).length > 0 && (
            <div className="card cardpad">
              <div className="sectionhead"><h2>已过有效期仍为“已发送”</h2></div>
              {d.quotes_overdue.map((q: any) => (
                <div className="designline" key={q.code}><span className="chip red">{dt(q.valid_until)}</span><div><Link to={`/quotes/${q.quote_id}`}><b style={{ fontSize: 12 }}>{q.code}</b></Link><div className="sub">{q.project_name} · 请跟进或更新报价状态</div></div></div>
              ))}
            </div>
          )}
          <Alerts d={d} title={leader ? "异常项目（延期/逾期节点）" : "执行提醒"} />
        </div>
      </div>
      <div className="grid cols2" style={{ marginTop: 18 }}>
        <Timeline d={d} />
        <Todos d={d} />
      </div>
    </>
  );
}

export { msLabel };
