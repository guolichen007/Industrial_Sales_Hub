import { useEffect, useState } from "react";
import { dt, get, post } from "../api";
import { useAuth } from "../auth";
import { Field, Loading, PageHead, useLoad, useToast } from "../ui";

export default function Cases() {
  const { can } = useAuth();
  const { notify, fail } = useToast();
  const [cands, , loading, err] = useLoad(() => get<any[]>("/performances/?publishable=1"), []);
  const [ents] = useLoad(() => get<any[]>("/legal-entities/"), []);
  const [releases, reloadRel] = useLoad(() => get<any[]>("/case-releases/"), []);
  const [sel, setSel] = useState<number[]>([]);
  const [preview, setPreview] = useState<any[]>([]);
  const [title, setTitle] = useState("项目业绩案例");
  const [subtitle, setSubtitle] = useState("");
  const [entity, setEntity] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!sel.length) return setPreview([]);
    post("/case-preview/", { performance_ids: sel }).then((r) => setPreview(r.cases)).catch(fail);
  }, [sel, fail]);
  const publish = async () => {
    setBusy(true);
    try {
      const r = await post("/case-releases/", { performance_ids: sel, title, subtitle, legal_entity: entity || null });
      notify("案例册 PDF 已生成");
      reloadRel();
      window.open(`/api/case-releases/${r.id}/pdf/?inline=1`, "_blank");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead k="PUBLIC CASES" title="对外案例" sub="只从“已确认 + 允许对外展示”的业绩中挑选；生成时按白名单取数，不含合同金额、报价、成本、毛利、内部备注" />
      <div className="split">
        <div className="card cardpad">
          <div className="sectionhead"><h2>候选业绩（{cands?.length ?? 0}）</h2><small>在业绩台账中勾选“允许作为对外案例”后出现</small></div>
          {loading && !cands ? <Loading err={err} /> : (cands || []).map((p) => (
            <label key={p.id} className="designline" style={{ cursor: "pointer" }}>
              <input type="checkbox" className="casecheck" checked={sel.includes(p.id)} disabled={!can("case.write")} onChange={(e) => setSel(e.target.checked ? [...sel, p.id] : sel.filter((x) => x !== p.id))} />
              <div><b style={{ fontSize: 12 }}>{p.title}</b><div className="sub">{p.year} · {p.region} · {p.product_summary}</div></div>
            </label>
          ))}
          {cands && cands.length === 0 && <div className="sub">暂无可公开的已确认业绩</div>}
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          {can("case.write") && (
            <div className="card cardpad">
              <div className="sectionhead"><h2>案例册设置</h2></div>
              <Field label="标题"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
              <Field label="副标题"><input className="input" value={subtitle} onChange={(e) => setSubtitle(e.target.value)} /></Field>
              <Field label="署名公司"><select className="select" value={entity} onChange={(e) => setEntity(Number(e.target.value) || "")}><option value="">（不署名）</option>{(ents || []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></Field>
              <button className="btn primary" style={{ marginTop: 12 }} disabled={!sel.length || busy} onClick={publish}>生成案例册 PDF（{sel.length} 条）</button>
            </div>
          )}
          <div className="card cardpad">
            <div className="sectionhead"><h2>公开版预览</h2><span className="chip">服务端白名单字段</span></div>
            <div className="casepreview">
              {preview.map((c, i) => (
                <div className="caseitem" key={i}>
                  <h3>{c.title}</h3>
                  <div className="micro">{[c.year, c.region, c.industry, c.machine_type].filter(Boolean).join(" · ")}</div>
                  <div className="sub">供货产品：{c.product_summary || "—"}{c.owner_display && ` ｜ 业主：${c.owner_display}`}</div>
                  {c.public_summary && <p className="sub">{c.public_summary}</p>}
                </div>
              ))}
              {!preview.length && <div className="sub">勾选左侧业绩后显示</div>}
            </div>
          </div>
        </div>
      </div>
      <div className="card cardpad" style={{ marginTop: 18 }}>
        <div className="sectionhead"><h2>已生成的案例册</h2></div>
        <table className="data">
          <thead><tr><th>标题</th><th>条数</th><th>生成人</th><th>时间</th><th>PDF SHA-256</th><th /></tr></thead>
          <tbody>
            {(releases || []).map((r) => (
              <tr key={r.id}><td>{r.title}</td><td>{r.performance_ids.length}</td><td>{r.created_by_name}</td><td>{dt(r.created_at, true)}</td><td className="hash">{r.pdf_sha256.slice(0, 16)}…</td>
                <td><a className="btn mini" href={`/api/case-releases/${r.id}/pdf/`}>下载</a></td></tr>
            ))}
            {releases && releases.length === 0 && <tr><td colSpan={6} className="empty-cell">暂无</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
