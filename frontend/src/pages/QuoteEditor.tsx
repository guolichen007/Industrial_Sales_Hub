import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, del, dt, get, money, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { QUOTE_NEXT, QUOTE_STATUS } from "../options";
import { Chip, Field, Loading, Modal, PageHead, useLoad, useToast } from "../ui";

function LineEditor({ line, rev, editable, onChanged, onSaveProduct }: { line: any; rev: any; editable: boolean; onChanged: (rev: any) => void; onSaveProduct: (line: any) => void }) {
  const { fail } = useToast();
  const { can } = useAuth();
  const revId = rev.id;
  const pick = (l: any) => ({ qty: l.qty, multiplier: l.multiplier, unit_price: l.unit_price, tax_rate: l.tax_rate, discount_rate: l.discount_rate, remark: l.remark, unit: l.unit });
  const [v, setV] = useState(pick(line));
  useEffect(() => setV(pick(line)), [line]);
  const save = async (body: any) => {
    try {
      onChanged(await patch(`/quote-revisions/${revId}/lines/${line.id}/`, { ...body, expected_version: rev.lock_version }));
    } catch (e) {
      fail(e);
      if (e instanceof ApiError && e.code === "stale_version") onChanged(await get(`/quote-revisions/${revId}/`));
    }
  };
  const commit = (k: keyof typeof v) => {
    if (String(v[k]) !== String(line[k])) save({ [k]: v[k] });
  };
  const toggleParam = (key: string) => {
    const cur: string[] = line.visible_param_keys || [];
    save({ visible_param_keys: cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key] });
  };
  const remove = async () => {
    if (!confirm(`删除报价行「${line.name}」？`)) return;
    try {
      onChanged(await del(`/quote-revisions/${revId}/lines/${line.id}/`));
    } catch (e) {
      fail(e);
    }
  };
  const inp = (k: keyof typeof v, label: string, hint?: string) => (
    <div>
      <label>{label}{hint && <span className="micro"> {hint}</span>}</label>
      <input className="input" value={v[k]} disabled={!editable} onChange={(e) => setV({ ...v, [k]: e.target.value })} onBlur={() => commit(k)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
    </div>
  );
  return (
    <div className="linebox">
      <div className="sectionhead" style={{ paddingBottom: 4 }}>
        <div><b>{line.name}</b> <span className="sub">{line.model}{line.brand && ` · ${line.brand}`}</span></div>
        <div className="rowactions">
          <span className="num" style={{ fontWeight: 700 }}>{money(line.line_gross)}</span>
          {!line.product && can("product.write") && <button className="btn mini" onClick={() => onSaveProduct(line)} title="把这条手工新增的产品存入产品库，不影响本报价">存入产品库</button>}
          {editable && <button className="btn mini" onClick={remove}>删除</button>}
        </div>
      </div>
      {line.params?.length > 0 && (
        <div className="quote-model-params">
          <span className="micro">在报价单中显示：</span>
          {line.params.map((p: any) => (
            <label key={p.key}><input type="checkbox" disabled={!editable} checked={(line.visible_param_keys || []).includes(p.key)} onChange={() => toggleParam(p.key)} /> {p.label} {p.value}{p.unit}</label>
          ))}
        </div>
      )}
      <div className="lineedit" style={{ gridTemplateColumns: "repeat(6,minmax(0,1fr))" }}>
        {inp("qty", "数量")}
        {inp("multiplier", "乘数", "如每盘米数")}
        {inp("unit", "单位")}
        {inp("unit_price", rev.price_basis === "GROSS" ? "含税单价" : "未税单价")}
        {inp("tax_rate", "税率", "0.13=13%")}
        {inp("discount_rate", "折扣率", "0.05=5%")}
      </div>
      <div className="lineedit" style={{ gridTemplateColumns: "3fr 1fr 1fr" }}>
        {inp("remark", "行备注")}
        <div><label>未税金额</label><div className="num" style={{ padding: "10px 0" }}>{money(line.line_net)}</div></div>
        <div><label>税额</label><div className="num" style={{ padding: "10px 0" }}>{money(line.line_tax)}</div></div>
      </div>
    </div>
  );
}

