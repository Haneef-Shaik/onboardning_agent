import type { ChatMessageRecord } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";

export interface MessageViewProps {
  message: ChatMessageRecord;
  canConfirm: boolean;
  onReview: (message: ChatMessageRecord) => void;
}

/** Takes the reader to the cited task's row on the run page (the row has `id="task-<id>"`). */
function showTask(event: React.MouseEvent<HTMLAnchorElement>, taskId: string) {
  const row = document.getElementById(`task-${taskId}`);
  if (!row) return;
  event.preventDefault();
  row.scrollIntoView?.({ block: "center" });
  row.focus();
}

function proposalNote(message: ChatMessageRecord): string | null {
  const status = message.proposal?.status;
  if (status === "confirmed") return "Retry confirmed";
  if (status === "failed") return "This proposal is no longer available (used, failed or expired).";
  return null;
}

export function MessageView({ message, canConfirm, onReview }: MessageViewProps) {
  if (message.role === "user") {
    return (
      <li data-testid="chat-question" className="self-end rounded bg-slate-100 px-2 py-1 text-sm">
        {message.content}
      </li>
    );
  }
  const proposal = message.proposal;
  const citations = message.citations ?? [];
  const note = proposalNote(message);
  return (
    <li data-testid="chat-answer" className="rounded border border-slate-200 px-2 py-1 text-sm">
      <p>{message.content}</p>
      {message.status !== null && message.status !== undefined && message.status !== "ok" && (
        <p className="text-xs text-amber-800">
          {message.status === "refused" ? "The assistant declined to answer." : "This needs a person to look at it."}
        </p>
      )}
      {citations.length > 0 && (
        <p className="mt-1 text-xs text-slate-600">
          Tasks cited:{" "}
          {citations.map((citation) => (
            <a
              key={`${citation.run_id}/${citation.task_id}`}
              href={`#task-${citation.task_id}`}
              className="mr-2 underline"
              onClick={(event) => showTask(event, citation.task_id)}
            >
              {citation.task_id}
            </a>
          ))}
        </p>
      )}
      {proposal && note && <p className="mt-1 text-xs text-slate-600">{note}</p>}
      {proposal && proposal.status === "pending" && canConfirm && (
        <Button className="mt-1" variant="outline" onClick={() => onReview(message)}>
          Review proposed retry
        </Button>
      )}
    </li>
  );
}
