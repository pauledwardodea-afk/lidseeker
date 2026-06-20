import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { SearchResult } from "../types";
import { MediaRow } from "../components/MediaRow";
import { RequestButton, type ReqState } from "../components/RequestButton";
import { EmptyState, Spinner } from "../components/States";

type Tab = "album" | "artist" | "track";
const TAB_LABEL: Record<Tab, string> = { album: "Albums", artist: "Artists", track: "Songs" };

export function Search() {
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("album");
  const [term, setTerm] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reqState, setReqState] = useState<Record<string, ReqState>>({});

  const run = async (t: Tab = tab) => {
    if (!term.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.search(term.trim(), t);
      setResults(res);
    } catch (e: any) {
      setError(e.message || "Search failed");
      setResults([]);
    } finally {
      setLoading(false);
      setSearched(true);
    }
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    setResults([]);
    setSearched(false);
    if (term.trim()) run(t);
  };

  const request = async (r: SearchResult) => {
    setReqState((s) => ({ ...s, [r.foreignId]: "loading" }));
    try {
      const body =
        r.type === "track"
          ? { type: "track" as const, foreignId: r.foreignId, albumForeignId: r.albumForeignId ?? undefined }
          : { type: "album" as const, foreignId: r.foreignId };
      const res = await api.request(body);
      setReqState((s) => ({ ...s, [r.foreignId]: res.status === "error" ? "error" : "done" }));
    } catch {
      setReqState((s) => ({ ...s, [r.foreignId]: "error" }));
    }
  };

  return (
    <>
      <div className="searchbar">
        <input
          placeholder={`Search ${TAB_LABEL[tab].toLowerCase()}…`}
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <button className="btn primary" onClick={() => run()}>
          Search
        </button>
      </div>

      <div className="tabs" style={{ marginBottom: 14 }}>
        {(["album", "artist", "track"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => switchTab(t)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      {loading ? (
        <Spinner />
      ) : error ? (
        <EmptyState icon="☁️" title="Search failed" message={error} />
      ) : !searched ? (
        <EmptyState icon="🔎" title="Search for music" message={`Find ${TAB_LABEL[tab].toLowerCase()} to request.`} />
      ) : results.length === 0 ? (
        <EmptyState icon="🤷" title="No results" message="Try a different search." />
      ) : (
        <div className="card stack" style={{ padding: 0 }}>
          {results.map((r) =>
            r.type === "artist" ? (
              <MediaRow
                key={r.foreignId}
                imageUrl={r.imageUrl}
                title={r.title}
                subtitle="Artist"
                onClick={() => nav(`/artist/${encodeURIComponent(r.foreignId)}?name=${encodeURIComponent(r.title)}`)}
                trailing={<span className="muted">›</span>}
              />
            ) : (
              <MediaRow
                key={r.foreignId}
                imageUrl={r.imageUrl}
                title={r.title}
                subtitle={[r.artist, r.albumTitle, r.year].filter(Boolean).join(" · ")}
                trailing={
                  <RequestButton
                    inLibrary={r.inLibrary}
                    requested={r.requested}
                    state={reqState[r.foreignId] ?? "idle"}
                    onClick={() => request(r)}
                  />
                }
              />
            ),
          )}
        </div>
      )}
    </>
  );
}
