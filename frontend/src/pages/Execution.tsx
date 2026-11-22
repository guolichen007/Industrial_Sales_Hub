import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { EXECUTION_PHASES, msLabel } from "../options";
import { Chip, Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";

export function OrderForm({ project, quotes, onClose, onSaved }: { project: any; quotes: any[]; onClose: () => void; onSaved: () => void }) {
  const { can, me } = useAuth();
  const { notify, fail } = useToast();
  const [ents] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [key] = useState(() => `ord-${project.id}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const f = useForm({ basis: "CONTRACT", quote: "", legal_entity: "", order_ref: "", signed_on: new Date().toISOString().slice(0, 10), contract_amount: "", product_summary: "", reason: "" });
  const [busy, setBusy] = useState(false);
  const leaderOnly = f.v.basis === "LEADER" || me!.flags.performance_confirm_mode === "AND";
  const canConfirmNow = !leaderOnly || can("performance.approve");
  const save = async (confirmNow: boolean) => {
    setBusy(true);
    try {
      await post("/order-confirmations/", {
        project: project.id, basis: f.v.basis, quote: f.v.quote || null, legal_entity: f.v.legal_entity || undefined, order_ref: f.v.order_ref,
        signed_on: f.v.signed_on || null, ...(can("order.amount") ? { contract_amount: f.v.contract_amount || null } : {}), product_summary: f.v.product_summary, reason: f.v.reason,
        idempotency_key: key, confirm_now: confirmNow,
      });
      notify(confirmNow ? "已确认：业绩已生成，项目进入合同执行" : "已登记，待确认");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`登记合同 / 订单确认 · ${project.name}`} onClose={onClose}
      foot={<>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn" disabled={busy} onClick={() => save(false)}>仅登记</button>
        <button className="btn primary" disabled={busy || !canConfirmNow} onClick={() => save(true)} title={canConfirmNow ? "" : "需由部门负责人确认"}>登记并确认业绩</button>
      </>}>
      <div className="notice" style={{ marginBottom: 14 }}>
        业绩确认规则（待甲方最终确认，当前为「{me!.flags.performance_confirm_mode === "AND" ? "合同/订单 且 负责人确认" : "合同/订单 或 负责人确认"}」）。同一出具主体下合同/订单号不能重复；重复提交不会生成两条业绩。
      </div>
      <div className="fieldset">
        <Field label="确认依据 *">
          <select className="select" {...f.bind("basis")}><option value="CONTRACT">合同</option><option value="ORDER">订单</option><option value="LEADER">负责人确认（无合同/订单号）</option></select>
        </Field>
        {quotes.length > 0 && (
          <Field label="关联报价（可选）" hint="绑定该报价最新的已冻结版本；自动带出出具主体和客户">
            <select className="select" {...f.bind("quote")}><option value="">（无报价/线下直接签约）</option>{quotes.filter((q) => (q.frozen_revisions || []).length).map((q) => <option key={q.id} value={q.id}>{q.code}-V{q.frozen_revisions[q.frozen_revisions.length - 1].version} → {q.recipient_name}</option>)}</select>
          </Field>
        )}
        {!f.v.quote && (
          <Field label="出具主体 *" err={f.errs.legal_entity}>
            <select className="select" {...f.bind("legal_entity")}><option value="">请选择</option>{(ents || []).map((e) => <option key={e.id} value={e.id}>{e.short_name}</option>)}</select>
          </Field>
        )}
        {f.v.basis !== "LEADER" && <Field label="合同/订单号 *" err={f.errs.order_ref}><input className="input" {...f.bind("order_ref")} /></Field>}
        <Field label="签订日期"><input className="input" type="date" {...f.bind("signed_on")} /></Field>
        {can("order.amount") ? <Field label="合同金额（含税）" err={f.errs.contract_amount}><input className="input" {...f.bind("contract_amount")} inputMode="decimal" /></Field>
          : <Field label="合同金额"><div className="hint" style={{ paddingTop: 8 }}>由负责人或商务录入（财务本期不维护金额）</div></Field>}
        <Field label="供货产品" full><input className="input" {...f.bind("product_summary")} placeholder="例如：集中润滑系统 2 套、安全制动器 4 台" /></Field>
        <Field label={f.v.basis === "LEADER" ? "确认理由 *" : "说明"} full err={f.errs.reason}><textarea className="input textarea" {...f.bind("reason")} /></Field>
      </div>
    </Modal>
  );
}

export default function Execution() {
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const [exec, setExec] = useState("执行中");
  const [projects, , loading, err] = useLoad(() => get<any[]>("/projects/" + qs({ execution_phase: exec })), [exec], 30000);
  const [orders, reloadOrders] = useLoad(() => (can("order.access") ? get<any[]>("/order-confirmations/") : Promise.resolve([])), [], 30000);
  const confirm_ = async (o: any) => {
    try {
      await post(`/order-confirmations/${o.id}/confirm/`);
      notify("已确认并生成业绩");
      reloadOrders();
    } catch (e) {
      fail(e);
    }
  };
  const setAmount = async (o: any) => {
    const v = prompt(`合同 ${o.order_ref || "（负责人确认）"} 的含税金额`, o.contract_amount || "");
    if (v === null || v === "") return;
    try {
      await post(`/order-confirmations/${o.id}/amount/`, { contract_amount: v });
      notify("合同金额已更新，订单业绩金额已同步");
      reloadOrders();
    } catch (e) {
      fail(e);
    }
  };
  const revoke = async (o: any) => {
    const reason = prompt("撤销原因（必填，将保留记录）");
    if (!reason) return;
    try {
      await post(`/order-confirmations/${o.id}/revoke/`, { reason });
      notify("已撤销，关联业绩标记为已撤销");
      reloadOrders();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <>
      <PageHead k="PROJECT EXECUTION" title="项目执行" sub={can("update.execution.write") ? "中标后的执行进度由商务助理与财务更新；销售可随时查看。页面每 30 秒自动刷新。" : "查看已签约项目的执行进度（安全摘要，不含金额）。页面每 30 秒自动刷新。"} />
      <div className="toolbar">
        <select className="select" value={exec} onChange={(e) => setExec(e.target.value)}><option value="">全部</option>{EXECUTION_PHASES.map((x) => <option key={x}>{x}</option>)}</select>
      </div>
      <div className="grid">
        {loading && !projects ? <Loading err={err} /> : (projects || []).map((p) => <ExecCard key={p.id} p={p} />)}
        {projects && projects.length === 0 && <div className="card"><div className="empty">没有处于该执行阶段的项目</div></div>}
      </div>
      {can("order.access") && (
        <div className="card cardpad" style={{ marginTop: 20 }}>
          <div className="sectionhead"><h2>合同 / 订单确认记录</h2><small>业绩归集的唯一受控入口 · 状态机 待确认 → 已确认 → 已撤销</small></div>
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>项目</th><th>依据</th><th>编号</th><th>出具主体</th><th>客户</th>{can("order.amount") && <th>报价版本</th>}{can("order.amount") && <th className="right">合同金额</th>}<th>状态</th><th>确认</th><th /></tr></thead>
              <tbody>
                {(orders || []).map((o) => (
                  <tr key={o.id}>
                    <td><Link to={`/projects/${o.project}`}>{o.project_name}</Link></td><td>{o.basis_label}</td><td>{o.order_ref || "—"}</td><td>{o.legal_entity_name}</td><td>{o.customer_name || "—"}</td>
                    {can("order.amount") && <td className="micro">{o.quote_code || "—"}</td>}
                    {can("order.amount") && <td className="right num">{o.contract_amount ? Number(o.contract_amount).toLocaleString("zh-CN", { minimumFractionDigits: 2 }) : "—"}</td>}
                    <td><Chip s={o.status_label} /></td><td className="micro">{o.confirmed_by_name}{o.revoke_reason && <div>撤销：{o.revoke_reason}</div>}</td>
                    <td className="rowactions">
                      {o.status === "DRAFT" && can("order.confirm") && <button className="btn mini primary" onClick={() => confirm_(o)}>确认</button>}
                      {o.status !== "REVOKED" && can("order.amount") && <button className="btn mini" onClick={() => setAmount(o)}>{o.contract_amount ? "改金额" : "补录金额"}</button>}
                      {o.status === "CONFIRMED" && can("order.revoke") && <button className="btn mini warn" onClick={() => revoke(o)}>撤销</button>}
                    </td>
                  </tr>
                ))}
                {orders && orders.length === 0 && <tr><td colSpan={9} className="empty-cell">暂无记录。请在项目详情中“登记合同/订单”。</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function ExecCard({ p }: { p: any }) {
  const [d] = useLoad(() => get(`/projects/${p.id}/`), [p.id], 30000);
  return (
    <div className="card cardpad">
      <div className="sectionhead">
        <div><Link className="link" to={`/projects/${p.id}`} style={{ fontWeight: 750 }}>{p.name}</Link> <span className="micro">{p.code} · {p.sales_owner_name}</span></div>
        <Chip s={p.execution_phase} />
      </div>
      {d?.tracks && (d.tracks.delivery || d.tracks.payment) && (
        <div className="micro" style={{ marginBottom: 6 }}>
          项目总体状态：生产/交付 <b>{d.tracks.delivery?.label || "—"}</b> ｜ 资金 <b>{d.tracks.payment?.label || "—"}</b>
          {d.tracks.multi_order && <span style={{ color: "#8a5a12" }}>（本项目 {d.tracks.active_order_count} 张有效合同/订单，非各合同各自状态）</span>}
        </div>
      )}
      {d ? (d.milestones.length ? (
        <div className="progress-rail">
          {d.milestones.map((m: any) => <div key={m.id} className={"progress-tile " + (m.state === "DONE" ? "done" : m.state === "IN_PROGRESS" || m.state === "DELAYED" ? "current" : "")} title={m.note}>{m.stage}<br />{msLabel(m.state)}</div>)}
        </div>
      ) : <div className="sub">尚未建立执行节点</div>) : <div className="sub">加载中…</div>}
    </div>
  );
}
