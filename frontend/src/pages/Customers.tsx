import { useState } from "react";
import { Link } from "react-router-dom";
import { del, get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { EXECUTION_PHASES, PARTY_ROLES, PRESALE_PHASES } from "../options";
import { Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";

function AccountForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const f = useForm({ name: initial?.name || "", industry: initial?.industry || "", region: initial?.region || "", address: initial?.address || "", remark: initial?.remark || "" });
  const [kinds, setKinds] = useState<string[]>(initial?.kinds || []);
  const [cats, setCats] = useState<number[]>(initial?.categories || []);
  const [allCats] = useLoad(() => get<any[]>("/account-categories/"), []);
  const [dups] = useLoad(() => (f.v.name.length >= 2 ? get<any[]>("/accounts/check-duplicate/" + qs({ name: f.v.name, exclude: initial?.id })) : Promise.resolve([])), [f.v.name]);
  const save = async () => {
    try {
      if (initial) await patch(`/accounts/${initial.id}/`, { ...f.v, kinds, categories: cats });
      else await post("/accounts/", { ...f.v, kinds, categories: cats });
      notify("客户已保存");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal title={initial ? "编辑客户" : "新建客户组织"} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.name} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="组织名称 *" full err={f.errs.name}><input className="input" {...f.bind("name")} autoFocus /></Field>
        {dups && dups.length > 0 && <div className="warnnote formfield full">可能重复：{dups.map((d) => d.name).join("、")}。请确认不是同一家单位。</div>}
        <Field label="业务身份（可多选）" full>
          <div className="tagline">{PARTY_ROLES.map((k) => (
            <label key={k} className="chk"><input type="checkbox" checked={kinds.includes(k)} onChange={(e) => setKinds(e.target.checked ? [...kinds, k] : kinds.filter((x) => x !== k))} />{k}</label>
          ))}</div>
        </Field>
        <Field label="客户类别（自定义，可多选）" full hint="类别由负责人/商务在下方“客户类别”中维护">
          <div className="tagline">{(allCats || []).filter((c) => c.active).map((c) => (
            <label key={c.id} className="chk"><input type="checkbox" checked={cats.includes(c.id)} onChange={(e) => setCats(e.target.checked ? [...cats, c.id] : cats.filter((x) => x !== c.id))} />{c.name}</label>
          ))}{allCats && allCats.length === 0 && <span className="micro">暂无类别</span>}</div>
        </Field>
        <Field label="行业"><input className="input" {...f.bind("industry")} /></Field>
        <Field label="地区"><input className="input" {...f.bind("region")} /></Field>
        <Field label="地址" full><input className="input" {...f.bind("address")} /></Field>
        <Field label="备注" full><textarea className="input textarea" {...f.bind("remark")} /></Field>
      </div>
    </Modal>
  );
}

function ContactForm({ account, onClose, onSaved }: { account: any; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const f = useForm({ name: "", department: "", position: "", mobile: "", phone: "", email: "" });
  const save = async () => {
    try {
      await post("/contacts/", { ...f.v, account: account.id });
      notify("联系人已添加");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal title={`添加联系人 · ${account.name}`} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.name} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="姓名 *"><input className="input" {...f.bind("name")} autoFocus /></Field>
        <Field label="部门"><input className="input" {...f.bind("department")} /></Field>
        <Field label="职务"><input className="input" {...f.bind("position")} /></Field>
        <Field label="手机"><input className="input" {...f.bind("mobile")} /></Field>
        <Field label="座机"><input className="input" {...f.bind("phone")} /></Field>
        <Field label="邮箱"><input className="input" {...f.bind("email")} /></Field>
      </div>
    </Modal>
  );
}

export default function Customers() {
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [category, setCategory] = useState("");
  const [cats, reloadCats] = useLoad(() => get<any[]>("/account-categories/"), []);
  const [list, reload, loading, err] = useLoad(() => get<any[]>("/accounts/" + qs({ q, kind, category })), [q, kind, category]);
  const [pf, setPf] = useState({ q: "", role: "", presale_phase: "", execution_phase: "" });
  const [sel, setSel] = useState<number | null>(null);
  const [modal, setModal] = useState("");
  const cur = (list || []).find((a) => a.id === sel) || (list || [])[0];
  const [projects] = useLoad(() => (cur ? get<any[]>(`/accounts/${cur.id}/projects/` + qs(pf)) : Promise.resolve([])), [cur?.id, pf.q, pf.role, pf.presale_phase, pf.execution_phase]);
  const addCat = async () => {
    const name = prompt("新的客户类别名称（如：重点主机厂、港口集团）");
    if (!name) return;
    try {
      await post("/account-categories/", { name });
      reloadCats();
    } catch (e) {
      fail(e);
    }
  };
  const remove = async () => {
    if (!cur || !confirm(`删除客户「${cur.name}」？被项目或报价引用的客户不能删除。`)) return;
    try {
      await del(`/accounts/${cur.id}/`);
      notify("已删除");
      setSel(null);
      reload();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <>
      <PageHead k="CUSTOMER DIRECTORY" title="客户资料库" sub="统一组织库：一个组织可同时是主机厂、投资方等多种身份；多部门、多联系人">
        {can("account.write") && <button className="btn primary" onClick={() => setModal("new")}>+ 新建客户</button>}
      </PageHead>
      <div className="toolbar">
        <input className="input" placeholder="搜索名称、联系人、地区" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">全部身份</option>{PARTY_ROLES.map((k) => <option key={k}>{k}</option>)}</select>
        <select className="select" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">全部类别</option>{(cats || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        {can("account.category") && <button className="btn mini soft" onClick={addCat}>+ 客户类别</button>}
      </div>
      <div className="split">
        <div className="card">
          {loading && !list ? <Loading err={err} /> : (
            <div className="tablewrap">
              <table className="data">
                <thead><tr><th>组织名称</th><th>身份</th><th>地区</th><th>联系人</th><th>关联项目</th></tr></thead>
                <tbody>
                  {(list || []).map((a) => (
                    <tr key={a.id} onClick={() => setSel(a.id)} style={{ cursor: "pointer", background: cur?.id === a.id ? "#f2faf8" : undefined }}>
                      <td><span className="link">{a.name}</span></td>
                      <td>{a.kinds.map((k: string) => <span key={k} className="chip blue" style={{ marginRight: 4 }}>{k}</span>)}{(a.category_names || []).map((k: string) => <span key={k} className="chip" style={{ marginRight: 4 }}>{k}</span>)}</td>
                      <td>{a.region || "—"}</td><td>{a.contacts.length}</td><td>{a.project_count}</td>
                    </tr>
                  ))}
                  {list && list.length === 0 && <tr><td colSpan={5} className="empty-cell">没有符合条件的客户</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {cur && (
          <div className="card cardpad" style={{ alignSelf: "start" }}>
            <div className="sectionhead">
              <h2>{cur.name}</h2>
              <div className="rowactions">
                {can("account.write") && <button className="btn mini" onClick={() => setModal("edit")}>编辑</button>}
                {can("account.delete") && <button className="btn mini warn" onClick={remove}>删除</button>}
              </div>
            </div>
            <div className="kv" style={{ marginBottom: 14 }}>
              <span>行业</span><span>{cur.industry || "—"}</span><span>地区</span><span>{cur.region || "—"}</span><span>地址</span><span>{cur.address || "—"}</span>
            </div>
            <div className="sectionhead"><h2 style={{ fontSize: 14 }}>联系人</h2>{can("account.write") && <button className="btn mini soft" onClick={() => setModal("contact")}>+ 联系人</button>}</div>
            {cur.contacts.map((c: any) => (
              <div className="designline" key={c.id}>
                <div className="n">{c.name.slice(0, 1)}</div>
                <div><b style={{ fontSize: 12 }}>{c.name}</b> <span className="sub">{[c.department, c.position].filter(Boolean).join(" · ")}</span><div className="micro">{[c.mobile, c.phone, c.email].filter(Boolean).join(" / ")}</div></div>
              </div>
            ))}
            {cur.contacts.length === 0 && <div className="sub">暂无联系人</div>}
            <div className="sectionhead" style={{ marginTop: 16 }}><h2 style={{ fontSize: 14 }}>关联项目（{(projects || []).length}）</h2></div>
            <div className="toolbar" style={{ marginBottom: 6 }}>
              <input className="input" placeholder="项目名称/编号" value={pf.q} onChange={(e) => setPf({ ...pf, q: e.target.value })} style={{ width: 130 }} />
              <select className="select" value={pf.role} onChange={(e) => setPf({ ...pf, role: e.target.value })}><option value="">全部身份</option>{PARTY_ROLES.map((k) => <option key={k}>{k}</option>)}</select>
              <select className="select" value={pf.presale_phase} onChange={(e) => setPf({ ...pf, presale_phase: e.target.value })}><option value="">售前阶段</option>{PRESALE_PHASES.map((k) => <option key={k}>{k}</option>)}</select>
              <select className="select" value={pf.execution_phase} onChange={(e) => setPf({ ...pf, execution_phase: e.target.value })}><option value="">执行阶段</option>{EXECUTION_PHASES.map((k) => <option key={k}>{k}</option>)}</select>
            </div>
            {(projects || []).map((p) => (
              <div className="designline" key={p.id}>
                <span className="chip blue">{p.roles.join("、")}</span>
                <div><Link to={`/projects/${p.id}`}><b style={{ fontSize: 12 }}>{p.name}</b></Link><div className="micro">{p.code} · {p.presale_phase} · {p.execution_phase}{p.sales_owner_name && ` · ${p.sales_owner_name}`}</div></div>
              </div>
            ))}
            {projects && projects.length === 0 && <div className="sub">暂无（或不在您的可见范围内）</div>}
          </div>
        )}
      </div>
      {modal === "new" && <AccountForm onClose={() => setModal("")} onSaved={() => (setModal(""), reload())} />}
      {modal === "edit" && cur && <AccountForm initial={cur} onClose={() => setModal("")} onSaved={() => (setModal(""), reload())} />}
      {modal === "contact" && cur && <ContactForm account={cur} onClose={() => setModal("")} onSaved={() => (setModal(""), reload())} />}
    </>
  );
}
