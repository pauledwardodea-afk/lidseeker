import type { Pipeline as PipelineT, ServiceLink } from "../types";

const STAGE_LABELS: Record<string, string> = {
  requested: "Requested",
  searching: "Searching",
  downloading: "Downloading",
  importing: "Importing",
  available: "Available",
};

export function Pipeline({
  pipeline,
  services,
  onRetry,
  onSearchNow,
  onRemove,
  retrying,
  searching,
  searchMessage,
}: {
  pipeline: PipelineT;
  services: ServiceLink[];
  onRetry: () => void;
  onSearchNow: () => void;
  onRemove: () => void;
  retrying: boolean;
  searching: boolean;
  searchMessage?: string | null;
}) {
  const showBar =
    pipeline.stage === "downloading" ||
    pipeline.stage === "importing" ||
    (pipeline.percent > 0 && pipeline.percent < 100);
  const canSearch = pipeline.stage !== "available" && !pipeline.failed && !pipeline.stuck;

  return (
    <div className="pipeline">
      <div className="stepper">
        {pipeline.stages.map((key, i) => {
          const cls = i < pipeline.stageIndex ? "done" : i === pipeline.stageIndex ? "current" : "";
          return (
            <div className={`step ${cls}`} key={key}>
              <div className="dot">{i < pipeline.stageIndex ? "✓" : i + 1}</div>
              <div className="label">{STAGE_LABELS[key] ?? key}</div>
            </div>
          );
        })}
      </div>

      {pipeline.detail && (
        <div className={"detail" + (pipeline.failed ? " fail" : "")}>{pipeline.detail}</div>
      )}

      {showBar && (
        <div className="bar">
          <div style={{ width: `${Math.min(100, Math.max(0, pipeline.percent))}%` }} />
        </div>
      )}

      <div className="btnrow">
        {(pipeline.failed || pipeline.stuck) && (
          <button className="btn small primary" onClick={onRetry} disabled={retrying}>
            {retrying ? "Retrying…" : "Retry"}
          </button>
        )}
        {canSearch && (
          <button className="btn small" onClick={onSearchNow} disabled={searching}>
            {searching ? "Starting…" : "Search now"}
          </button>
        )}
        <button className="btn small danger" onClick={onRemove}>
          Remove
        </button>
      </div>
      {searchMessage && <div className="toast">{searchMessage}</div>}

      {services.length > 0 && (
        <div className="linkchips">
          <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>
            Open in
          </span>
          {services.map((s) => (
            <a className="chip" key={s.name} href={s.url} target="_blank" rel="noreferrer">
              {s.name} ↗
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
