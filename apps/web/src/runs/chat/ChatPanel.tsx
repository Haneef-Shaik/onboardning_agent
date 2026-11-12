/**
 * Ask the run assistant about this run (FR-C1). Answers cite tasks, each a link to that task's row.
 * A proposed retry changes nothing until HR confirms it in a dialog (FR-C2, ADR 0024): the panel never
 * treats words as consent, and a manager gets answers only.
 */
import type { ApiClient, Role } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorNotice } from "../../components/ErrorNotice";
import { canControlRuns } from "../policy";
import { askRun, confirmProposal, getChat } from "./chat-api";
import { ConfirmRetryDialog } from "./ConfirmRetryDialog";
import { MessageView } from "./MessageView";

export interface ChatPanelProps {
  client: ApiClient;
  role: Role;
  runId: string;
  /** Called after a confirmed action changed the run, so the page re-reads it. */
  onRunChanged: () => void;
}

export function ChatPanel({ client, role, runId, onRunChanged }: ChatPanelProps) {
  const queryClient = useQueryClient();
  const key = ["run-chat", runId] as const;
  const history = useQuery({ queryKey: key, queryFn: () => getChat(client, runId) });
  const [question, setQuestion] = useState("");
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const canConfirm = canControlRuns(role);

  const ask = useMutation({
    mutationFn: (text: string) => askRun(client, runId, text),
    onSuccess: () => {
      setQuestion("");
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const confirm = useMutation({
    mutationFn: (proposalId: string) => confirmProposal(client, runId, proposalId),
    onSettled: () => {
      setReviewingId(null);
      void queryClient.invalidateQueries({ queryKey: key });
    },
    onSuccess: () => onRunChanged(),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (text === "" || ask.isPending) return;
    confirm.reset(); // an old confirm error is not about this question
    ask.mutate(text);
  }

  const messages = history.data ?? [];
  // The dialog follows the stored proposal, so one used or expired elsewhere is no longer offered.
  const open = messages.find((m) => m.id === reviewingId)?.proposal;
  const reviewing = open?.status === "pending" ? open : null;
  return (
    <section aria-label="Run assistant" className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
      <h2 className="text-sm font-medium">Ask about this run</h2>
      {history.isError && <ErrorNotice error={history.error} />}
      <ol data-testid="chat-log" className="flex flex-col gap-2">
        {messages.map((message) => (
          <MessageView key={message.id} message={message} canConfirm={canConfirm} onReview={(m) => setReviewingId(m.id)} />
        ))}
      </ol>
      {ask.isPending && <p className="text-xs text-slate-500">Thinking…</p>}
      {ask.isError && <ErrorNotice error={ask.error} />}
      {confirm.isError && <ErrorNotice error={confirm.error} />}
      <form onSubmit={submit} className="flex gap-2">
        <input
          aria-label="Ask about this run"
          className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
          value={question}
          maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <Button type="submit" disabled={ask.isPending || question.trim() === ""}>
          Ask
        </Button>
      </form>
      <p className="text-xs text-slate-500">Do not type personal details such as ID or bank numbers.</p>
      {reviewing && (
        <ConfirmRetryDialog
          proposal={reviewing}
          busy={confirm.isPending}
          onConfirm={() => confirm.mutate(reviewing.proposal_id)}
          onCancel={() => setReviewingId(null)}
        />
      )}
    </section>
  );
}
