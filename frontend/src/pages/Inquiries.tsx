import { useState } from "react";
import { Link } from "react-router-dom";
import { dt, get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { Chip, Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";

/** RC3-G0 询价登记：客户发来的询价资料（不含价格）。报价在报价中心生成并可关联本询价。 */
function InquiryForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const [projects] = useLoad(() => get<any>("/projects/?page_size=500"), []);
  const [accounts] = useLoad(() => get<any>("/accounts/?page_size=500"), []);
  const plist = Array.isArray(projects) ? projects : projects?.results || [];
  const alist = Array.isArray(accounts) ? accounts : accounts?.results || [];
  const f = useForm({
    project: initial?.project ?? "", inquirer: initial?.inquirer ?? "", inquirer_contact: initial?.inquirer_contact || "",
    received_on: initial?.received_on || new Date().toISOString().slice(0, 10), due_on: initial?.due_on || "", requirement: initial?.requirement || "", status: initial?.status === "CLOSED" ? "CLOSED" : "OPEN",
  });
  const [items, setItems] = useState<any[]>(initial?.items || []);
  const save = async () => {
    const body: any = { ...f.v, inquirer: f.v.inquirer || null, due_on: f.v.due_on || null, items };
    try {
      if (initial) await patch(`/inquiries/${initial.id}/`, body);
      else await post("/inquiries/", body);
      notify("询价已保存");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  const setItem = (i: number, k: string, v: string) => setItems(items.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  return (
    <Modal wide title={initial ? `询价 ${initial.code}` : "登记询价"} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.project} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="项目 *" err={f.errs.project}><select className="select" {...f.bind("project")}><option value="">请选择</option>{plist.map((p: any) => <option key={p.id} value={p.id}>{p.code} {p.name}</option>)}</select></Field>
        <Field label="询价方"><select className="select" {...f.bind("inquirer")}><option value="">（未指定）</option>{alist.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
        <Field label="询价联系人"><input className="input" {...f.bind("inquirer_contact")} /></Field>
        <Field label="收到日期 *"><input className="input" type="date" {...f.bind("received_on")} /></Field>
        <Field label="要求回复日期" err={f.errs.due_on}><input className="input" type="date" {...f.bind("due_on")} /></Field>
        {initial && <Field label="状态"><select className="select" {...f.bind("status")}><option value="OPEN">未关闭（按关联报价自动：当前 {initial.status === "CLOSED" ? "待重算" : initial.status_label}）</option><option value="CLOSED">已关闭</option></select></Field>}
        <Field label="需求说明（不填价格）" full><textarea className="input textarea" {...f.bind("requirement")} /></Field>
        <div className="formfield full">
          <label>产品明细（型号/名称/数量/单位；价格在报价中心填写）</label>
          {items.map((it, i) => (
            <div key={i} className="rowactions" style={{ marginBottom: 4 }}>
              {["model", "name", "qty", "unit", "note"].map((k) => <input key={k} className="input" style={{ width: k === "name" || k === "note" ? 160 : 90 }} placeholder={{ model: "型号", name: "名称", qty: "数量", unit: "单位", note: "备注" }[k]} value={it[k] || ""} onChange={(e) => setItem(i, k, e.target.value)} />)}
              <button className="btn mini" onClick={() => setItems(items.filter((_, j) => j !== i))}>删除</button>
            </div>
          ))}
          <button className="btn mini soft" onClick={() => setItems([...items, { model: "", name: "", qty: "1", unit: "台", note: "" }])}>+ 明细行</button>
        </div>
      </div>
    </Modal>
  );
}

export default function Inquiries() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [list, reload, loading, err] = useLoad(() => get<any>("/inquiries/" + qs({ q, status })), [q, status]);
  const rows = Array.isArray(list) ? list : list?.results || [];
  const [modal, setModal] = useState<any>(null);
  return (
    <>
      <PageHead k="INQUIRIES" title="询价登记" sub="客户/主机厂发来的询价资料（不含价格）；关联报价后为“报价中”，报价标记已发送后为“已报价”">
        {can("inquiry.write") && <button className="btn primary" onClick={() => setModal("new")}>+ 登记询价</button>}
      </PageHead>
      <div className="toolbar">
        <input className="input" placeholder="搜索编号、项目、询价方、需求" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option><option value="OPEN">待报价</option><option value="QUOTING">报价中</option><option value="QUOTED">已报价（已发送客户）</option><option value="CLOSED">已关闭</option></select>
      </div>
      <div className="card">
        {loading && !list ? <Loading err={err} /> : (
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>询价编号</th><th>项目</th><th>询价方</th><th>收到</th><th>要求回复</th><th>状态</th><th>关联报价</th><th>登记 / 最后修改</th><th /></tr></thead>
              <tbody>
                {rows.map((x: any) => (
                  <tr key={x.id}>
                    <td><b>{x.code}</b></td><td><Link to={`/projects/${x.project}`}>{x.project_name}</Link></td><td>{x.inquirer_name || "—"}</td>
                    <td>{dt(x.received_on)}</td><td>{x.due_on ? dt(x.due_on) : "—"}</td><td><Chip s={x.status_label} /></td><td className="micro">{x.quote_codes.join("、") || "—"}</td>
                    <td className="micro">{x.created_by_name} {dt(x.created_at, true)}<br />{x.updated_by_name} {dt(x.updated_at, true)}</td>
                    <td className="rowactions">{can("inquiry.write") && <button className="btn mini" onClick={() => setModal(x)}>编辑</button>}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={9} className="empty-cell">暂无询价</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {modal && <InquiryForm initial={modal === "new" ? undefined : modal} onClose={() => setModal(null)} onSaved={() => (setModal(null), reload())} />}
    </>
  );
}
