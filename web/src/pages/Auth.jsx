import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { isRemote, serverUrl } from "../api.js";
import { homeFor, useAuth } from "../App.jsx";

function AuthForm({ mode }) {
  const { user, loading, signIn } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({ login: "", password: "", name: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (isRemote && !serverUrl()) return <Navigate to="/connect" replace />;
  if (!loading && user) return <Navigate to={homeFor(user)} replace />;
  const reg = mode === "register";
  const set = k => e => setF({ ...f, [k]: e.target.value });
  const submit = async e => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const u = await signIn(reg ? "/api/auth/register" : "/api/auth/login", reg ? f : { login: f.login, password: f.password });
      nav(homeFor(u), { replace: true });
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <div className="auth"><form className="auth-card" onSubmit={submit}>
      <span className="brand big"><span className="brand-mark" aria-hidden="true" />Open Guardian Kids</span>
      <h1>{reg ? "Tạo tài khoản phụ huynh" : "Đăng nhập"}</h1>
      <div className="stack">
        {err && <p className="error" role="alert">{err}</p>}
        <label>Email hoặc tên đăng nhập<input value={f.login} onChange={set("login")} required autoComplete="username" autoFocus /></label>
        {reg && <label>Tên hiển thị (không bắt buộc)<input value={f.name} onChange={set("name")} maxLength={100} /></label>}
        <label>Mật khẩu{reg && " (tối thiểu 10 ký tự)"}
          <input type="password" value={f.password} onChange={set("password")} required minLength={reg ? 10 : undefined}
            autoComplete={reg ? "new-password" : "current-password"} /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Đang xử lý…" : reg ? "Tạo tài khoản" : "Đăng nhập"}</button>
      </div>
      <p className="muted">{reg ? <>Đã có tài khoản? <Link to="/login">Đăng nhập</Link></> : <>Chưa có tài khoản? <Link to="/register">Tạo tài khoản</Link></>}
        {" · "}<Link to="/privacy">Quyền riêng tư</Link>{isRemote && <> · <Link to="/connect">Đổi máy chủ</Link></>}</p>
    </form></div>
  );
}
export const Login = () => <AuthForm mode="login" />;
export const Register = () => <AuthForm mode="register" />;