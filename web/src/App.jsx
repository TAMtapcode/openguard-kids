import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, isRemote, serverUrl, setCsrf, setToken } from "./api.js";
import { Loading, ToastProvider } from "./ui.jsx";
import { Login, Register } from "./pages/Auth.jsx";
import Connect from "./pages/Connect.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Child from "./pages/Child.jsx";
import Kid from "./pages/Kid.jsx";
import Admin from "./pages/Admin.jsx";
import Privacy from "./pages/Privacy.jsx";

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);
export const homeFor = u => ({ parent: "/", child_view: "/kid", admin: "/admin" })[u.role] || "/";

function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, user: null });
  const refresh = useCallback(async () => {
    if (isRemote && !serverUrl()) return setState({ loading: false, user: null });
    try {
      const r = await api("GET", "/api/auth/me");
      setCsrf(r.csrf);
      setState({ loading: false, user: r.user });
    } catch {
      setState({ loading: false, user: null });
    }
  }, []);
  useEffect(() => {
    refresh();
    const on401 = () => { setToken(null); setState({ loading: false, user: null }); };
    window.addEventListener("ogk:401", on401);
    return () => window.removeEventListener("ogk:401", on401);
  }, [refresh]);
  const signIn = (path, body) => api("POST", path, { ...body, token: isRemote }).then(r => {
    if (r.token) setToken(r.token);
    setCsrf(r.csrf);
    setState({ loading: false, user: r.user });
    return r.user;
  });
  const signOut = async () => {
    try { await api("POST", "/api/auth/logout"); } catch { /* phiên đã hết hạn */ }
    setToken(null);
    setCsrf("");
    setState({ loading: false, user: null });
  };
  return <AuthCtx.Provider value={{ ...state, signIn, signOut }}>{children}</AuthCtx.Provider>;
}

function Shell({ children }) {
  const { user, signOut } = useAuth();
  const nav = useNavigate();
  return (
    <>
      <a className="skip" href="#main">Bỏ qua điều hướng</a>
      <nav className="top">
        <Link className="brand" to="/"><span className="brand-mark" aria-hidden="true" />Open Guardian Kids</Link>
        <span className="grow" />
        {isRemote && serverUrl() && (
          <button className="server-chip" type="button" title="Đổi máy chủ" onClick={() => nav("/connect")}>{new URL(serverUrl()).host}</button>
        )}
        {user && <span className="who">{user.display_name}</span>}
        {user && <button className="btn ghost" type="button" onClick={async () => { await signOut(); nav("/login"); }}>Đăng xuất</button>}
      </nav>
      <main id="main">{children}</main>
    </>
  );
}

function Private({ roles, children }) {
  const { loading, user } = useAuth();
  if (loading) return <Loading />;
  if (isRemote && !serverUrl()) return <Navigate to="/connect" replace />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(user.role)) return <Navigate to={homeFor(user)} replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <Routes>
          <Route path="/connect" element={<Connect />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/privacy" element={<Shell><Privacy /></Shell>} />
          <Route path="/" element={<Private roles={["parent"]}><Dashboard /></Private>} />
          <Route path="/child/:id" element={<Private roles={["parent"]}><Child /></Private>} />
          <Route path="/kid" element={<Private roles={["child_view"]}><Kid /></Private>} />
          <Route path="/admin" element={<Private roles={["admin"]}><Admin /></Private>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </ToastProvider>
  );
}
