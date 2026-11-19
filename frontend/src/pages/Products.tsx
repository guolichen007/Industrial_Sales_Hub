import { useState } from "react";
import { get, money, patch, post, qs } from "../api";
import { Chip, Field, Loading, Modal, PageHead, useForm, useLoad, useToast } from "../ui";
import { ImportModal } from "./Settings";

function ProductForm({ initial, cats, onClose, onSaved }: { initial?: any; cats: any[]; onClose: () => void; onSaved: () => void }) {
  const { notify, fail } = useToast();
  const f = useForm({
    sku: initial?.sku || "", model: initial?.model || "", name: initial?.name || "", category: initial?.category || cats[0]?.id || "",
    brand: initial?.brand || "", drawing_no: initial?.drawing_no || "", unit: initial?.unit || "台", tax_rate: initial?.tax_rate || "0.13",
    base_price: initial?.base_price || "0", technical_desc: initial?.technical_desc || "",
  });
  const [params, setParams] = useState<any[]>(initial?.params || []);
  const applyDict = () => {
    const c = cats.find((x) => x.id === Number(f.v.category));
    if (!c) return;
    const have = new Set(params.map((p) => p.key));
    setParams([...params, ...c.parameters.filter((d: any) => !have.has(d.key)).map((d: any) => ({ key: d.key, label: d.label, value: "", unit: d.unit }))]);
  };
  const save = async () => {
    try {
      const body = { ...f.v, params: params.filter((p) => p.label) };
      if (initial) await patch(`/products/${initial.id}/`, body);
      else await post("/products/", body);
      notify("产品已保存");
      onSaved();
    } catch (e) {
      f.catchErr(e);
      fail(e);
    }
  };
  return (
    <Modal wide title={initial ? `编辑产品 ${initial.sku}` : "新增产品型号"} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!f.v.sku || !f.v.name} onClick={save}>保存</button></>}>
      <div className="fieldset">
        <Field label="型号编码（唯一）*" err={f.errs.sku}><input className="input" {...f.bind("sku")} /></Field>
        <Field label="型号 *" err={f.errs.model}><input className="input" {...f.bind("model")} /></Field>
        <Field label="名称 *" err={f.errs.name}><input className="input" {...f.bind("name")} /></Field>
        <Field label="类别"><select className="select" {...f.bind("category")}>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field label="品牌"><input className="input" {...f.bind("brand")} /></Field>
        <Field label="图号"><input className="input" {...f.bind("drawing_no")} /></Field>
        <Field label="单位"><input className="input" {...f.bind("unit")} /></Field>
        <Field label="税率" hint="0.13 表示 13%" err={f.errs.tax_rate}><input className="input" {...f.bind("tax_rate")} /></Field>
        <Field label="参考未税单价" err={f.errs.base_price}><input className="input" {...f.bind("base_price")} inputMode="decimal" /></Field>
        <Field label="技术说明" full><textarea className="input textarea" {...f.bind("technical_desc")} /></Field>
        <Field label="技术参数" full hint="报价时可逐项选择是否在报价单中显示">
          <div>
            {params.map((p, i) => (
              <div key={i} className="inline-form" style={{ marginBottom: 6 }}>
                <input className="input" style={{ width: 140 }} placeholder="参数名" value={p.label} onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                <input className="input" style={{ width: 160 }} placeholder="值" value={p.value} onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                <input className="input" style={{ width: 80 }} placeholder="单位" value={p.unit} onChange={(e) => setParams(params.map((x, j) => (j === i ? { ...x, unit: e.target.value } : x)))} />
                <button className="btn mini" onClick={() => setParams(params.filter((_, j) => j !== i))}>删除</button>
              </div>
            ))}
            <div className="rowactions">
              <button className="btn mini soft" onClick={() => setParams([...params, { key: `p${Date.now() % 100000}`, label: "", value: "", unit: "" }])}>+ 参数</button>
              <button className="btn mini" onClick={applyDict}>按类别参数字典补齐</button>
            </div>
          </div>
        </Field>
      </div>
    </Modal>
  );
}

export default function Products() {
  const { notify, fail } = useToast();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [active, setActive] = useState("1");
  const [cats, reloadCats] = useLoad(() => get<any[]>("/product-categories/"), []);
  const [list, reload, loading, err] = useLoad(() => get<any[]>("/products/" + qs({ q, category: cat, active })), [q, cat, active]);
  const [modal, setModal] = useState<any>(null);
  const toggle = async (p: any) => {
    try {
      await patch(`/products/${p.id}/`, { active: !p.active });
      notify(p.active ? "已停用（历史报价不受影响）" : "已启用");
      reload();
    } catch (e) {
      fail(e);
    }
  };
  const addCat = async () => {
    const name = prompt("新类别名称");
    if (!name) return;
    try {
      await post("/product-categories/", { name });
      reloadCats();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <>
      <PageHead k="PRODUCT CATALOG" title="产品型号库" sub="统一产品库：商务助理与负责人维护；停用代替删除，历史报价不受影响">
        <button className="btn" onClick={addCat}>+ 类别</button>
        <button className="btn" onClick={() => setModal("import")}>Excel 导入</button>
        <button className="btn primary" disabled={!cats?.length} onClick={() => setModal("new")}>+ 新增产品</button>
      </PageHead>
      <div className="toolbar">
        <input className="input" placeholder="搜索编码、型号、名称、品牌" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">全部类别</option>{(cats || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        <select className="select" value={active} onChange={(e) => setActive(e.target.value)}><option value="1">启用中</option><option value="0">已停用</option><option value="">全部</option></select>
      </div>
      <div className="card">
        {loading && !list ? <Loading err={err} /> : (
          <div className="tablewrap">
            <table className="data">
              <thead><tr><th>编码 / 型号</th><th>名称</th><th>类别</th><th>品牌</th><th>单位</th><th className="right">参考未税单价</th><th>税率</th><th>参数</th><th>状态</th><th /></tr></thead>
              <tbody>
                {(list || []).map((p) => (
                  <tr key={p.id}>
                    <td><b>{p.sku}</b><div className="micro">{p.model}</div></td>
                    <td>{p.name}</td><td>{p.category_name}</td><td>{p.brand}</td><td>{p.unit}</td>
                    <td className="right num">{money(p.base_price)}</td><td>{(Number(p.tax_rate) * 100).toFixed(0)}%</td>
                    <td className="micro" style={{ whiteSpace: "normal", maxWidth: 260 }}>{p.params.map((x: any) => `${x.label} ${x.value}${x.unit}`).join("；")}</td>
                    <td><Chip s={p.active ? "启用" : "停用"} cls={p.active ? "" : "gray"} /></td>
                    <td className="rowactions"><button className="btn mini" onClick={() => setModal(p)}>编辑</button><button className="btn mini" onClick={() => toggle(p)}>{p.active ? "停用" : "启用"}</button></td>
                  </tr>
                ))}
                {list && list.length === 0 && <tr><td colSpan={10} className="empty-cell">暂无产品</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {modal === "import" && <ImportModal kind="products" onClose={() => setModal(null)} onDone={reload} />}
      {modal && modal !== "import" && cats && <ProductForm initial={modal === "new" ? undefined : modal} cats={cats} onClose={() => setModal(null)} onSaved={() => (setModal(null), reload())} />}
    </>
  );
}
