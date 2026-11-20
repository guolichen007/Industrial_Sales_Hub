import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, dt, get, money, patch, post } from "../api";
import { useAuth } from "../auth";
import { MILESTONE_STATES, PARTY_ROLES, PRESALE_PHASES, msLabel } from "../options";
import { Chip, Field, Loading, Modal, useForm, useLoad, useToast } from "../ui";
import { ProjectForm } from "./Projects";
import { Attachments } from "./common";
import { OrderForm } from "./Execution";

function PartyModal({ projectId, onClose, onSaved }: { projectId: number; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const [q, setQ] = useState("");
  const [accounts] = useLoad(() => get<any[]>("/accounts/?q=" + encodeURIComponent(q)), [q]);
  const [account, setAccount] = useState<number | "">("");
  const [role, setRole] = useState("主机厂");
  const [primary, setPrimary] = useState(false);
  const save = async () => {
    try {
      await post(`/projects/${projectId}/parties/`, { account, party_role: role, is_primary: primary });
      notify("已关联参与方");
      onSaved();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <Modal title="关联参与方" onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!account} onClick={save}>关联</button></>}>
      <div className="notice" style={{ marginBottom: 14 }}>一个项目可以关联多家主机厂、业主、投资方等；同一组织可以以不同身份参与。客户不存在时，请先在“客户资料库”中新建。</div>
      <div className="fieldset">
        <Field label="搜索客户" full><input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="输入名称或联系人" /></Field>
        <Field label="客户组织 *" full>
          <select className="select" value={account} onChange={(e) => setAccount(Number(e.target.value) || "")} size={6}>
            {(accounts || []).map((a) => <option key={a.id} value={a.id}>{a.name}　[{a.kinds.join("、")}]</option>)}
          </select>
        </Field>
        <Field label="参与身份"><select className="select" value={role} onChange={(e) => setRole(e.target.value)}>{PARTY_ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
        <Field label=" "><label className="chk"><input type="checkbox" checked={primary} onChange={(e) => setPrimary(e.target.checked)} />主要参与方</label></Field>
      </div>
    </Modal>
  );
}

function UpdateModal({ projectId, stream, onClose, onSaved }: { projectId: number; stream: "PRESALE" | "EXECUTION"; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const f = useForm({ title: "", content: "", phase: "", contact_name: "", next_action: "", next_action_date: "" });
  const save = async () => {
    try {
      await post(`/projects/${projectId}/updates/`, { ...f.v, stream, next_action_date: f.v.next_action_date || null });
      notify("进展已记录");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal title={stream === "PRESALE" ? "记录售前跟进" : "记录执行进展"} onClose={onClose}
      foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.title} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="标题 *" full err={f.errs.title}><input className="input" {...f.bind("title")} autoFocus placeholder={stream === "PRESALE" ? "例如：技术澄清会" : "例如：设备出厂检验完成"} /></Field>
        {stream === "PRESALE" && (
          <Field label="更新售前阶段（可选）">
            <select className="select" {...f.bind("phase")}><option value="">（不变）</option>{PRESALE_PHASES.map((x) => <option key={x}>{x}</option>)}</select>
          </Field>
        )}
        <Field label="联系对象"><input className="input" {...f.bind("contact_name")} /></Field>
        <Field label="内容" full><textarea className="input textarea" {...f.bind("content")} /></Field>
        <Field label="下一步行动"><input className="input" {...f.bind("next_action")} /></Field>
        <Field label="下次行动日期"><input className="input" type="date" {...f.bind("next_action_date")} /></Field>
      </div>
    </Modal>
  );
}

function MilestoneRow({ m, projectId, editable, onSaved }: { m: any; projectId: number; editable: boolean; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState({ state: m.state, owner_name: m.owner_name, planned_date: m.planned_date || "", actual_date: m.actual_date || "", note: m.note, delay_reason: m.delay_reason || "" });
  const save = async () => {
    try {
      await patch(`/projects/${projectId}/milestones/${m.id}/`, { ...v, planned_date: v.planned_date || null, actual_date: v.actual_date || null });
      notify(`节点「${m.stage}」已更新`);
      setEdit(false);
      onSaved();
    } catch (e) {
      fail(e);
    }
  };
  if (!edit)
    return (
      <tr>
        <td><b>{m.stage}</b></td>
        <td><Chip s={msLabel(m.state)} /></td>
        <td>{m.owner_name || "—"}</td>
        <td>{dt(m.planned_date)}</td>
        <td>{dt(m.actual_date)}</td>
        <td style={{ whiteSpace: "normal" }} className="micro">{m.state === "DELAYED" && m.delay_reason && <div style={{ color: "#b55c5c" }}>延期：{m.delay_reason}</div>}{m.note || ""}{m.updated_by_name && <div>更新：{m.updated_by_name}</div>}</td>
        <td>{editable && <button className="btn mini" onClick={() => setEdit(true)}>更新</button>}</td>
      </tr>
    );
  return (
    <tr>
      <td><b>{m.stage}</b></td>
      <td><select className="ms-select" value={v.state} onChange={(e) => setV({ ...v, state: e.target.value })}>{MILESTONE_STATES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
      <td><input className="ms-select" style={{ width: 80 }} value={v.owner_name} onChange={(e) => setV({ ...v, owner_name: e.target.value })} /></td>
      <td><input className="ms-select" type="date" value={v.planned_date} onChange={(e) => setV({ ...v, planned_date: e.target.value })} /></td>
      <td><input className="ms-select" type="date" value={v.actual_date} onChange={(e) => setV({ ...v, actual_date: e.target.value })} /></td>
      <td>
        {v.state === "DELAYED" && <input className="ms-select" style={{ width: 180, marginBottom: 4, borderColor: "#e3a9a3" }} value={v.delay_reason} onChange={(e) => setV({ ...v, delay_reason: e.target.value })} placeholder="延期原因（必填）" />}
        <input className="ms-select" style={{ width: 180 }} value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} placeholder="说明（销售可见，勿填金额）" />
      </td>
      <td className="rowactions"><button className="btn mini primary" disabled={v.state === "DELAYED" && !v.delay_reason.trim()} onClick={save}>保存</button><button className="btn mini" onClick={() => setEdit(false)}>取消</button></td>
    </tr>
  );
}

export default function ProjectDetail() {
  const { id } = useParams();
  const pid = Number(id);
  const nav = useNavigate();
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const [p, reload, loading, err, status] = useLoad(() => get(`/projects/${pid}/`), [pid], 30000);
  const [updates, reloadUpdates] = useLoad(() => get<any[]>(`/projects/${pid}/updates/`), [pid], 30000);
  const [related, reloadRelated] = useLoad(() => get(`/projects/${pid}/related/`), [pid]);
  const [tab, setTab] = useState("overview");
  const [modal, setModal] = useState<string>("");
  if (loading && !p) return <Loading err={err} status={status} />;
  if (!p) return <Loading err={err || "项目不存在或无权访问"} status={status} />;
  const refresh = () => {
    reload();
    reloadUpdates();
    reloadRelated();
  };
  const canExec = can("update.execution.write");
  const editable = !!p.can_edit;
  const pauseResume = async (phase: string) => {
    const note = prompt(phase === "暂停" ? "暂停原因" : "恢复说明") || "";
    try {
      await post(`/projects/${pid}/execution-phase/`, { phase, note });
      notify(`执行阶段已设为「${phase}」`);
      refresh();
    } catch (e) {
      fail(e);
    }
  };
  const removeParty = async (party: any) => {
    if (!confirm(`移除参与方「${party.account_name}（${party.party_role}）」？`)) return;
    try {
      await del(`/projects/${pid}/parties/${party.id}/`);
      notify("已移除");
      refresh();
    } catch (e) {
      fail(e);
    }
  };
  const initMs = async () => {
    try {
      await post(`/projects/${pid}/milestones/`);
      notify("已初始化执行节点");
      refresh();
    } catch (e) {
      fail(e);
    }
  };
  const done = p.milestones.filter((m: any) => m.state === "DONE" || m.state === "SKIPPED").length;
  const tabs: [string, string][] = [["overview", "项目全景"], ["presale", "售前跟进"], ["execution", "合同执行"]];
  if (editable || can("project.read")) tabs.push(["files", editable ? "附件" : "交付文件"]);
  return (
    <>
      <div className="detailbanner">
        <div className="kicker" style={{ color: "#9fc6cc" }}>{p.code} · {[p.industry, p.machine_type, p.region].filter(Boolean).join(" · ")}</div>
        <h2>{p.name}</h2>
        <div className="muted">{p.description}</div>
        <div className="tagline" style={{ marginTop: 12 }}>
          <span className="chip">售前：{p.presale_phase}</span>
          <span className="chip blue">执行：{p.execution_phase}</span>
          <span className="chip gray">销售：{p.sales_owner_name || "未指定"}</span>
          {!editable && <span className="chip gray">只读（非本人负责/参与）</span>}
          {p.milestones.length > 0 && <span className="chip gray">执行节点 {done}/{p.milestones.length}</span>}
        </div>
      </div>
      <div className="actions" style={{ marginBottom: 14 }}>
        <button className="btn" onClick={() => nav("/projects")}>← 返回列表</button>
        {editable && <button className="btn" onClick={() => setModal("edit")}>编辑项目</button>}
        {editable && can("update.presale.write") && <button className="btn soft" onClick={() => setModal("PRESALE")}>+ 售前跟进</button>}
        {canExec && <button className="btn soft" onClick={() => setModal("EXECUTION")}>+ 执行进展</button>}
        {can("quote.access") && <button className="btn primary" onClick={() => nav(`/quotes?new=1&project=${pid}`)}>+ 为此项目报价</button>}
        {can("order.confirm") && <button className="btn dark" onClick={() => setModal("order")}>登记合同/订单</button>}
      </div>
      <div className="tabbar">{tabs.map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>

      {tab === "overview" && (
        <div className="split">
          <div className="grid">
            <div className="card cardpad">
              <div className="sectionhead"><h2>参与方</h2>{editable && <button className="btn mini soft" onClick={() => setModal("party")}>+ 关联参与方</button>}</div>
              {p.parties.length === 0 ? <div className="warnnote">项目尚未关联客户。项目可独立存在，后续可随时关联多家主机厂、业主或投资方。</div> : (
                <div className="tablewrap">
                <table className="data">
                  <thead><tr><th>组织</th><th>身份</th><th>说明</th><th /></tr></thead>
                  <tbody>{p.parties.map((x: any) => (
                    <tr key={x.id}><td><b>{x.account_name}</b>{x.is_primary && <span className="pill" style={{ marginLeft: 6 }}>主要</span>}</td><td><Chip s={x.party_role} cls="blue" /></td><td>{x.note}</td>
                      <td>{editable && <button className="btn mini" onClick={() => removeParty(x)}>移除</button>}</td></tr>
                  ))}</tbody>
                </table>
                </div>
              )}
            </div>
            <div className="card cardpad">
              <div className="sectionhead"><h2>执行进度</h2><small>{canExec ? "商务/财务维护" : "只读 · 安全摘要"}</small></div>
              {p.milestones.length === 0 ? <div className="sub">合同/订单确认后自动生成执行节点。</div> : (
                <div className="progress-rail">
                  {p.milestones.map((m: any) => <div key={m.id} className={"progress-tile " + (m.state === "DONE" ? "done" : m.state === "IN_PROGRESS" || m.state === "DELAYED" ? "current" : "")}>{m.stage}<br />{msLabel(m.state)}</div>)}
                </div>
              )}
            </div>
            {related && (related.quotes.length > 0 || can("quote.access")) && (
              <div className="card cardpad">
                <div className="sectionhead"><h2>关联报价</h2>{!can("quote.access") && <small>无金额摘要</small>}</div>
                <div className="tablewrap">
                <table className="data">
                  <thead><tr><th>报价编号</th><th>接收客户</th><th>出具公司</th><th>状态</th>{can("quote.access") && <th className="right">最新版本含税</th>}</tr></thead>
                  <tbody>
                    {related.quotes.map((q: any) => (
                      <tr key={q.id}>
                        <td>{can("quote.access") ? <Link className="link" to={`/quotes/${q.id}`}>{q.code}</Link> : q.code}{q.latest_version && <span className="micro"> V{q.latest_version}</span>}</td>
                        <td>{q.recipient_name}</td><td>{q.legal_entity_name}</td><td><Chip s={q.status_label} /></td>
                        {can("quote.access") && <td className="right num">{money(q.total_gross)}</td>}
                      </tr>
                    ))}
                    {related.quotes.length === 0 && <tr><td colSpan={5} className="empty-cell">暂无报价</td></tr>}
                  </tbody>
                </table>
                </div>
              </div>
            )}
            {related && related.orders.length > 0 && (
              <div className="card cardpad">
                <div className="sectionhead"><h2>合同 / 订单确认</h2></div>
                <div className="tablewrap">
                <table className="data">
                  <thead><tr><th>依据</th><th>编号</th><th>出具主体</th><th>状态</th>{can("order.amount") && <th className="right">合同金额</th>}<th>确认人</th></tr></thead>
                  <tbody>{related.orders.map((o: any) => (
                    <tr key={o.id}><td>{o.basis_label}</td><td>{o.order_ref || "—"}</td><td>{o.legal_entity_name}</td><td><Chip s={o.status_label} /></td>
                      {can("order.amount") && <td className="right num">{money(o.contract_amount)}</td>}<td>{o.confirmed_by_name || "—"}</td></tr>
                  ))}</tbody>
                </table>
                </div>
                {can("order.amount") && related.orders.map((o: any) => <div key={`att-${o.id}`} style={{ marginTop: 10 }}><Attachments ownerType="order" ownerId={o.id} canUpload title={`合同/订单证据 · ${o.order_ref || o.basis_label}（仅商务与负责人可见）`} /></div>)}
              </div>
            )}
          </div>
          <div className="grid" style={{ alignContent: "start" }}>
            <div className="card cardpad">
              <div className="sectionhead"><h2>项目信息</h2></div>
              <div className="kv">
                <span>项目编号</span><span>{p.code}</span>
                <span>行业</span><span>{p.industry || "—"}</span>
                <span>主机类型</span><span>{p.machine_type || "—"}</span>
                <span>区域 / 地点</span><span>{[p.region, p.location].filter(Boolean).join(" · ") || "—"}</span>
                <span>投资方</span><span>{p.investor || "—"}</span>
                {"expected_amount_note" in p && <><span>预计规模</span><span>{p.expected_amount_note || "—"}</span></>}
                <span>销售负责人</span><span>{p.sales_owner_name || "—"}</span>
                <span>创建时间</span><span>{dt(p.created_at)}</span>
              </div>
            </div>
            <div className="card cardpad">
              <div className="sectionhead"><h2>最新动态</h2></div>
              <div className="timeline">
                {(updates || []).slice(0, 6).map((u) => (
                  <div className="timeitem" key={u.id}>
                    <div className="time">{dt(u.happened_at, true)} · {u.created_by_name} · {u.stream === "PRESALE" ? "售前" : "执行"}</div>
                    <b>{u.title}</b>
                    {u.content && <div className="sub">{u.content}</div>}
                  </div>
                ))}
                {updates && updates.length === 0 && <div className="sub">暂无记录</div>}
              </div>
            </div>
            {related && related.performances.length > 0 && (
              <div className="card cardpad">
                <div className="sectionhead"><h2>业绩</h2></div>
                {related.performances.map((x: any) => <div className="designline" key={x.id}><Chip s={x.status_label} /><div><b style={{ fontSize: 12 }}>{x.title}</b><div className="sub">{x.year} · {x.source_label}</div></div></div>)}
              </div>
            )}
          </div>
        </div>
      )}

      {(tab === "presale" || tab === "execution") && (
        <div className="split">
          <div className="card cardpad">
            <div className="sectionhead">
              <h2>{tab === "presale" ? "售前跟进记录" : "执行进展记录"}</h2>
              <small>{tab === "presale" ? "销售 / 商务助理录入" : "商务助理 / 财务更新；销售可查看"}</small>
            </div>
            <div className="timeline">
              {(updates || []).filter((u) => u.stream === (tab === "presale" ? "PRESALE" : "EXECUTION")).map((u) => (
                <div className="timeitem" key={u.id}>
                  <div className="time">{dt(u.happened_at, true)} · {u.created_by_name}{u.phase && ` · ${u.phase}`}{u.contact_name && ` · 联系：${u.contact_name}`}</div>
                  <b>{u.title}</b>
                  {u.content && <div className="sub" style={{ whiteSpace: "pre-wrap" }}>{u.content}</div>}
                  {u.next_action && <div className="micro">下一步：{u.next_action}{u.next_action_date && `（${u.next_action_date}）`}</div>}
                </div>
              ))}
            </div>
          </div>
          <div className="card cardpad" style={{ alignSelf: "start" }}>
            {tab === "presale"
              ? editable && can("update.presale.write") ? <button className="btn primary" onClick={() => setModal("PRESALE")}>+ 记录售前跟进</button> : <div className="sub">只读：售前跟进由项目负责销售或商务录入</div>
              : canExec ? <button className="btn primary" onClick={() => setModal("EXECUTION")}>+ 记录执行进展</button> : <div className="sub">执行进度由商务/财务维护，销售可随时查看。</div>}
          </div>
        </div>
      )}

      {tab === "execution" && (
        <div className="card cardpad" style={{ marginTop: 18 }}>
          <div className="sectionhead">
            <h2>执行节点</h2>
            <div className="rowactions">
              {canExec && p.milestones.length > 0 && p.execution_phase !== "暂停" && <button className="btn mini warn" onClick={() => pauseResume("暂停")}>暂停执行</button>}
              {canExec && p.execution_phase === "暂停" && <button className="btn mini soft" onClick={() => pauseResume("执行中")}>恢复执行</button>}
              {can("milestone.init") && p.milestones.length === 0 && related?.orders?.some((o: any) => o.status === "CONFIRMED") && <button className="btn mini" onClick={initMs}>补建节点</button>}
            </div>
          </div>
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>节点</th><th>状态</th><th>责任人</th><th>计划完成</th><th>实际完成</th><th>说明</th><th /></tr></thead>
              <tbody>
                {p.milestones.map((m: any) => <MilestoneRow key={m.id + m.state + m.note} m={m} projectId={pid} editable={canExec} onSaved={refresh} />)}
                {p.milestones.length === 0 && <tr><td colSpan={7} className="empty-cell">合同/订单确认后自动生成</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "files" && <div className="card cardpad"><Attachments ownerType="project" ownerId={pid} canUpload={editable} title={editable ? "项目附件（默认内部；“交付/进度”附件可由商务/负责人授权销售可见）" : "交付/进度文件（经商务或负责人授权可见）"} /></div>}

      {modal === "edit" && <ProjectForm initial={p} onClose={() => setModal("")} onSaved={() => (setModal(""), refresh())} />}
      {modal === "party" && <PartyModal projectId={pid} onClose={() => setModal("")} onSaved={() => (setModal(""), refresh())} />}
      {(modal === "PRESALE" || modal === "EXECUTION") && <UpdateModal projectId={pid} stream={modal} onClose={() => setModal("")} onSaved={() => (setModal(""), refresh())} />}
      {modal === "order" && <OrderForm project={p} quotes={related?.quotes || []} onClose={() => setModal("")} onSaved={() => (setModal(""), refresh())} />}
    </>
  );
}
