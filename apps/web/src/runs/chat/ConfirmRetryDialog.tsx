import type { ChatProposal } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { useEffect, useRef } from "react";

export interface ConfirmRetryDialogProps {
  proposal: ChatProposal;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** The one place a chat proposal becomes an action: nothing is sent until "Confirm retry" is pressed. */
export function ConfirmRetryDialog({ proposal, busy, onConfirm, onCancel }: ConfirmRetryDialogProps) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-slate-900/40">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-retry-title"
        aria-describedby="confirm-retry-body"
        className="w-full max-w-sm rounded-md bg-white p-4 shadow"
        onKeyDown={(event) => event.key === "Escape" && onCancel()}
      >
        <h3 id="confirm-retry-title" className="text-sm font-semibold">
          Retry task {proposal.task_id}?
        </h3>
        <p id="confirm-retry-body" className="mt-1 text-sm text-slate-700">
          The assistant proposes retrying task {proposal.task_id}. Nothing happens until you confirm. This is recorded
          in the audit log under your name.
        </p>
        <div className="mt-3 flex justify-end gap-2">
          <Button ref={cancel} variant="outline" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button disabled={busy} onClick={onConfirm}>
            Confirm retry
          </Button>
        </div>
      </div>
    </div>
  );
}
