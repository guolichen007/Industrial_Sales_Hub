import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { EXECUTION_PHASES, PRESALE_PHASES } from "../options";
import { Chip, Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";

export function ProjectForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (p: any) => void }) {
  const { can, me } = useAuth();
  const { notify, fail } = useToast();
  const [users] = useLoad(() => get<any[]>("/user-options/"), []);
  const f = useForm({
    name: initial?.name || "", industry: initial?.industry || "", machine_type: initial?.machine_type || "", region: initial?.region || "",
    location: initial?.location || "", investor: initial?.investor || "", description: initial?.description || "",
    presale_phase: initial?.presale_phase || "线索发现", sales_owner: initial?.sales_owner ?? (me!.role === "SALES" ? me!.id : ""), expected_amount_note: initial?.expected_amount_note || "",
  });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...f.v, sales_owner: f.v.sales_owner || null };
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
        <Field label="售前阶段">
          <select className="select" {...f.bind("presale_phase")}>{PRESALE_PHASES.map((x) => <option key={x}>{x}</option>)}</select>
        </Field>
        <Field label="销售负责人" hint={me!.role === "SALES" ? "销售不能变更负责人" : undefined}>
          <select className="select" {...f.bind("sales_owner")} disabled={me!.role === "SALES" && !!initial}>
            <option value="">（未指定）</option>
            {(users || []).filter((u) => u.role === "SALES" || u.role === "LEADER").map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </Field>
        <Field label="预计规模（文字，选填）" hint={can("quote.access") ? undefined : "仅描述，不录入金额"}><input className="input" {...f.bind("expected_amount_note")} /></Field>
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
  const [list, , loading, err] = useLoad(() => get<any[]>("/projects/" + qs({ q, presale_phase: phase, execution_phase: exec, mine: mine ? 1 : "" })), [q, phase, exec, mine]);
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
              <thead><tr><th>项目 / 编号</th><th>参与方</th><th>行业 · 主机</th><th>区域</th><th>售前</th><th>执行</th><th>进度</th><th>销售</th>{can("quote.access") && <th>报价</th>}<th>更新</th></tr></thead>
              <tbody>
                {(list || []).map((p) => (
                  <tr key={p.id}>
                    <td><Link className="link" to={`/projects/${p.id}`}>{p.name}</Link><div className="micro">{p.code}</div></td>
                    <td style={{ whiteSpace: "normal", maxWidth: 280 }} className="micro">{p.party_names.length ? p.party_names.join("、") : <span className="chip gray">未关联</span>}</td>
                    <td>{[p.industry, p.machine_type].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{p.region || "—"}</td>
                    <td><Chip s={p.presale_phase} /></td>
                    <td><Chip s={p.execution_phase} /></td>
                    <td className="micro">{p.milestone_total ? `${p.milestone_done}/${p.milestone_total}` : "—"}</td>
                    <td>{p.sales_owner_name || "—"}</td>
                    {can("quote.access") && <td>{p.quote_count}</td>}
                    <td className="micro">{new Date(p.updated_at).toLocaleDateString("zh-CN")}</td>
                  </tr>
                ))}
                {list && list.length === 0 && <tr><td colSpan={10} className="empty-cell">没有符合条件的项目</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && <ProjectForm onClose={() => setCreating(false)} onSaved={(p) => nav(`/projects/${p.id}`)} />}
    </>
  );
}
