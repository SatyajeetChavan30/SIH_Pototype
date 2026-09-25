import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, SIGNED_OUT_EVENT } from "./api";
import { setActor } from "./live";

export type Role = "field" | "office" | "admin";
export interface User { username: string; display_name: string; role: Role }

interface AuthCtx {
  user: User | null;        // null when sign-in is switched off on the server (NWIS_AUTH=off)
  authOn: boolean;
  /** True when the signed-in role may use a feature restricted to `roles` (always true with sign-in off). */
  can: (...roles: Role[]) => boolean;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({ user: null, authOn: false, can: () => true, logout: () => undefined });
export const useAuth = () => useContext(Ctx);
export const OFFICE: Role[] = ["office", "admin"];

const USER_KEY = "nwis.user";
const ROLE_LABEL: Record<Role, string> = { field: "Field / rig site", office: "Office / RTOC", admin: "Administrator" };
export const roleLabel = (r: Role) => ROLE_LABEL[r] ?? r;

function cached(): User | null {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); } catch { return null; }
}
function remember(u: User | null) {
  try { u ? localStorage.setItem(USER_KEY, JSON.stringify(u)) : localStorage.removeItem(USER_KEY); } catch { /* best-effort */ }
  if (u) setActor(u.display_name);
}

/** Shows the sign-in screen until the server accepts a session; everything inside may assume access. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ ready: boolean; authOn: boolean; user: User | null; demo: { username: string; role: Role }[] }>(
    { ready: false, authOn: false, user: null, demo: [] });

  useEffect(() => {
    api<{ auth: boolean; user: User | null; demo_users: { username: string; role: Role }[] }>("/api/auth/me")
      .then((m) => { remember(m.user); setState({ ready: true, authOn: m.auth, user: m.user, demo: m.demo_users }); })
      // server unreachable (rig link down): keep the last signed-in user so the cached rig view still opens
      .catch(() => { const u = cached(); setState({ ready: true, authOn: !!u, user: u, demo: [] }); });
    const out = () => setState((s) => (s.authOn ? { ...s, user: null } : s));
    window.addEventListener(SIGNED_OUT_EVENT, out);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, out);
  }, []);

  if (!state.ready) return <div className="empty" style={{ padding: 40 }}>Loading NWIS…</div>;
  if (state.authOn && !state.user) return <Login demo={state.demo} onDone={(u) => { remember(u); setState((s) => ({ ...s, user: u })); }} />;

  const { user, authOn } = state;
  const value: AuthCtx = {
    user, authOn,
    can: (...roles) => !authOn || (!!user && roles.includes(user.role)),
    logout: () => {
      // drop the offline copy of API data too, so the next person on a shared tablet starts clean
      const clearCache = "caches" in window ? caches.delete("nwis-api").catch(() => false) : Promise.resolve(false);
      api("/api/auth/logout", { method: "POST" }).catch(() => undefined)
        .finally(() => clearCache.finally(() => { remember(null); location.reload(); }));
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function Login({ demo, onDone }: { demo: { username: string; role: Role }[]; onDone: (u: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const r = await api<{ user: User }>("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }) });
      onDone(r.user);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return <div className="login-page">
    <form className="card login" onSubmit={submit}>
      <div className="brand" style={{ marginBottom: 4 }}>
        <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><rect width="32" height="32" rx="7" fill="#000000" /><path d="M16 4 L22 28 H10 Z" fill="none" stroke="#ffffff" strokeWidth="2.5" /><circle cx="16" cy="12" r="3" fill="#d95926" /></svg>
        <span>eRTMAC-NWIS <small>Nearby Wells Intelligence System</small></span>
      </div>
      <h2 className="view" style={{ margin: "8px 0 2px" }}>Sign in</h2>
      <p className="small muted" style={{ margin: "0 0 12px" }}>Your name goes into the decision log with every acknowledgement and approval.</p>
      <label className="small">Username
        <input type="text" autoComplete="username" value={username} autoFocus onChange={(e) => setUsername(e.target.value)} />
      </label>
      <label className="small">Password
        <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      {err && <div className="small" style={{ color: "var(--bad-ink)" }}>{err}</div>}
      <button className="btn primary" type="submit" disabled={busy || !username || !password}>{busy ? "Signing in…" : "Sign in"}</button>
      {demo.length > 0 && <div className="small muted login-demo">
        Demo accounts (password <code>demo</code>):
        <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
          {demo.map((d) => <button key={d.username} type="button" className="btn sm" onClick={() => { setUsername(d.username); setPassword("demo"); }}>
            {d.username} · {roleLabel(d.role)}</button>)}
        </div>
      </div>}
    </form>
  </div>;
}
