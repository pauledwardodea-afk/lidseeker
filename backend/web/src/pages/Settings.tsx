import { useEffect, useState } from "react";
import { api } from "../api";
import type { AppSettings } from "../types";
import { Spinner } from "../components/States";

export function Settings() {
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
