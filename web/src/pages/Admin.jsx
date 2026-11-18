import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Loading, useToast } from "../ui.jsx";

export default function Admin() {
  const { user } = useAuth();
  const toast = useToast();
  const [o, setO] = useState(null);
  const load = useCallback(() => api("GET", "/api/admin/overview").then(setO).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  if (!o) return <Loading />;
  const toggle = async u => { try { await api("POST", `/api/admin/users/${u.id}/toggle`); load(); } catch (e) { toast(e.message, true); } };
  return (
    <>
      <header className="page-head"><h1>Quản trị hệ thống</h1>
        <p className="muted">{o.stats.children} hồ sơ trẻ · {o.stats.devices} thiết bị. Quản trị viên không xem được dữ liệu hoạt động của trẻ.</p></header>
      <section className="panel"><div className="scroll"><table>
        <thead><tr><th>ID</th><th>Tài khoản</th><th>Vai trò</th><th>Trạng thái</th><th /></tr></thead>
        <tbody>{o.users.map(u => (
          <tr key={u.id}><td>{u.id}</td><td>{u.login}</td><td>{u.role}</td><td>{u.disabled ? "đã khóa" : "hoạt động"}</td>
            <td>{u.id !== user.id && <button className="btn ghost" type="button" onClick={() => toggle(u)}>{u.disabled ? "Mở" : "Khóa"}</button>}</td></tr>
        ))}</tbody>
      </table></div></section>
    </>
  );
}
