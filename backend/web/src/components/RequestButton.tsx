export type ReqState = "idle" | "loading" | "done" | "error";

export function RequestButton({
  inLibrary,
  requested,
  state,
  onClick,
}: {
  inLibrary: boolean;
  requested: boolean;
  state: ReqState;
  onClick: () => void;
}) {
  if (inLibrary) return <span className="muted" style={{ fontSize: 13 }}>In library</span>;
  if (state === "done" || requested)
    return <span className="muted" style={{ fontSize: 13 }}>Requested</span>;
  if (state === "loading") return <button className="btn small" disabled>…</button>;
  return (
    <button className="btn small primary" onClick={onClick}>
      {state === "error" ? "Retry" : "Request"}
    </button>
  );
}
