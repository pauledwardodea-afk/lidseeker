const LABELS: Record<string, string> = {
  available: "Available",
  downloading: "Downloading",
  failed: "Failed",
  error: "Error",
  pending: "Pending",
};

export function StatusChip({ status }: { status: string }) {
  const cls = LABELS[status] ? status : "pending";
  return <span className={`statuschip ${cls}`}>{LABELS[status] ?? "Pending"}</span>;
}
