import { Link, useNavigate } from "react-router-dom";
import { get, dt } from "../api";
import { useAuth } from "../auth";
import { Chip, Loading, PageHead, useLoad } from "../ui";

export default function Dashboard() {
  const { me, can } = useAuth();
  const nav = useNavigate();
  const [d, , loading, err] = useLoad(() => get("/dashboard/"), []);
  const [projects] = useLoad(() => get<any[]>("/projects/"), []);
  if (loading || !d) return <Loading err={err} />;
  const role = me!.role;
  const title = role === "SALES" ? "我的销售工作台" : role === "FINANCE" ? "项目执行与财务协作" : role === "ASSISTANT" ? "商务协作工作台" : "经营与项目总览";
  const sub = role === "SALES" ? "集中查看我参与的项目、跟进记录和安全版执行进度" : "围绕项目的报价、订单、执行、业绩和对外案例形成完整闭环";
  const kpis: [string, any, string][] = [
    [role === "SALES" ? "我的活跃项目" : "活跃项目", d.active_projects, "项目独立建档，多家参与方"],
    ["执行中项目", d.executing_projects, "销售可看节点，商务/财务更新"],
    can("quote.access") ? ["待跟进报价", d.quotes_open, `共 ${d.quote_count} 份报价 · 版本冻结`] : ["近期动态", d.recent_updates.length, "项目跟进与执行记录"],
    ["已确认业绩", d.performance_confirmed, d.performance_pending != null ? `待核实 ${d.performance_pending} 条` : "内部台账与公开案例分离"],
  ];
  return (
    <>
      <PageHead k="WORKSPACE OVERVIEW" title={title} sub={sub}>
        {can("project.create") && <button className="btn" onClick={() => nav("/projects?new=1")}>+ 创建项目</button>}
        {can("quote.access") && <button className="btn primary" onClick={() => nav("/quotes?new=1")}>+ 新建报价</button>}
      </PageHead>
      <div className="grid cols4" style={{ marginBottom: 20 }}>
        {kpis.map(([l, v, dlt]) => (
          <div className="card kpi" key={l}>
            <div className="orb" />
            <div className="label">{l}</div>
            <div className="value">{v ?? 0}</div>
            <div className="delta">{dlt}</div>
          </div>
        ))}
      </div>
      <div className="split">
        <div className="card cardpad">
          <div className="sectionhead">
            <h2>重点项目与进度</h2>
            <Link className="btn mini" to="/projects">进入项目中心 →</Link>
          </div>
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>项目 / 编号</th><th>售前阶段</th><th>执行阶段</th><th>执行进度</th><th>负责人</th></tr></thead>
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
                {projects && projects.length === 0 && <tr><td colSpan={5} className="empty-cell">暂无项目</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card cardpad">
          <div className="sectionhead"><h2>最近项目动态</h2></div>
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
      </div>
      <div className="grid cols2" style={{ marginTop: 18 }}>
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
        <div className="card cardpad">
          <div className="sectionhead"><h2>执行提醒</h2><small>延期或已过计划日期的节点</small></div>
          {d.milestone_alerts.map((m: any, i: number) => (
            <div className="designline" key={i}>
              <Chip s={m.state} cls="red" />
              <div><Link to={`/projects/${m.project_id}`}><b style={{ fontSize: 12 }}>{m.project_name}</b></Link><div className="sub">{m.stage} · 计划 {dt(m.planned_date)}</div></div>
            </div>
          ))}
          {d.milestone_alerts.length === 0 && <div className="sub">暂无延期节点</div>}
        </div>
      </div>
    </>
  );
}
