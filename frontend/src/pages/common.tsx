import { useRef, useState } from "react";
import { api, del, dt, get, patch } from "../api";
import { useAuth } from "../auth";
import { useLoad, useToast } from "../ui";

const KLASS: Record<string, string> = { INTERNAL: "内部", PRESALE: "售前资料", DELIVERY: "交付/进度" };

export function Attachments({ ownerType, ownerId, canUpload, title }: { ownerType: string; ownerId: number; canUpload: boolean; title: string }) {
  const { notify, fail } = useToast();
  const [list, reload, , err] = useLoad(() => get<any[]>(`/attachments/?owner_type=${ownerType}&owner_id=${ownerId}`), [ownerType, ownerId]);
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const { can, me } = useAuth();
  const [klass, setKlass] = useState(ownerType === "project" && me?.role !== "SALES" ? "INTERNAL" : "");
  const manage = can("project.edit_any");
  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("owner_type", ownerType);
    fd.append("owner_id", String(ownerId));
    if (klass) fd.append("klass", klass);
    fd.append("file", f);
    setBusy(true);
    try {
      await api("/attachments/", { form: fd });
      notify("上传成功");
      reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  };
  return (
    <div>
      <div className="sectionhead">
        <h2>{title}</h2>
        {canUpload && (
          <>
            {ownerType === "project" && manage && <select className="select" value={klass} onChange={(e) => setKlass(e.target.value)} title="附件类别"><option value="INTERNAL">内部</option><option value="DELIVERY">交付/进度</option></select>}
            <input ref={ref} type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.xlsx,.docx,.zip" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <button className="btn mini soft" disabled={busy} onClick={() => ref.current?.click()}>{busy ? "上传中…" : "+ 上传附件"}</button>
          </>
        )}
      </div>
      {err ? <div className="sub">{err}</div> : (
        <div className="tablewrap">
        <table className="data">
          <thead><tr><th>文件</th><th>类别</th><th>大小</th><th>上传人</th><th>时间</th><th /></tr></thead>
          <tbody>
            {(list || []).map((a) => (
              <tr key={a.id}>
                <td>{a.filename}</td>
                <td>{KLASS[a.klass] || a.klass}{a.sales_visible && <span className="chip blue" style={{ marginLeft: 4 }}>销售可见</span>}</td>
                <td>{(a.size / 1024).toFixed(1)} KB</td><td>{a.uploaded_by_name}</td><td>{dt(a.uploaded_at, true)}</td>
                <td className="rowactions"><a className="btn mini" href={`/api/attachments/${a.id}/download/`}>下载</a>
                  {manage && a.klass === "DELIVERY" && <button className="btn mini" onClick={() => patch(`/attachments/${a.id}/`, { sales_visible: !a.sales_visible }).then(reload).catch(fail)}>{a.sales_visible ? "撤回销售可见" : "授权销售可见"}</button>}
                  {canUpload && (a.uploaded_by_name === me?.name || me?.role === "LEADER") && <button className="btn mini" onClick={() => confirm(`删除附件「${a.filename}」？`) && del(`/attachments/${a.id}/`).then(reload).catch(fail)}>删除</button>}</td>
              </tr>
            ))}
            {list && list.length === 0 && <tr><td colSpan={6} className="empty-cell">暂无附件（支持 PDF、图片、xlsx、docx、zip，≤20MB）</td></tr>}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
