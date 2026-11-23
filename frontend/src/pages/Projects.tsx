import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { dt, get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { EXECUTION_PHASES, PRESALE_PHASES } from "../options";
import { Chip, Field, Loading, Modal, PageHead, TrackStatusCell, useForm, useLoad, useToast } from "../ui";

export function ProjectForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (p: any) => void }) {
  const { can, me } = useAuth();
  const { notify, fail } = useToast();
  const [users] = useLoad(() => get<any[]>("/user-options/"), []);
  const f = useForm({
    name: initial?.name || "", industry: initial?.industry || "", machine_type: initial?.machine_type || "", region: initial?.region || "",
    location: initial?.location || "", investor: initial?.investor || "", description: initial?.description || "",
    presale_phase: initial?.presale_phase || "线索发现", sales_owner: initial?.sales_owner ?? (me!.role === "SALES" ? me!.id : ""), expected_amount_note: initial?.expected_amount_note || "", delivery_due_on: initial?.delivery_due_on || "",
  });
  const [busy, setBusy] = useState(false);
  const [members, setMembers] = useState<number[]>(initial?.members || []);
  const save = async () => {
    setBusy(true);
    try {
      const body: any = { ...f.v, sales_owner: f.v.sales_owner || null, delivery_due_on: f.v.delivery_due_on || null };
      if (!can("delivery.write") || !initial?.sales_locked_at) delete body.delivery_due_on;
      if (can("project.edit_any")) body.members = members;
      if (!can("project.see_scale_note")) delete body.expected_amount_note;
      if (me!.role === "SALES") delete body.sales_owner;
      const p = initial ? await patch(`/projects/${initial.id}/`, body) : await post("/projects/", body);
      notify(initial ? "项目已更新" : `项目 ${p.code} 已创建，可继续关联参与方`);
      onSaved(p);
    } catch (e) {
      f.catchErr(e);
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={initial ? "编辑项目" : "创建项目（可暂不关联客户）"} onClose={onClose}
      foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={busy || !f.v.name} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="项目名称 *" full err={f.errs.name}><input className="input" {...f.bind("name")} autoFocus /></Field>
        <Field label="行业"><input className="input" {...f.bind("industry")} placeholder="港口 / 电厂 / 钢厂…" /></Field>
        <Field label="主机类型"><input className="input" {...f.bind("machine_type")} placeholder="斗轮机 / 皮带机 / 圆堆…" /></Field>
        <Field label="区域"><input className="input" {...f.bind("region")} /></Field>
        <Field label="项目地点"><input className="input" {...f.bind("location")} /></Field>
        <Field label="投资方（文字）"><input className="input" {...f.bind("investor")} /></Field>
        <Field label="售前阶段" hint={me!.role === "SALES" ? "“成交”由合同/订单确认自动产生" : undefined}>
          <select className="select" {...f.bind("presale_phase")}>{PRESALE_PHASES.filter((x) => me!.role !== "SALES" || x !== "成交" || initial?.presale_phase === "成交").map((x) => <option key={x}>{x}</option>)}</select>
        </Field>
        <Field label="销售负责人" hint={me!.role === "SALES" ? "销售不能变更负责人" : undefined}>
          <select className="select" {...f.bind("sales_owner")} disabled={me!.role === "SALES"}>
            <option value="">（未指定）</option>
            {(users || []).filter((u) => u.role === "SALES" || u.role === "LEADER").map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </Field>
        {can("project.edit_any") && (
          <Field label="参与成员（可编辑该项目、查看项目附件）" full hint="仅商务/负责人可指派；成员不扩大业绩可见范围">
            <div className="tagline">{(users || []).filter((u) => u.role === "SALES").map((u) => (
              <label key={u.id} className="chk"><input type="checkbox" checked={members.includes(u.id)} onChange={(e) => setMembers(e.target.checked ? [...members, u.id] : members.filter((x) => x !== u.id))} />{u.name}</label>
            ))}</div>
          </Field>
        )}
        {can("project.see_scale_note") && <Field label="预计规模（内部，仅商务/负责人可见）"><input className="input" {...f.bind("expected_amount_note")} /></Field>}
        {can("delivery.write") && initial?.sales_locked_at && <Field label="计划交付完结日期（逾期提醒）"><input className="input" type="date" {...f.bind("delivery_due_on")} /></Field>}
        <Field label="项目说明" full><textarea className="input textarea" {...f.bind("description")} /></Field>
      </div>
    </Modal>
  );
}

export default function Projects() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [q, setQ] = useState("");
  const [phase, setPhase] = useState("");
  const [exec, setExec] = useState("");
  const [mine, setMine] = useState(false);
  const [creating, setCreating] = useState(sp.get("new") === "1");
  const [list, , loading, err] = useLoad(() => get<any[]>("/projects/" + qs({ q, presale_phase: phase, execution_phase: exec, mine: mine ? 1 : "" })), [q, phase, exec, mine], 30000);
  useEffect(() => {
    if (sp.get("new")) setSp({}, { replace: true });
  }, [sp, setSp]);
  return (
    <>
      <PageHead k="PROJECT CENTER" title="项目中心" sub="项目独立建档：一个项目可关联多家主机厂、业主、投资方；售前与执行两条进度线">
        {can("project.create") && <button className="btn primary" onClick={() => setCreating(true)}>+ 创建项目</button>}
      </PageHead>
      <div className="toolbar">
        <input className="input" placeholder="搜索项目名称、编号、参与方、主机类型、区域" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={phase} onChange={(e) => setPhase(e.target.value)}><option value="">全部售前阶段</option>{PRESALE_PHASES.map((x) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={exec} onChange={(e) => setExec(e.target.value)}><option value="">全部执行阶段</option>{EXECUTION_PHASES.map((x) => <option key={x}>{x}</option>)}</select>
        <label className="chk"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />只看我负责/参与的</label>
      </div>
      <div className="card">
        {loading && !list ? <Loading err={err} /> : (
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>项目 / 编号</th><th>参与方</th><th>行业 · 主机</th><th>区域</th><th>售前</th><th>交付 / 资金（项目总体）</th><th>销售</th><th>权限</th><th>资料最后修改</th></tr></thead>
              <tbody>
                {(list || []).map((p) => (
                  <tr key={p.id}>
                    <td><Link className="link" to={`/projects/${p.id}`}>{p.name}</Link><div className="micro">{p.code}</div></td>
                    <td style={{ whiteSpace: "normal", maxWidth: 280 }} className="micro">{p.party_names.length ? p.party_names.join("、") : <span className="chip gray">未关联</span>}</td>
                    <td>{[p.industry, p.machine_type].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{p.region || "—"}</td>
                    <td><Chip s={p.presale_phase} /></td>
                    <td style={{ whiteSpace: "normal", minWidth: 200 }}><TrackStatusCell s={p.status} /></td>
                    <td>{p.sales_owner_name || "—"}</td>
                    <td>{p.can_edit ? <span className="chip">可编辑</span> : <span className="chip gray">只读</span>}</td>
                    <td className="micro">{dt(p.updated_at, true)}<div>{p.updated_by_name || ""}</div></td>
                  </tr>
                ))}
                {list && list.length === 0 && <tr><td colSpan={9} className="empty-cell">没有符合条件的项目</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && <ProjectForm onClose={() => setCreating(false)} onSaved={(p) => nav(`/projects/${p.id}`)} />}
    </>
  );
}
