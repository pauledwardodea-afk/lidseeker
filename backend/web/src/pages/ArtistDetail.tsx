import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { api } from "../api";
import type { SearchResult } from "../types";
import { CoverArt } from "../components/MediaRow";
import { EmptyState, Spinner } from "../components/States";

const isSingleOrEp = (a: SearchResult) => {
  const t = (a.albumType ?? "").toLowerCase();
  return t === "single" || t === "ep";
};

export function ArtistDetail() {
  const { id = "" } = useParams();
  const [sp] = useSearchParams();
  const name = sp.get("name") ?? "Artist";

  const [albums, setAlbums] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"albums" | "eps">("albums");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .artistAlbums(id)
      .then((a) => alive && (setAlbums(a), setError(null)))
      .catch((e) => alive && setError(e.message || "Couldn't load discography"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id]);

  const shown = useMemo(
    () => albums.filter((a) => (tab === "eps" ? isSingleOrEp(a) : !isSingleOrEp(a))),
    [albums, tab],
  );

  const isLocked = (a: SearchResult) => a.inLibrary || a.requested || requested.has(a.foreignId);

  const toggle = (fid: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(fid) ? n.delete(fid) : n.add(fid);
      return n;
    });

  const selectableShown = shown.filter((a) => !isLocked(a));
  const allSelected = selectableShown.length > 0 && selectableShown.every((a) => selected.has(a.foreignId));
  const toggleAll = () =>
    setSelected((s) => {
      const n = new Set(s);
      if (allSelected) selectableShown.forEach((a) => n.delete(a.foreignId));
      else selectableShown.forEach((a) => n.add(a.foreignId));
      return n;
    });

  const submit = async () => {
    if (selected.size === 0 || submitting) return;
    setSubmitting(true);
    // Sequential to avoid the artist-create race on the backend.
    for (const fid of Array.from(selected)) {
      try {
        await api.request({ type: "album", foreignId: fid });
        setRequested((r) => new Set(r).add(fid));
      } catch {
        /* leave it selected so the user can retry */
      }
    }
    setSelected(new Set());
    setSubmitting(false);
  };

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <CoverArt url={null} alt={name} size={48} />
        <div>
          <h2 style={{ margin: 0 }}>{name}</h2>
          <Link to="/search" className="muted" style={{ fontSize: 13 }}>
            ‹ Back to search
          </Link>
        </div>
      </div>

      <div className="tabs" style={{ marginBottom: 12 }}>
        <button className={tab === "albums" ? "on" : ""} onClick={() => setTab("albums")}>
          Albums
        </button>
        <button className={tab === "eps" ? "on" : ""} onClick={() => setTab("eps")}>
          Singles &amp; EPs
        </button>
      </div>

      {loading ? (
        <Spinner />
      ) : error ? (
        <EmptyState icon="☁️" title="Couldn't load discography" message={error} />
      ) : shown.length === 0 ? (
        <EmptyState icon="💿" title="Nothing here" message="No releases of this type found." />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
            <button className="btn ghost small" onClick={toggleAll} disabled={selectableShown.length === 0}>
              {allSelected ? "Select none" : "Select all"}
            </button>
            <span className="muted" style={{ marginLeft: "auto", fontSize: 13 }}>
              {selected.size} selected
            </span>
          </div>
          <div className="card stack" style={{ padding: 0 }}>
            {shown.map((a) => {
              const locked = isLocked(a);
              return (
                <div className="row" key={a.foreignId}>
                  {!locked && (
                    <input
                      type="checkbox"
                      checked={selected.has(a.foreignId)}
                      onChange={() => toggle(a.foreignId)}
                      style={{ width: 18, height: 18, flex: "none" }}
                    />
                  )}
                  <CoverArt url={a.imageUrl} alt={`Cover art for ${a.title}`} />
                  <div className="meta">
                    <div className="title">{a.title}</div>
                    <div className="sub">{[a.albumType, a.year].filter(Boolean).join(" · ")}</div>
                  </div>
                  <div className="trailing muted" style={{ fontSize: 13 }}>
                    {a.inLibrary ? "In library" : locked ? "Requested" : ""}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {selected.size > 0 && (
        <div style={{ position: "sticky", bottom: 0, paddingTop: 12 }}>
          <button className="btn primary" style={{ width: "100%" }} onClick={submit} disabled={submitting}>
            {submitting ? "Requesting…" : `Request (${selected.size})`}
          </button>
        </div>
      )}
    </>
  );
}
