/**
 * The one component every sensitive field renders through (PRD FR-P1: masked everywhere by default).
 *
 * The API sends a masked form (e.g. `XXXXXX123X`) and, elsewhere, a vault token (`ptk_…`); the raw
 * value reaches the browser only after an audited reveal (FR-P2, UI-4), passed in as `revealed`.
 * A token (`ptk_`, any case, anywhere in the text) is never shown, whichever prop it comes in.
 */
import { cn } from "./cn";
import { Button } from "./components/button";

export const MASK = "••••••••";
const EMPTY = "—";
const TOKEN = /ptk_/i;

/** What to show for a field that is not revealed. */
export function maskedText(masked: string | null | undefined): string {
  const value = masked?.trim() ?? "";
  if (value.length === 0) return EMPTY;
  if (TOKEN.test(value)) return MASK;
  return value;
}

/** A revealed value is shown as is, except that a vault token never is. */
function revealedText(revealed: string): string {
  return TOKEN.test(revealed) ? MASK : revealed;
}

export interface MaskedFieldProps {
  label: string;
  /** The server's masked form of the value, or null when there is none. */
  masked: string | null | undefined;
  /** The plain value, only after an audited reveal. */
  revealed?: string | undefined;
  /** Shows a reveal control; pass it only for roles allowed to reveal this field. */
  onReveal?: (() => void) | undefined;
  onHide?: (() => void) | undefined;
  className?: string | undefined;
}

export function MaskedField({ label, masked, revealed, onReveal, onHide, className }: MaskedFieldProps) {
  const isRevealed = typeof revealed === "string" && revealed.length > 0;
  return (
    <div
      data-testid="masked-field"
      data-state={isRevealed ? "revealed" : "masked"}
      className={cn("flex flex-col gap-1", className)}
    >
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <span className="flex items-center gap-2">
        <span className="font-mono text-sm">{isRevealed ? revealedText(revealed) : maskedText(masked)}</span>
        {isRevealed && onHide && (
          <Button variant="ghost" size="xs" aria-label={`Hide ${label}`} onClick={onHide}>
            Hide
          </Button>
        )}
        {!isRevealed && onReveal && (
          <Button variant="outline" size="xs" aria-label={`Reveal ${label}`} onClick={onReveal}>
            Reveal
          </Button>
        )}
      </span>
    </div>
  );
}
