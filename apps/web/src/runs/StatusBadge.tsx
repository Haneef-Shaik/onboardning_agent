const TONES: Readonly<Record<string, string>> = {
  running: "bg-blue-100 text-blue-900",
  started: "bg-blue-100 text-blue-900",
  ready: "bg-blue-50 text-blue-900",
  waiting_approval: "bg-violet-100 text-violet-900",
  paused: "bg-slate-200 text-slate-800",
  blocked: "bg-amber-100 text-amber-900",
  failed: "bg-red-100 text-red-900",
  completed: "bg-green-100 text-green-900",
  done: "bg-green-100 text-green-900",
};

/** A run's or task's status; the words, not only the colour, carry the meaning. */
export function StatusBadge({ status, testId }: { status: string; testId?: string }) {
  return (
    <span
      {...(testId === undefined ? {} : { "data-testid": testId })}
      data-status={status}
      className={`rounded px-2 py-0.5 text-xs ${TONES[status] ?? "bg-slate-100 text-slate-700"}`}
    >
      {status.replace("_", " ")}
    </span>
  );
}
