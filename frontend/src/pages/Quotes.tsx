import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { dt, get, money, post, qs } from "../api";
import { QUOTE_STATUS } from "../options";
import { Chip, Field, Loading, Modal, PageHead, useLoad, useToast } from "../ui";

function NewQuote({ projectId, onClose }: { projectId?: number; onClose: () => void }) {
  const nav = useNavigate();
  const { fail } = useToast();
  const [ents] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [projects] = useLoad(() => get<any[]>("/projects/"), []);
  const [project, setProject] = useState<number | "">(projectId || "");
  const [detail] = useLoad(() => (project ? get(`/projects/${project}/`) : Promise.resolve(null)), [project]);
  const [accounts] = useLoad(() => get<any[]>("/accounts/"), []);
  const [entity, setEntity] = useState<number | "">("");
  const [recipient, setRecipient] = useState<number | "">("");
  const [contact, setContact] = useState<number | "">("");
  const [title, setTitle] = useState("");
  useEffect(() => {
    if (ents && !entity) setEntity(ents.filter((e) => e.active)[0]?.id || "");
  }, [ents, entity]);
  const partyIds = new Set((detail?.parties || []).map((p: any) => p.account));
  const recAcc = (accounts || []).find((a) => a.id === recipient);
  const create = async () => {
    try {
      const q = await post("/quotations/", { project, legal_entity: entity, recipient, recipient_contact: contact || null, title });
      nav(`/quotes/${q.id}`);
    } catch (e) {
      fail(e);
    }
  };
  return (
    <Modal title="新建报价 · 第 1 步：出具公司与接收客户" onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!project || !entity || !recipient} onClick={create}>创建并编辑明细 →</button></>}>
      <div className="steps"><span className="step current">① 主体与客户</span><span className="step">② 产品与参数</span><span className="step">③ 价格与条款</span><span className="step">④ 预览与生成</span></div>
      <Field label="出具公司（决定模板）*">
        <div className="grid cols3">
          {(ents || []).filter((e) => e.active).map((e) => (
            <div key={e.id} className={"choice" + (entity === e.id ? " selected" : "")} onClick={() => setEntity(e.id)} style={{ cursor: "pointer" }}>
              <h3>{e.short_name}</h3><small>{e.name}<br />模板 {e.template_key}</small>
            </div>
          ))}
        </div>
      </Field>
      <div className="fieldset" style={{ marginTop: 14 }}>
        <Field label="所属项目 *" full>
          <select className="select" value={project} onChange={(e) => (setProject(Number(e.target.value) || ""), setRecipient(""))}>
            <option value="">请选择项目</option>
            {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
          </select>
        </Field>
        <Field label="接收客户 *" full hint="报价必须明确接收客户；选择不会改变项目归属。带 ★ 为本项目参与方">
          <select className="select" value={recipient} onChange={(e) => (setRecipient(Number(e.target.value) || ""), setContact(""))}>
            <option value="">请选择接收客户</option>
            {[...(accounts || [])].sort((a, b) => Number(partyIds.has(b.id)) - Number(partyIds.has(a.id))).map((a) => <option key={a.id} value={a.id}>{partyIds.has(a.id) ? "★ " : ""}{a.name}</option>)}
          </select>
        </Field>
        <Field label="联系人">
          <select className="select" value={contact} onChange={(e) => setContact(Number(e.target.value) || "")}>
            <option value="">（不指定）</option>
            {(recAcc?.contacts || []).map((c: any) => <option key={c.id} value={c.id}>{c.name} {c.position}</option>)}
          </select>
        </Field>
        <Field label="报价标题（选填）"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

export default function Quotes() {
  const [sp, setSp] = useSearchParams();
  const [creating, setCreating] = useState(sp.get("new") === "1");
  const presetProject = Number(sp.get("project")) || undefined;
  useEffect(() => {
    if (sp.get("new")) setSp({}, { replace: true });
  }, [sp, setSp]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [entity, setEntity] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [ents] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [list, , loading, err] = useLoad(() => get<any[]>("/quotations/" + qs({ q, status, legal_entity: entity, date_from: from, date_to: to })), [q, status, entity, from, to]);
  return (
    <>
      <PageHead k="QUOTATION CENTER" title="报价中心" sub="三公司固定模板 · 后端 Decimal 计算 · 生成即冻结版本（仅商务助理/负责人/管理员可见）">
        <button className="btn primary" onClick={() => setCreating(true)}>+ 新建报价</button>
      </PageHead>
      <div className="toolbar">
        <input className="input" placeholder="搜索编号、项目、接收客户、产品型号" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={entity} onChange={(e) => setEntity(e.target.value)}><option value="">全部出具公司</option>{(ents || []).map((e) => <option key={e.id} value={e.id}>{e.short_name}</option>)}</select>
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(QUOTE_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input className="input" style={{ width: 150 }} type="date" value={from} onChange={(e) => setFrom(e.target.value)} title="创建日期起" />
        <input className="input" style={{ width: 150 }} type="date" value={to} onChange={(e) => setTo(e.target.value)} title="创建日期止" />
      </div>
      <div className="card">
        {loading && !list ? <Loading err={err} /> : (
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>报价编号</th><th>项目</th><th>接收客户</th><th>出具公司</th><th>版本</th><th className="right">最新含税合计</th><th>状态</th><th>报价人</th><th>创建</th></tr></thead>
              <tbody>
                {(list || []).map((x) => (
                  <tr key={x.id}>
                    <td><Link className="link" to={`/quotes/${x.id}`}>{x.code}</Link>{x.title && <div className="micro">{x.title}</div>}</td>
                    <td><Link to={`/projects/${x.project}`}>{x.project_name}</Link><div className="micro">{x.project_code}</div></td>
                    <td>{x.recipient_name}</td><td>{x.legal_entity_name}</td>
                    <td>{x.revisions.map((r: any) => <span key={r.id} className={"chip " + (r.status === "GENERATED" ? "" : "gray")} style={{ marginRight: 3 }}>V{r.version}</span>)}</td>
                    <td className="right num">{money(x.latest?.total_gross)}</td>
                    <td><Chip s={x.status_label} /></td><td>{x.owner_name}</td><td className="micro">{dt(x.created_at)}</td>
                  </tr>
                ))}
                {list && list.length === 0 && <tr><td colSpan={9} className="empty-cell">暂无报价</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && <NewQuote projectId={presetProject} onClose={() => setCreating(false)} />}
    </>
  );
}
