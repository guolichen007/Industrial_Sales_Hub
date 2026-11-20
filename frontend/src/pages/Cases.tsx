import { useEffect, useState } from "react";
import { dt, get, post, qs } from "../api";
import { useAuth } from "../auth";
import { Field, Loading, PageHead, useBusy, useLoad, useToast } from "../ui";

export default function Cases() {
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const [q, setQ] = useState("");
  const [year, setYear] = useState("");
  const [fx, setFx] = useState({ industry: "", machine_type: "", region: "", product: "" });
  const [facets] = useLoad(() => get<any>("/public-cases/?facets=1"), []);
  const [cands, , loading, err, status] = useLoad(() => get<any[]>("/public-cases/" + qs({ q, year, ...fx })), [q, year, fx.industry, fx.machine_type, fx.region, fx.product]);
  const [ents] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [releases, reloadRel] = useLoad(() => get<any[]>("/case-releases/"), []);
  const [sel, setSel] = useState<number[]>([]);
  const [preview, setPreview] = useState<any[]>([]);
  const [title, setTitle] = useState("项目业绩案例");
  const [subtitle, setSubtitle] = useState("");
  const [entity, setEntity] = useState<number | "">("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, run] = useBusy();
  useEffect(() => {
    setConfirmed(false);
    if (!sel.length) return setPreview([]);
    post("/case-preview/", { performance_ids: sel }).then((r) => setPreview(r.cases)).catch(fail);
  }, [sel, fail]);
  const publish = () =>
    run(async () => {
      try {
        const r = await post("/case-releases/", { performance_ids: sel, title, subtitle, legal_entity: entity || null });
        notify("案例册 PDF 已生成");
        reloadRel();
        window.open(`/api/case-releases/${r.id}/pdf/?inline=1`, "_blank");
      } catch (e) {
        fail(e);
      }
    });
  const withdraw = async (r: any) => {
    const reason = prompt("撤回原因（撤回后系统内不可再下载；已经发出的文件无法追回）");
    if (!reason) return;
    try {
      await post(`/case-releases/${r.id}/withdraw/`, { reason });
      notify("已撤回");
      reloadRel();
    } catch (e) {
      fail(e);
    }
  };
  return (
    <>
      <PageHead k="PUBLIC CASES" title="对外案例" sub="仅从“已确认 + 允许对外展示”的业绩中挑选；生成时服务端按白名单取数，不含合同金额、报价、成本、毛利、内部备注、主机厂与投资方" />
      <div className="toolbar">
        <input className="input" placeholder="搜索案例名称、产品、地区、行业、主机类型" value={q} onChange={(e) => setQ(e.target.value)} />
        <input className="input" style={{ width: 110 }} placeholder="年份" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, ""))} />
        <select className="select" value={fx.industry} onChange={(e) => setFx({ ...fx, industry: e.target.value })}><option value="">全部行业</option>{(facets?.industry || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={fx.machine_type} onChange={(e) => setFx({ ...fx, machine_type: e.target.value })}><option value="">全部主机类型</option>{(facets?.machine_type || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <select className="select" value={fx.region} onChange={(e) => setFx({ ...fx, region: e.target.value })}><option value="">全部地区</option>{(facets?.region || []).map((x: string) => <option key={x}>{x}</option>)}</select>
        <input className="input" style={{ width: 120 }} placeholder="供货产品" value={fx.product} onChange={(e) => setFx({ ...fx, product: e.target.value })} />
      </div>
      <div className="split">
        <div className="card cardpad">
          <div className="sectionhead"><h2>可公开案例（{cands?.length ?? 0}）</h2><small>负责人/商务在业绩台账中勾选“允许作为对外案例”后出现</small></div>
          {loading && !cands ? <Loading err={err} status={status} /> : (cands || []).map((p) => (
            <label key={p.id} className="designline" style={{ cursor: "pointer" }}>
              <input type="checkbox" className="casecheck" checked={sel.includes(p.id)} disabled={!can("case.generate")} onChange={(e) => setSel(e.target.checked ? [...sel, p.id] : sel.filter((x) => x !== p.id))} />
              <div><b style={{ fontSize: 12 }}>{p.title}</b><div className="sub">{[p.year, p.region, p.industry, p.product_summary].filter(Boolean).join(" · ")}</div></div>
            </label>
          ))}
          {cands && cands.length === 0 && <div className="sub">没有符合条件的可公开案例</div>}
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="card cardpad">
            <div className="sectionhead"><h2>公开版预览</h2><span className="chip">服务端白名单字段</span></div>
            <div className="casepreview">
              {preview.map((c, i) => (
                <div className="caseitem" key={i}>
                  <h3>{c.title}</h3>
                  <div className="micro">{[c.year, c.region, c.industry, c.machine_type].filter(Boolean).join(" · ")}</div>
                  <div className="sub">供货产品：{c.product_summary || "—"}{c.owner_display && ` ｜ 业主：${c.owner_display}`}</div>
                  {(c.photos || []).length > 0 && <div className="tagline">{c.photos.map((ph: any) => <img key={ph.id} src={`/api/case-photos/${ph.id}/image/`} alt={ph.caption} title={ph.caption} style={{ height: 56, borderRadius: 4 }} />)}</div>}
                  {c.public_summary && <p className="sub">{c.public_summary}</p>}
                </div>
              ))}
              {!preview.length && <div className="sub">勾选左侧案例后显示</div>}
            </div>
          </div>
          {can("case.generate") && (
            <div className="card cardpad">
              <div className="sectionhead"><h2>生成案例册 PDF</h2></div>
              <Field label="标题"><input className="input" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} /></Field>
              <Field label="副标题"><input className="input" value={subtitle} maxLength={200} onChange={(e) => setSubtitle(e.target.value)} /></Field>
              <Field label="署名公司"><select className="select" value={entity} onChange={(e) => setEntity(Number(e.target.value) || "")}><option value="">（不署名）</option>{(ents || []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field>
              <label className="chk" style={{ marginTop: 10 }}><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已人工核对上方预览：公开简介中不含金额、内部备注或未经授权的单位名称</label>
              <button className="btn primary" style={{ marginTop: 12 }} disabled={!sel.length || busy || !confirmed} onClick={publish}>{busy ? "生成中…" : `生成案例册 PDF（${sel.length} 条）`}</button>
            </div>
          )}
        </div>
      </div>
      <div className="card cardpad" style={{ marginTop: 18 }}>
        <div className="sectionhead"><h2>已生成的案例册</h2></div>
        <div className="tablewrap">
          <table className="data">
            <thead><tr><th>标题</th><th>条数</th><th>生成人</th><th>时间</th><th>状态</th><th /></tr></thead>
            <tbody>
              {(releases || []).map((r) => (
                <tr key={r.id}><td>{r.title}</td><td>{r.performance_ids.length}</td><td>{r.created_by_name}</td><td>{dt(r.created_at, true)}</td>
                  <td>{r.withdrawn ? <span className="chip red" title={r.withdrawn_reason}>已撤回</span> : <span className="chip">有效</span>}</td>
                  <td className="rowactions">
                    {!r.withdrawn && <a className="btn mini" href={`/api/case-releases/${r.id}/pdf/`}>下载</a>}
                    {!r.withdrawn && <button className="btn mini" title="把这本案例册的案例组合重新勾选（常用组合复用）" onClick={() => (setSel(r.performance_ids), setTitle(r.title))}>以此册再选</button>}
                    {!r.withdrawn && can("case.withdraw") && <button className="btn mini warn" onClick={() => withdraw(r)}>撤回</button>}
                  </td></tr>
              ))}
              {releases && releases.length === 0 && <tr><td colSpan={6} className="empty-cell">暂无</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
