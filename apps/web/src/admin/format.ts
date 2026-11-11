/** Display helpers for the admin views: the same text in every browser and time zone (UTC). */

/** `2026-11-01T08:00:00Z` → `2026-11-01 08:00 UTC`; an unreadable value is shown as given. */
export function formatWhen(iso: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso;
  return `${moment.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** A 0..1 rate as a percentage with one decimal; null (nothing to rate) as a dash. */
export function formatRate(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${(rate * 100).toFixed(1)}%`;
}
