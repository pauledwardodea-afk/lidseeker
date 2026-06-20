import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoverCategories, SearchResult } from "../types";
import { MediaRow } from "../components/MediaRow";
import { RequestButton, type ReqState } from "../components/RequestButton";
import { EmptyState, Spinner } from "../components/States";

export function Discover() {
  const [items, setItems] = useState<SearchResult[]>([]);
  const [cats, setCats] = useState<DiscoverCategories>({ genres: [], decades: [] });
  const [genre, setGenre] = useState<string | null>(null);
  const [decade, setDecade] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reqState, setReqState] = useState<Record<string, ReqState>>({});

  const filtered = genre !== null || decade !== null;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([api.discover(genre, decade), api.discoverCategories(genre, decade)])
      .then(([list, c]) => {
        if (!alive) return;
        setItems(list);
        setCats(c);
        setError(null);
      })
      .catch((e) => alive && setError(e.message || "Couldn't load Discover"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [genre, decade]);

  const request = async (r: SearchResult) => {
    setReqState((s) => ({ ...s, [r.foreignId]: "loading" }));
    try {
      const res = await api.request({ type: "album", foreignId: r.foreignId });
      setReqState((s) => ({ ...s, [r.foreignId]: res.status === "error" ? "error" : "done" }));
    } catch {
      setReqState((s) => ({ ...s, [r.foreignId]: "error" }));
    }
  };

  return (
    <>
      <div className="chips" style={{ marginBottom: 14 }}>
        <button className={"chip" + (!filtered ? " on" : "")} onClick={() => { setGenre(null); setDecade(null); }}>
          New
        </button>
        {cats.decades.map((d) => (
          <button key={"d" + d} className={"chip" + (decade === d ? " on" : "")} onClick={() => setDecade(decade === d ? null : d)}>
            {d}s
          </button>
        ))}
        {cats.genres.map((g) => (
          <button key={"g" + g} className={"chip" + (genre === g ? " on" : "")} onClick={() => setGenre(genre === g ? null : g)}>
            {g}
          </button>
        ))}
      </div>

      {loading ? (
        <Spinner />
      ) : error && items.length === 0 ? (
        <EmptyState icon="☁️" title="Couldn't load Discover" message={error} />
      ) : items.length === 0 ? (
        <EmptyState
          icon="🧭"
          title={filtered ? "Nothing in this category" : "Nothing new yet"}
          message={
            filtered
              ? "No unowned albums here right now. Try another genre or decade."
              : "New releases from artists in your library will show up here."
          }
        />
      ) : (
        <div className="card stack" style={{ padding: 0 }}>
          {items.map((a) => (
            <MediaRow
              key={a.foreignId}
              imageUrl={a.imageUrl}
              title={a.title}
              subtitle={[a.artist, a.year].filter(Boolean).join(" · ")}
              trailing={
                <RequestButton
                  inLibrary={a.inLibrary}
                  requested={a.requested}
                  state={reqState[a.foreignId] ?? "idle"}
                  onClick={() => request(a)}
                />
              }
            />
          ))}
        </div>
      )}
    </>
  );
}