function ProductPicker({ onPick, onClose }: { onPick: (id: number) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [list] = useLoad(() => get<any[]>("/products/" + qs({ q, active: 1 })), [q]);
  return (
    <Modal wide title="选择产品型号" onClose={onClose}>
      <input className="input" style={{ width: "100%", marginBottom: 12 }} placeholder="搜索编码、型号、名称、品牌" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
      <div className="tablewrap" style={{ maxHeight: "55vh" }}>
        <table className="data">
          <thead><tr><th>编码 / 型号</th><th>名称</th><th>类别</th><th className="right">参考单价</th><th>参数</th><th /></tr></thead>
          <tbody>
            {(list || []).map((p) => (
              <tr key={p.id}>
                <td><b>{p.sku}</b><div className="micro">{p.model}</div></td><td>{p.name}</td><td>{p.category_name}</td><td className="right num">{money(p.base_price)}</td>
                <td className="micro" style={{ whiteSpace: "normal", maxWidth: 260 }}>{p.params.map((x: any) => `${x.label} ${x.value}${x.unit}`).join("；")}</td>
                <td><button className="btn mini primary" onClick={() => onPick(p.id)}>加入</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

export default function QuoteEditor() {
  const { id } = useParams();
  const { notify, fail } = useToast();
  const [quote, reloadQuote, loading, err] = useLoad(() => get(`/quotations/${id}/`), [id]);
  const [revId, setRevId] = useState<number | null>(null);
  const [rev, setRev] = useState<any>(null);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState("");
  const [unsigned, setUnsigned] = useState(false);
  useEffect(() => {
    if (quote && !revId) setRevId(quote.revisions[quote.revisions.length - 1]?.id);
  }, [quote, revId]);
  useEffect(() => {
    if (revId) get(`/quote-revisions/${revId}/`).then(setRev).catch(fail);
  }, [revId, fail]);
  if ((loading && !quote) || !quote) return <Loading err={err} />;
  if (!rev) return <Loading />;
  const editable = rev.status === "DRAFT";
  const saveRev = async (body: any) => {
    try {
      setRev(await patch(`/quote-revisions/${rev.id}/`, { ...body, expected_version: rev.lock_version }));
    } catch (e) {
      fail(e);
      if (e instanceof ApiError && e.code === "stale_version") setRev(await get(`/quote-revisions/${rev.id}/`));
    }
  };
  const saveAsProduct = async (line: any) => {
    const sku = prompt(`存入产品库：请输入唯一型号编码（建议与型号一致）`, line.model && line.model !== "—" ? line.model : "");
    if (!sku) return;
    const cats = await get<any[]>("/product-categories/");
    const name = prompt(`类别（可选：${cats.map((c) => c.name).join("、")}）`, cats[0]?.name || "");
    const cat = cats.find((c) => c.name === name);
    if (!cat) return fail(new Error("请输入已有的类别名称"));
    try {
      await post(`/quote-revisions/${rev.id}/save-product/`, { line: line.id, sku, category: cat.id });
      notify(`已存入产品库：${sku}（本报价不受影响）`);
    } catch (e) {
      fail(e);
    }
  };
  const addLine = async (pid: number) => {
    try {
      setRev(await post(`/quote-revisions/${rev.id}/lines/`, { product: pid, qty: "1", expected_version: rev.lock_version }));
      setPicking(false);
      notify("已加入产品，可调整数量与价格");
    } catch (e) {
      fail(e);
    }
  };
  const addCustom = async () => {
    const name = prompt("非标/临时项目名称（如：安装调试费）");
    if (!name) return;
    try {
      setRev(await post(`/quote-revisions/${rev.id}/lines/`, { name, model: "—", qty: "1", unit_price: "0", unit: "项", expected_version: rev.lock_version }));
    } catch (e) {
      fail(e);
    }
  };
  const generate = async (trial = false) => {
    const what = trial ? "试用 PDF（带“试用·模板未签收”水印，不能作为正式报价发送）" : "正式 PDF";
    if (!confirm(`生成 ${quote.code}-V${rev.version} 的${what}？生成后此版本冻结，不可再修改。`)) return;
    setBusy(true);
    try {
      setRev(await post(`/quote-revisions/${rev.id}/render/`, trial ? { trial: true } : {}));
      reloadQuote();
      notify(trial ? "试用 PDF 已生成（版本已冻结）。模板签收后请新建版本生成正式 PDF。" : "PDF 已生成，版本已冻结");
    } catch (e) {
      if (e instanceof ApiError && e.code === "template_unsigned") setUnsigned(true);
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  const newVersion = async () => {
    try {
      const r = await post(`/quotations/${quote.id}/revisions/`);
      reloadQuote();
      setRevId(r.id);
      notify(`已创建 V${r.version}（复制自上一版本，可编辑）`);
    } catch (e) {
      fail(e);
    }
  };
  const setStatus = async (s: string) => {
    try {
      await post(`/quotations/${quote.id}/status/`, { status: s });
      reloadQuote();
      notify(`报价状态已更新为「${QUOTE_STATUS[s]}」`);
    } catch (e) {
      fail(e);
    }
  };
  return (
    <>
      <PageHead k={`${quote.legal_entity_name} · 模板`} title={`${quote.code}`} sub={`项目：${quote.project_name} ｜ 接收客户：${quote.recipient_name}`}>
        <Link className="btn" to="/quotes">← 报价列表</Link>
        {QUOTE_NEXT[quote.status]?.map((s) => <button key={s} className="btn" onClick={() => setStatus(s)}>标记{QUOTE_STATUS[s]}</button>)}
      </PageHead>
      <div className="toolbar">
        <span className="sub">版本：</span>
        {quote.revisions.map((r: any) => (
          <button key={r.id} className={"btn mini " + (r.id === rev.id ? "dark" : "")} onClick={() => setRevId(r.id)}>V{r.version} {r.status === "GENERATED" ? (r.is_trial ? "试用🔒" : "🔒") : "草稿"}</button>
        ))}
        {!quote.revisions.some((r: any) => r.status === "DRAFT") && quote.status !== "CANCELLED" && <button className="btn mini soft" onClick={newVersion}>+ 新版本</button>}
        <Chip s={quote.status_label} />
        <span className="micro">单价口径：{rev.price_basis === "GROSS" ? "含税单价" : "未税单价"}（出具主体配置）。报价状态仅记录商务事实，不等于业绩。</span>
      </div>
      <div className="steps">
        <span className="step">① 主体与客户 ✓</span>
        <span className={"step " + (editable ? "current" : "")}>② 产品与参数</span>
        <span className={"step " + (editable ? "current" : "")}>③ 价格与条款</span>
        <span className={"step " + (!editable ? "current" : "")}>④ 预览与生成</span>
      </div>
      {!editable && (
        <div className="notice" style={{ marginBottom: 16 }}>
          此版本已于 {dt(rev.generated_at, true)} 由 {rev.generated_by_name} 生成并冻结（模板 {rev.template_key} · {rev.template_version}）。修改请创建新版本。
          {rev.is_trial && <div className="err" style={{ marginTop: 6 }}>这是试用报价：模板生成时尚未经负责人签收，PDF 带“试用”水印；不能标记为已发送/已接受，也不能作为合同依据。</div>}
          <div className="hash">快照 SHA-256：{rev.snapshot_sha256}<br />PDF SHA-256：{rev.pdf_sha256}</div>
        </div>
      )}
      <div className="split">
        <div>
          <div className="card cardpad">
            <div className="sectionhead">
              <h2>报价明细（{rev.lines.length} 行）</h2>
              {editable && <div className="rowactions"><button className="btn mini" onClick={addCustom}>+ 非标项</button><button className="btn mini primary" onClick={() => setPicking(true)}>+ 选择产品</button></div>}
            </div>
            {rev.lines.map((l: any) => <LineEditor key={l.id} line={l} rev={rev} editable={editable} onChanged={setRev} onSaveProduct={saveAsProduct} />)}
            {rev.lines.length === 0 && <div className="empty">尚未添加产品。点击“选择产品”从产品库加入；加入后价格与参数与产品库解耦。</div>}
            <div className="totals">
              <span>未税合计</span><b className="num">{money(rev.total_net)}</b>
              <span>税额合计</span><b className="num">{money(rev.total_tax)}</b>
              <span>含税总计{rev.host_qty > 1 ? `（×${rev.host_qty}${rev.host_label || "台"}）` : ""}</span><b className="num" style={{ fontSize: 17, color: "#0b7d74" }}>{money(rev.total_gross)}</b>
              <span>大写</span><b>{rev.total_upper}</b>
            </div>
          </div>
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="card cardpad" key={`terms-${rev.id}-${rev.status}`}>
            <div className="sectionhead"><h2>商务条款</h2></div>
            <div className="fieldset" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <Field label="报价日期"><input className="input" type="date" disabled={!editable} defaultValue={rev.quote_date} onBlur={(e) => e.target.value !== rev.quote_date && saveRev({ quote_date: e.target.value })} /></Field>
              <Field label="有效期（天）"><input className="input" disabled={!editable} defaultValue={rev.valid_days} onBlur={(e) => e.target.value !== String(rev.valid_days) && saveRev({ valid_days: e.target.value })} /></Field>
              <Field label="交货期" full><input className="input" disabled={!editable} defaultValue={rev.delivery_terms} onBlur={(e) => e.target.value !== rev.delivery_terms && saveRev({ delivery_terms: e.target.value })} placeholder="例如：合同生效后 45 天" /></Field>
              <Field label="质保期"><input className="input" disabled={!editable} defaultValue={rev.warranty} onBlur={(e) => e.target.value !== rev.warranty && saveRev({ warranty: e.target.value })} placeholder="例如：12个月" /></Field>
              <Field label="整单台数 × 说明" hint="明细为单台/单套时填写，如 2 台斗轮"><div className="rowactions"><input className="input" style={{ width: 70 }} disabled={!editable} defaultValue={rev.host_qty} onBlur={(e) => e.target.value !== String(rev.host_qty) && saveRev({ host_qty: e.target.value })} /><input className="input" disabled={!editable} defaultValue={rev.host_label} placeholder="台斗轮" onBlur={(e) => e.target.value !== rev.host_label && saveRev({ host_label: e.target.value })} /></div></Field>
              <Field label="付款方式" full><input className="input" disabled={!editable} defaultValue={rev.payment_terms} onBlur={(e) => e.target.value !== rev.payment_terms && saveRev({ payment_terms: e.target.value })} placeholder="例如：预付 30%，发货前付清" /></Field>
              <Field label="其他条款（每行一条）" full><textarea className="input textarea" style={{ minHeight: 110 }} disabled={!editable} defaultValue={rev.terms} onBlur={(e) => e.target.value !== rev.terms && saveRev({ terms: e.target.value })} /></Field>
              <Field label="备注" full><textarea className="input textarea" disabled={!editable} defaultValue={rev.remark} onBlur={(e) => e.target.value !== rev.remark && saveRev({ remark: e.target.value })} /></Field>
              <Field label="参考铜价（手工录入，仅印刷）" full><input className="input" disabled={!editable} defaultValue={rev.ref_copper_price} placeholder="例如：1# 电解铜 78,500 元/吨" onBlur={(e) => e.target.value !== rev.ref_copper_price && saveRev({ ref_copper_price: e.target.value })} /></Field>
              <Field label="参考汇率"><input className="input" disabled={!editable} defaultValue={rev.ref_exchange_rate} placeholder="例如：USD/CNY 7.12" onBlur={(e) => e.target.value !== rev.ref_exchange_rate && saveRev({ ref_exchange_rate: e.target.value })} /></Field>
              <Field label="参考值有效期至"><input className="input" type="date" disabled={!editable} defaultValue={rev.ref_valid_until || ""} onBlur={(e) => e.target.value !== (rev.ref_valid_until || "") && saveRev({ ref_valid_until: e.target.value || null })} /></Field>
              <label className="chk formfield full"><input type="checkbox" disabled={!editable} checked={rev.show_upper_amount} onChange={(e) => saveRev({ show_upper_amount: e.target.checked })} />报价单显示大写金额</label>
            </div>
          </div>
          <div className="card cardpad">
            <div className="sectionhead"><h2>预览与生成</h2></div>
            <div className="actions">
              <button className="btn" disabled={!rev.lines.length} onClick={() => setPreviewUrl(`/api/quote-revisions/${rev.id}/preview/?t=${Date.now()}`)}>预览 PDF</button>
              {editable && <button className="btn primary" disabled={busy || !rev.lines.length} onClick={() => generate(false)}>{busy ? "生成中…" : "生成正式 PDF 并冻结"}</button>}
              {editable && unsigned && <button className="btn" disabled={busy || !rev.lines.length} onClick={() => generate(true)}>生成试用 PDF（带水印）</button>}
              {!editable && <a className="btn primary" href={`/api/quote-revisions/${rev.id}/pdf/`}>下载 PDF</a>}
              {!editable && <button className="btn" onClick={() => setPreviewUrl(`/api/quote-revisions/${rev.id}/pdf/?inline=1`)}>查看存档</button>}
            </div>
            {rev.last_error && <div className="err">上次生成失败：{rev.last_error}</div>}
            {unsigned && editable && <div className="err">该出具主体的报价模板尚未经部门负责人签收（系统设置 → 出具主体）。现在只能生成带水印的试用 PDF。</div>}
            <div className="hint" style={{ marginTop: 10 }}>未经盖章的系统 PDF 仅供线下发送参考；审批与电子签章不在首期范围。</div>
          </div>
        </div>
      </div>
      {picking && <ProductPicker onPick={addLine} onClose={() => setPicking(false)} />}
      {previewUrl && (
        <Modal wide title="PDF 预览" onClose={() => setPreviewUrl("")}>
          <iframe className="pdfframe" src={previewUrl} title="PDF 预览" />
        </Modal>
      )}
    </>
  );
}
