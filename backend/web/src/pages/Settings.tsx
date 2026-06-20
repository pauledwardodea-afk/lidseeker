import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import type { AppSettings, User } from "../types";
import { Spinner } from "../components/States";

export function Settings() {
  const { me, isAdmin } = useAuth();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch(() => setSettings({}))
      .finally(() => setLoading(false));
  }, []);

  const setQuality = async (q: "mp3" | "flac") => {
    if (!settings || settings.quality === q || saving) return;
    const prev = settings.quality;
    setSettings({ ...settings, quality: q });
    setSaving(true);
    setMessage(null);
    try {
      const res = await api.setQuality(q);
      setMessage(res.message ?? "Saved");
    } catch {
      setSettings((s) => (s ? { ...s, quality: prev } : s));
      setMessage("Couldn't change quality");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Spinner />;
  if (!settings) return null;

  return (
    <div className="stack">
      <div className="card" style={{ padding: 16 }}>
        <div className="section-title">Account</div>
        <p className="muted" style={{ marginTop: 2, fontSize: 14 }}>
          Signed in as <b>{me?.username}</b>
          {me && <span className="muted"> · {me.role}</span>}
        </p>
      </div>

      {isAdmin && <UsersCard />}

      <ChangePasswordCard />

      {settings.quality != null && (
        <div className="card" style={{ padding: 16 }}>
          <div className="section-title">Download quality</div>
          <p className="muted" style={{ marginTop: 2, fontSize: 13 }}>
            FLAC prefers lossless and falls back to MP3. Applies to new requests.
          </p>
          <div className="tabs" style={{ maxWidth: 240 }}>
            {(["mp3", "flac"] as const).map((q) => (
              <button key={q} className={settings.quality === q ? "on" : ""} onClick={() => setQuality(q)} disabled={saving}>
                {q.toUpperCase()}
              </button>
            ))}
          </div>
          {message && <div className="toast">{message}</div>}
        </div>
      )}

      <div className="card" style={{ padding: 16 }}>
        <div className="section-title">Notifications</div>
        {settings.ntfyTopic ? (
          <>
            <p className="muted" style={{ marginTop: 2, fontSize: 13 }}>
              Get pinged when an album is ready. Install the ntfy app and subscribe to this topic:
            </p>
            <div style={{ fontSize: 14 }}>
              Server: <span className="muted">{settings.ntfyUrl}</span>
            </div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>Topic: {settings.ntfyTopic}</div>
          </>
        ) : (
          <p className="muted" style={{ marginTop: 2, fontSize: 13 }}>
            Push notifications aren’t configured on the server.
          </p>
        )}
      </div>
    </div>
  );
}

function UsersCard() {
  const { me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "user">("user");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<User | null>(null);

  const load = () => api.users().then(setUsers).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createUser(username.trim(), password, role);
      setUsername("");
      setPassword("");
      setRole("user");
      load();
    } catch (err: any) {
      setError(err.message || "Couldn't create user");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (u: User) => {
    setConfirmDel(null);
    try {
      await api.deleteUser(u.id);
      load();
    } catch (err: any) {
      setError(err.message || "Couldn't remove user");
    }
  };

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="section-title">Users</div>
      <p className="muted" style={{ marginTop: 2, marginBottom: 12, fontSize: 13 }}>
        Add accounts for other people. Each person sees only their own requests; admins see everyone’s.
      </p>

      <div className="stack" style={{ marginBottom: 14 }}>
        {users.map((u) => (
          <div className="row" key={u.id} style={{ padding: "8px 4px" }}>
            <div className="meta">
              <div className="title">{u.username}</div>
              <div className="sub">{u.role}</div>
            </div>
            {u.username === me?.username ? (
              <span className="muted" style={{ fontSize: 13 }}>You</span>
            ) : (
              <button className="btn small danger" onClick={() => setConfirmDel(u)}>
                Remove
              </button>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={add} className="stack">
        <input
          className="input"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoCapitalize="none"
        />
        <input
          className="input"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <div className="tabs" style={{ maxWidth: 240 }}>
          {(["user", "admin"] as const).map((r) => (
            <button type="button" key={r} className={role === r ? "on" : ""} onClick={() => setRole(r)}>
              {r === "user" ? "User" : "Admin"}
            </button>
          ))}
        </div>
        <button className="btn primary" type="submit" disabled={busy || !username.trim() || !password}>
          {busy ? "Adding…" : "Add user"}
        </button>
        {error && <div className="detail fail">{error}</div>}
      </form>

      {confirmDel && (
        <div className="backdrop" onClick={() => setConfirmDel(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Remove user?</h3>
            <p className="muted">
              “{confirmDel.username}” will be removed and can no longer sign in. Their request history
              stays. This can’t be undone.
            </p>
            <div className="actions">
              <button className="btn ghost" onClick={() => setConfirmDel(null)}>
                Cancel
              </button>
              <button className="btn danger" onClick={() => remove(confirmDel)}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.changePassword(current, next);
      setOk(true);
      setMessage(res.message ?? "Password changed.");
      setCurrent("");
      setNext("");
    } catch (err: any) {
      setOk(false);
      setMessage(err.message || "Couldn't change password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="section-title">Change password</div>
      <form onSubmit={submit} className="stack" style={{ marginTop: 8 }}>
        <input
          className="input"
          type="password"
          placeholder="Current password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <input
          className="input"
          type="password"
          placeholder="New password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <button className="btn primary" type="submit" disabled={busy || !current || !next}>
          {busy ? "Saving…" : "Update password"}
        </button>
        {message && <div className={ok ? "toast" : "detail fail"}>{message}</div>}
      </form>
    </div>
  );
}
