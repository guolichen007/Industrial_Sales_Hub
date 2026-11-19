import { useRef, useState } from "react";
import { api, dt, get } from "../api";
import { useLoad, useToast } from "../ui";

export function Attachments({ ownerType, ownerId, canUpload, title }: { ownerType: string; ownerId: number; canUpload: boolean; title: string }) {
  const { notify, fail } = useToast();
  const [list, reload, , err] = useLoad(() => get<any[]>(`/attachments/?owner_type=${ownerType}&owner_id=${ownerId}`), [ownerType, ownerId]);
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("owner_type", ownerType);
    fd.append("owner_id", String(ownerId));
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
            <input ref={ref} type="file" hidden accept=".pdf,.png,.jpg,.jpeg,.xlsx,.docx,.zip" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <button className="btn mini soft" disabled={busy} onClick={() => ref.current?.click()}>{busy ? "上传中…" : "+ 上传附件"}</button>
          </>
        )}
      </div>
      {err ? <div className="sub">{err}</div> : (
        <div className="tablewrap">
        <table className="data">
          <thead><tr><th>文件</th><th>大小</th><th>上传人</th><th>时间</th><th /></tr></thead>
          <tbody>
            {(list || []).map((a) => (
              <tr key={a.id}>
                <td>{a.filename}</td><td>{(a.size / 1024).toFixed(1)} KB</td><td>{a.uploaded_by_name}</td><td>{dt(a.uploaded_at, true)}</td>
                <td><a className="btn mini" href={`/api/attachments/${a.id}/download/`}>下载</a></td>
              </tr>
            ))}
            {list && list.length === 0 && <tr><td colSpan={5} className="empty-cell">暂无附件（支持 PDF、图片、xlsx、docx、zip，≤20MB）</td></tr>}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
