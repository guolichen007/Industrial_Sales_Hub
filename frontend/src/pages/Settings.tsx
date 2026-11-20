import { useRef, useState } from "react";
import { api, dt, get, patch, post } from "../api";
import { useAuth } from "../auth";
import { ROLE_LABEL } from "../options";
import { Chip, Field, Modal, PageHead, useForm, useLoad, useToast } from "../ui";

export function ImportModal({ kind, onClose, onDone }: { kind: "products" | "accounts" | "performances"; onClose: () => void; onDone: () => void }) {
  const { notify, fail } = useToast();
  const ref = useRef<HTMLInputElement>(null);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const label = { products: "产品", accounts: "客户", performances: "历史业绩" }[kind];
  const preview = async (f: File) => {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", f);
    setBusy(true);
    try {
      setRes(await api("/import-batches/preview/", { form: fd }));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const commit = async () => {
    setBusy(true);
    try {
      const r = await post(`/import-batches/${res.id}/commit/`);
      notify(`导入完成：新增 ${r.result.created}，跳过 ${r.result.skipped}`);
      onDone();
      onClose();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={`Excel 导入 · ${label}`} onClose={onClose}
      foot={<><button className="btn" onClick={onClose}>关闭</button>{res && <button className="btn primary" disabled={busy || !res.summary.new} onClick={commit}>确认导入 {res.summary.new} 条新数据</button>}</>}>
      <div className="notice" style={{ marginBottom: 12 }}>
        步骤：① 下载标准模板填写 → ② 上传预检（不写入数据库，逐行提示错误与重复）→ ③ 确认导入。仅接受 .xlsx，不执行宏或公式。{kind === "performances" && "导入的业绩为“待核实”状态，需负责人确认。"}
      </div>
      <div className="actions">
        <a className="btn" href={`/api/import-batches/template/${kind}/`}>下载模板</a>
        <input ref={ref} type="file" accept=".xlsx" hidden onChange={(e) => e.target.files?.[0] && preview(e.target.files[0])} />
        <button className="btn soft" disabled={busy} onClick={() => ref.current?.click()}>{busy ? "处理中…" : "选择文件并预检"}</button>
      </div>
      {res && (
        <>
          <div className="mini-cells">
            <div className="mini"><small>可新增</small><strong>{res.summary.new}</strong></div>
            <div className="mini"><small>已存在（跳过）</small><strong>{res.summary.duplicate}</strong></div>
            <div className="mini"><small>错误</small><strong style={{ color: res.summary.error ? "#b4453f" : undefined }}>{res.summary.error}</strong></div>
          </div>
          <div className="tablewrap" style={{ maxHeight: "40vh" }}>
            <table className="data">
              <thead><tr><th>行号</th><th>结果</th><th>{res.template[0]}</th><th>{res.template[1]}</th><th>问题</th></tr></thead>
              <tbody>
                {res.rows.filter((r: any) => r.status !== "new").concat(res.rows.filter((r: any) => r.status === "new").slice(0, 20)).map((r: any) => (
                  <tr key={r.row}><td>{r.row}</td><td><Chip s={{ new: "新增", duplicate: "重复", error: "错误" }[r.status as string]} cls={{ new: "", duplicate: "gray", error: "red" }[r.status as string]} /></td>
                    <td>{r.values[0]}</td><td>{r.values[1]}</td><td className="fielderr">{r.errors.join("；")}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}

function UserForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const f = useForm({ username: initial?.username || "", display_name: initial?.display_name || "", phone: initial?.phone || "", email: initial?.email || "", role: initial?.role || "SALES", password: "" });
  const save = async () => {
    try {
      const body: any = { ...f.v };
      if (!body.password) delete body.password;
      if (initial) await patch(`/users/${initial.id}/`, body);
      else await post("/users/", body);
      notify("账号已保存");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal title={initial ? `编辑账号 ${initial.username}` : "新建账号"} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="用户名 *" err={f.errs.username}><input className="input" {...f.bind("username")} disabled={!!initial} /></Field>
        <Field label="姓名"><input className="input" {...f.bind("display_name")} /></Field>
        <Field label="角色"><select className="select" {...f.bind("role")}>{Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="电话"><input className="input" {...f.bind("phone")} /></Field>
        <Field label={initial ? "重置密码（留空不改）" : "初始密码 *"} err={f.errs.password} hint="至少 8 位，不能为纯数字或常见密码"><input className="input" type="password" {...f.bind("password")} autoComplete="new-password" /></Field>
        <Field label="邮箱"><input className="input" {...f.bind("email")} /></Field>
      </div>
    </Modal>
  );
}

function EntityForm({ e, onClose, onSaved }: { e: any; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const { can } = useAuth();
  const keys = ["name", "short_name", "name_en", "address", "address_en", "phone", "fax", "email", "website", "tax_no", "bank_name", "bank_account", "currency_label", "quote_title", "intro_text", "footer_note", "default_terms"] as const;
  const f = useForm(Object.fromEntries(keys.map((k) => [k, e[k] || ""])) as Record<(typeof keys)[number], string>);
  const [basis, setBasis] = useState(e.price_basis);
  const [taxCols, setTaxCols] = useState(!!e.show_tax_columns);
  const [tpl, setTpl] = useState(e.template_key);
  const save = async () => {
    try {
      await patch(`/legal-entities/${e.id}/`, { ...f.v, price_basis: basis, show_tax_columns: taxCols, template_key: tpl });
      notify("已保存。新生成的报价使用新信息，历史报价不变。如修改了全称/英文名/标题/模板/价税口径/Logo，原签收自动失效，需负责人重新签收。");
      onSaved();
    } catch (x) {
      fail(x);
    }
  };
  const uploadLogo = async (file: File | undefined) => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      await api(`/legal-entities/${e.id}/logo/`, { form: fd });
      notify("Logo 已更新（签收需重新确认）");
      onSaved();
    } catch (x) {
      fail(x);
    }
  };
  const L: Record<string, string> = { name: "公司全称（中文）", short_name: "简称", name_en: "英文全称", address: "地址（中文，可多行）", address_en: "英文地址（可多行）", phone: "电话", fax: "传真", email: "邮箱", website: "网址",
    tax_no: "税号", bank_name: "开户行", bank_account: "账号", currency_label: "币种说明", quote_title: "报价单标题", intro_text: "开头语", footer_note: "页脚说明", default_terms: "默认商务条款（每行一条）" };
  const multi = new Set(["default_terms", "address", "address_en"]);
  return (
    <Modal wide title={`出具主体 · ${e.short_name}`} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="报价模板版式" full hint="三套固定版式依据三份真实样张设计；更换版式会使签收失效">
          <select className="select" value={tpl} onChange={(x) => setTpl(x.target.value)}>
            <option value="A">A · 双语规格表（型号/描述/米数，铜价参考，英文条款）</option>
            <option value="B">B · 中英抬头 + 参数子行（如制动力矩）+ 大写</option>
            <option value="C">C · 双语抬头 + 部位/点数 + 多台总计</option>
          </select>
        </Field>
        {keys.map((k) => <Field key={k} label={L[k]} full={multi.has(k) || k === "name" || k === "name_en"}>{multi.has(k) ? <textarea className="input textarea" style={{ minHeight: k === "default_terms" ? 120 : 60 }} {...f.bind(k)} /> : <input className="input" {...f.bind(k)} />}</Field>)}
        <Field label="单价口径" hint="须按真实样张与财务确认；修改只影响之后新建/未冻结的报价，并使签收失效">
          <select className="select" value={basis} onChange={(x) => setBasis(x.target.value)}><option value="NET">未税单价，逐行计税</option><option value="GROSS">含税单价，倒算税额</option></select>
        </Field>
        <Field label="合计区"><label className="chk"><input type="checkbox" checked={taxCols} onChange={(x) => setTaxCols(x.target.checked)} />含税口径下也列出未税合计与税额</label></Field>
        {can("legal_entity.write") && (
          <Field label="Logo（PNG/JPG，≤1 MB）" full hint="上传后重新编码为 PNG 并去除元数据；仅打印在报价单上">
            <div className="rowactions">{e.logo_key && <img src={`/api/legal-entities/${e.id}/logo/image/`} alt="logo" style={{ maxHeight: 40 }} />}<input type="file" accept="image/png,image/jpeg" onChange={(x) => uploadLogo(x.target.files?.[0])} /></div>
          </Field>
        )}
      </div>
    </Modal>
  );
}

function SignoffPanel({ e, onChanged }: { e: any; onChanged: () => void }) {
  const { notify, fail } = useToast();
  const { can } = useAuth();
  const [note, setNote] = useState("");
  const [hist, setHist] = useState<any[] | null>(null);
  const sign = async () => {
    try {
      await post(`/legal-entities/${e.id}/signoff/`, { note });
      notify("已签收：该主体可生成正式报价");
      setNote("");
      onChanged();
    } catch (x) {
      fail(x);
    }
  };
  const revoke = async () => {
    const reason = prompt("撤销签收的原因（必填）");
    if (!reason) return;
    try {
      await post(`/legal-entities/${e.id}/signoff/revoke/`, { reason });
      notify("已撤销签收：之后只能生成试用报价");
      onChanged();
    } catch (x) {
      fail(x);
    }
  };
  const so = e.signoff;
  return (
    <div style={{ marginTop: 8 }}>
      {so ? <div className="micro">✔ 已签收：{so.template_key}/{so.template_version} · {so.signed_by} · {dt(so.signed_at, true)}<br />依据：{so.note}</div>
        : <div className="micro" style={{ color: "#a96336" }}>模板 {e.template_key}/{e.template_version} 未签收：只能生成带“试用”水印的试用报价（不能标记已发送、不能作为合同依据）。</div>}
      {can("template.signoff") && !so && (
        <div className="inline-form" style={{ marginTop: 6 }}>
          <input className="input" placeholder="签收依据：核对了哪些真实样张、财务黄金用例结果" value={note} onChange={(x) => setNote(x.target.value)} style={{ minWidth: 260 }} />
          <button className="btn mini primary" disabled={note.trim().length < 6} onClick={sign}>负责人签收</button>
        </div>
      )}
      {can("template.signoff") && so && <button className="btn mini" style={{ marginTop: 6 }} onClick={revoke}>撤销签收</button>}
      {can("legal_entity.write") && <button className="btn mini" style={{ marginTop: 6, marginLeft: 6 }} onClick={() => get<any[]>(`/legal-entities/${e.id}/signoffs/`).then(setHist).catch(fail)}>签收记录</button>}
      {hist && <ul className="micro">{hist.length === 0 && <li>暂无</li>}{hist.map((h) => <li key={h.id}>{h.template_key}/{h.template_version} · {h.signed_by} · {dt(h.signed_at, true)}{h.revoked_at ? `（已撤销：${h.revoke_reason}）` : ""}</li>)}</ul>}
    </div>
  );
}

function ChangePassword() {
  const { notify, fail } = useToast();
  const f = useForm({ old_password: "", new_password: "" });
  const save = async () => {
    try {
      await post("/auth/password/", f.v);
      notify("密码已修改");
      f.setV({ old_password: "", new_password: "" });
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <div className="inline-form">
      <Field label="原密码"><input className="input" type="password" {...f.bind("old_password")} autoComplete="current-password" /></Field>
      <Field label="新密码" err={f.errs.new_password}><input className="input" type="password" {...f.bind("new_password")} autoComplete="new-password" /></Field>
      <button className="btn primary" disabled={!f.v.old_password || !f.v.new_password} onClick={save}>修改密码</button>
    </div>
  );
}

export default function Settings() {
  const { me, can } = useAuth();
  const [users, reloadUsers] = useLoad(() => (can("user.manage") ? get<any[]>("/users/") : Promise.resolve([])), []);
  const [ents, reloadEnts] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [audit] = useLoad(() => (can("audit.read") ? get("/audit-events/") : Promise.resolve({ results: [] })), []);
  const [last, setLast] = useState("");
  const [modal, setModal] = useState<any>(null);
  return (
    <>
      <PageHead k="SETTINGS" title="系统设置" sub="账号、报价出具主体、数据导入、操作审计" />
      <div className="grid">
        <div className="card cardpad">
          <div className="sectionhead"><h2>我的账号</h2><small>{me!.name} · {me!.role_label}</small></div>
          <ChangePassword />
        </div>
        {can("user.manage") && (
          <div className="card cardpad">
            <div className="sectionhead"><h2>账号与角色</h2><button className="btn mini primary" onClick={() => setModal({ t: "user" })}>+ 新建账号</button></div>
            <div className="notice" style={{ marginBottom: 10 }}>管理员只负责账号与系统配置，不能查看报价、金额与业绩。请为部门负责人、商务助理、销售、财务分别创建账号；停用、改角色、重置密码后对方需重新登录。</div>
            {last && <div className="err">{last}</div>}
            <div className="tablewrap">
            <table className="data">
              <thead><tr><th>用户名</th><th>姓名</th><th>角色</th><th>状态</th><th>最近登录</th><th /></tr></thead>
              <tbody>{(users || []).map((u) => (
                <tr key={u.id}><td>{u.username}</td><td>{u.display_name}</td><td>{u.role_label}</td><td><Chip s={u.is_active ? "启用" : "停用"} cls={u.is_active ? "" : "red"} /></td><td>{dt(u.last_login, true)}</td>
                  <td className="rowactions"><button className="btn mini" onClick={() => setModal({ t: "user", u })}>编辑</button>
                    {u.id !== me!.id && <button className="btn mini" onClick={() => patch(`/users/${u.id}/`, { is_active: !u.is_active }).then(reloadUsers).catch((e) => setLast(e.message))}>{u.is_active ? "停用" : "启用"}</button>}</td></tr>
              ))}</tbody>
            </table>
            </div>
          </div>
        )}
        <div className="card cardpad">
          <div className="sectionhead"><h2>报价出具主体（三公司固定模板）</h2><small>数据共享，仅模板与抬头区分</small></div>
          <div className="grid cols3">
            {(ents || []).map((e) => (
              <div className="choice" key={e.id}>
                <h3>{e.short_name} · 模板 {e.template_key}</h3>
                <small>{e.name}<br />{e.address}<br />{e.phone}<br />口径：{e.price_basis === "GROSS" ? "含税单价" : "未税单价"} · {e.signoff ? "模板已签收" : <b style={{ color: "#a96336" }}>模板未签收</b>}</small>
                {can("legal_entity.write") && <div style={{ marginTop: 8 }}><button className="btn mini" onClick={() => setModal({ t: "ent", e })}>编辑信息与条款</button></div>}
                {e.template_version !== undefined && <SignoffPanel e={e} onChanged={reloadEnts} />}
              </div>
            ))}
          </div>
        </div>
        {can("import.run") && (
          <div className="card cardpad">
            <div className="sectionhead"><h2>数据导入</h2><small>先预检，再提交；重复导入不会产生重复数据</small></div>
            <div className="actions">
              <button className="btn" onClick={() => setModal({ t: "imp", k: "products" })}>导入产品</button>
              <button className="btn" onClick={() => setModal({ t: "imp", k: "accounts" })}>导入客户</button>
              <button className="btn" onClick={() => setModal({ t: "imp", k: "performances" })}>导入历史业绩</button>
            </div>
          </div>
        )}
        {can("audit.read") && (
          <div className="card cardpad">
            <div className="sectionhead"><h2>操作审计（最近 50 条）</h2></div>
            <div className="tablewrap">
              <table className="data">
                <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>摘要</th></tr></thead>
                <tbody>{(audit?.results || []).map((a: any) => <tr key={a.id}><td>{dt(a.at, true)}</td><td>{a.actor_name || "—"}</td><td>{a.action}</td><td>{a.resource}#{a.resource_id}</td><td style={{ whiteSpace: "normal" }}>{a.summary}</td></tr>)}</tbody>
              </table>
            </div>
          </div>
        )}
      </div>
      {modal?.t === "user" && <UserForm initial={modal.u} onClose={() => setModal(null)} onSaved={() => (setModal(null), reloadUsers())} />}
      {modal?.t === "ent" && <EntityForm e={modal.e} onClose={() => setModal(null)} onSaved={() => (setModal(null), reloadEnts())} />}
      {modal?.t === "imp" && <ImportModal kind={modal.k} onClose={() => setModal(null)} onDone={() => {}} />}
    </>
  );
}
