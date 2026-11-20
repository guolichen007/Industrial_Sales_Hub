import { useState } from "react";
import { Link } from "react-router-dom";
import { get, money, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { Chip, Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";
import { ImportModal } from "./Settings";

function PerfForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const f = useForm({
    title: initial?.title || "", year: initial?.year || new Date().getFullYear(), region: initial?.region || "", industry: initial?.industry || "",
    product_summary: initial?.product_summary || "", machine_type: initial?.machine_type || "", host_maker: initial?.host_maker || "", owner_org: initial?.owner_org || "",
    investor: initial?.investor || "", internal_amount: initial?.internal_amount || "", public_summary: initial?.public_summary || "", remark: initial?.remark || "",
  });
  const [flags, setFlags] = useState({ publishable: !!initial?.publishable, public_name_authorized: !!initial?.public_name_authorized });
  const save = async () => {
    try {
      const body: any = { ...f.v, ...flags, internal_amount: f.v.internal_amount || null };
      if (!can("performance.see_amount") || initial?.source === "ORDER") delete body.internal_amount;
      if (initial) await patch(`/performances/${initial.id}/`, body);
      else await post("/performances/", body);
      notify(initial ? "已保存" : "已补录，状态为“待核实”，需负责人确认");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal wide title={initial ? "编辑业绩" : "手工补录历史业绩"} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.title} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="业绩名称 *" full err={f.errs.title}><input className="input" {...f.bind("title")} /></Field>
        <Field label="年份 *" err={f.errs.year}><input className="input" {...f.bind("year")} /></Field>
        <Field label="地区"><input className="input" {...f.bind("region")} /></Field>
        <Field label="行业"><input className="input" {...f.bind("industry")} /></Field>
        <Field label="主机类型"><input className="input" {...f.bind("machine_type")} /></Field>
        <Field label="供货产品" full><input className="input" {...f.bind("product_summary")} /></Field>
        <Field label="主机厂（内部）"><input className="input" {...f.bind("host_maker")} /></Field>
        <Field label="业主"><input className="input" {...f.bind("owner_org")} /></Field>
        <Field label="投资方（内部）"><input className="input" {...f.bind("investor")} /></Field>
        {can("performance.see_amount") && <Field label="内部金额" err={f.errs.internal_amount} hint={initial?.source === "ORDER" ? "以合同/订单为准，不可在此修改" : undefined}><input className="input" {...f.bind("internal_amount")} disabled={initial?.source === "ORDER"} /></Field>}
        <Field label="公开简介（用于对外案例）" full><textarea className="input textarea" {...f.bind("public_summary")} /></Field>
        <Field label="对外展示" full>
          <div className="tagline">
            <label className="chk"><input type="checkbox" checked={flags.publishable} onChange={(e) => setFlags({ ...flags, publishable: e.target.checked })} />允许作为对外案例</label>
            <label className="chk"><input type="checkbox" checked={flags.public_name_authorized} onChange={(e) => setFlags({ ...flags, public_name_authorized: e.target.checked })} />业主名称已获授权公开</label>
          </div>
        </Field>
        {can("performance.see_amount") && <Field label="内部备注" full><textarea className="input textarea" {...f.bind("remark")} /></Field>}
      </div>
    </Modal>
  );
}

export default function Performance() {
  const { can, me } = useAuth();
  const { notify, fail } = useToast();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [year, setYear] = useState("");
  const [fx, setFx] = useState({ industry: "", machine_type: "", region: "", source: "", year_from: "", year_to: "" });
  const filters = { q, status, year, ...fx };
  const [facets] = useLoad(() => get<any>("/performances/facets/"), []);
  const [list, reload, loading, err, st] = useLoad(() => get<any[]>("/performances/" + qs(filters)), [q, status, year, fx.industry, fx.machine_type, fx.region, fx.source, fx.year_from, fx.year_to]);
  const [stats, reloadStats] = useLoad(() => get("/performances/stats/"), []);
  const [modal, setModal] = useState<any>(null);
  const refresh = () => (reload(), reloadStats());
  const act = async (p: any, a: "approve" | "revoke") => {
    let body = {};
    if (a === "revoke") {
      const reason = prompt("撤销原因（必填）");
      if (!reason) return;
      body = { reason };
    }
    try {
      await post(`/performances/${p.id}/${a}/`, body);
      notify(a === "approve" ? "业绩已确认" : "业绩已撤销（保留记录）");
      refresh();
    } catch (e) {
      fail(e);
    }
  };
  const seeAmt = can("performance.see_amount");
  return (
    <>
      <PageHead k="PERFORMANCE LEDGER" title="业绩台账" sub={seeAmt ? "订单确认自动生成 + 历史手工补录/导入（待核实→负责人确认）" : me!.role === "SALES" ? "我的业绩（按负责/参与归属授权，不含内部金额）" : "业绩安全视图（不含内部金额）"}>
        {can("performance.export") && <a className="btn" href={"/api/performances/export/" + qs(filters)} title={seeAmt ? "按当前筛选导出（含内部金额，导出写审计）" : "按当前筛选导出（不含内部金额）"}>导出 Excel</a>}
        {can("import.run") && <button className="btn" onClick={() => setModal("import")}>Excel 导入历史业绩</button>}
        {can("performance.write") && <button className="btn primary" onClick={() => setModal("new")}>+ 手工补录</button>}
      </PageHead>
      {stats && (
        <div className="grid cols4" style={{ marginBottom: 18 }}>
          <div className="card kpi"><div className="orb" /><div className="label">已确认业绩</div><div className="value">{stats.confirmed}</div></div>
          <div className="card kpi"><div className="orb" /><div className="label">待核实</div><div className="value">{stats.pending}</div></div>
          {seeAmt && <div className="card kpi"><div className="orb" /><div className="label">已确认金额合计</div><div className="value" style={{ fontSize: 22 }}>{money(stats.amount_total)}</div><div className="delta">内部数据 · 不进入对外案例</div></div>}
          <div className="card kpi"><div className="orb" /><div className="label">年度分布</div><div className="micro" style={{ marginTop: 10 }}>{Object.entries(stats.by_year).map(([y, v]: any) => `${y}: ${v.count}`).join("　") || "—"}</div></div>
        </div>
      )}
      <div className="toolbar">
        <input className="input" placeholder="搜索名称、产品、地区、主机厂、业主" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option><option value="PENDING">待核实</option><option value="CONFIRMED">已确认</option><option value="REVOKED">已撤销</option></select>
        <input className="input" style={{ width: 90 }} placeholder="年份" value={year} onChange={(e) => setYear(e.target.value)} />
        <input className="input" style={{ width: 90 }} placeholder="起始年" value={fx.year_from} onChange={(e) => setFx({ ...fx, year_from: e.target.value.replace(/\D/g, "") })} />
        <input className="input" style={{ width: 90 }} placeholder="截止年" value={fx.year_to} onChange={(e) => setFx({ ...fx, year_to: e.target.value.replace(/\D/g, "") })} />
        <select className="select" value={fx.industry} onChange={(e) => setFx({ ...fx, industry: e.target.value })}><option value="">全部行业</option>{(facets?.industry || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={fx.machine_type} onChange={(e) => setFx({ ...fx, machine_type: e.target.value })}><option value="">全部主机类型</option>{(facets?.machine_type || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={fx.region} onChange={(e) => setFx({ ...fx, region: e.target.value })}><option value="">全部地区</option>{(facets?.region || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={fx.source} onChange={(e) => setFx({ ...fx, source: e.target.value })}><option value="">全部来源</option><option value="ORDER">订单确认</option><option value="MANUAL">手工补录</option><option value="IMPORT">Excel 导入</option></select>
      </div>
      <div className="card">
        {loading && !list ? <Loading err={err} status={st} /> : (
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>业绩名称</th><th>年份</th><th>地区 · 行业</th><th>供货产品</th><th>主机</th><th>来源</th>{seeAmt && <th className="right">内部金额</th>}<th>状态</th><th>对外</th><th /></tr></thead>
              <tbody>
                {(list || []).map((p) => (
                  <tr key={p.id}>
                    <td><b>{p.title}</b>{p.project && <div className="micro"><Link to={`/projects/${p.project}`}>{p.project_name}</Link></div>}</td>
                    <td>{p.year}</td><td>{[p.region, p.industry].filter(Boolean).join(" · ") || "—"}</td><td>{p.product_summary || "—"}</td><td>{p.machine_type || "—"}</td>
                    <td className="micro">{p.source_label}{p.order_ref && <div>{p.order_ref}</div>}</td>
                    {seeAmt && <td className="right num">{money(p.internal_amount)}</td>}
                    <td><Chip s={p.status_label} /></td>
                    <td>{p.publishable ? <span className="chip">可公开</span> : <span className="chip gray">内部</span>}</td>
                    <td className="rowactions">
                      {can("performance.write") && p.status !== "REVOKED" && <button className="btn mini" onClick={() => setModal(p)}>编辑</button>}
                      {p.source === "ORDER" && p.status === "CONFIRMED" && can("performance.approve") && <span className="micro" title="订单业绩请在“项目执行 → 合同/订单确认”中撤销">经订单撤销</span>}
                      {can("performance.approve") && p.status === "PENDING" && <button className="btn mini primary" onClick={() => act(p, "approve")}>确认</button>}
                      {can("performance.approve") && p.status === "CONFIRMED" && p.source !== "ORDER" && <button className="btn mini warn" onClick={() => act(p, "revoke")}>撤销</button>}
                    </td>
                  </tr>
                ))}
                {list && list.length === 0 && <tr><td colSpan={10} className="empty-cell">暂无业绩</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {modal === "import" && <ImportModal kind="performances" onClose={() => setModal(null)} onDone={refresh} />}
      {modal && modal !== "import" && <PerfForm initial={modal === "new" ? undefined : modal} onClose={() => setModal(null)} onSaved={() => (setModal(null), refresh())} />}
    </>
  );
}
