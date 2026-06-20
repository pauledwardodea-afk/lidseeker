import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

export function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { token } = await api.login(username.trim(), password);
      login(token);
      nav("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Invalid username or password" : "Couldn't sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="container" style={{ maxWidth: 380, marginTop: "12vh" }}>
      <h1 className="brand" style={{ textAlign: "center", fontSize: 28, marginBottom: 4 }}>
        Lidseeker
      </h1>
      <p className="muted" style={{ textAlign: "center", marginTop: 0, marginBottom: 24 }}>
        Sign in to request music
      </p>
      <form className="card" style={{ padding: 18 }} onSubmit={submit}>
        <div className="field">
          <label>Username</label>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoCapitalize="none"
            autoCorrect="off"
            autoFocus
          />
        </div>
        <div className="field">
          <label>Password</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="input"
              type={show ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button type="button" className="btn ghost small" onClick={() => setShow((s) => !s)}>
              {show ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {error && <div className="detail fail">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy} style={{ width: "100%", marginTop: 8 }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
