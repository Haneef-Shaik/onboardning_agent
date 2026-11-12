/**
 * The Approvals Inbox (FR-H2, U5): each approval the signed-in user may decide, with the AI-prepared packet
 * (masked), approved or rejected in one action. The first decision wins: a later one says "already decided".
 */
import type { ApiClient } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { ErrorNotice } from "../components/ErrorNotice";
import { StatusBadge } from "../runs/StatusBadge";
import {
  ALREADY_DECIDED,
  decideApproval,
  isAlreadyDecided,
  listMyApprovals,
  type ApprovalRecord,
  type Decision,
} from "./approvals-api";

const REFRESH_MS = 3000;
export const APPROVALS_KEY = ["approvals", "mine"] as const;

export function ApprovalsInbox({ client }: { client: ApiClient }) {
  const approvals = useQuery({
    queryKey: APPROVALS_KEY,
    queryFn: () => listMyApprovals(client),
    refetchInterval: REFRESH_MS,
  });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Approvals</h1>
      {approvals.isError && <ErrorNotice error={approvals.error} />}
      {approvals.data?.length === 0 && <p className="text-sm text-slate-500">No approvals for you.</p>}
      {approvals.data?.map((approval) => (
        <ApprovalCard key={approval.id} client={client} approval={approval} />
      ))}
    </div>
  );
}

function ApprovalCard({ client, approval }: { client: ApiClient; approval: ApprovalRecord }) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: Decision) => decideApproval(client, approval.id, decision, comment),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  });
  const pending = approval.status === "pending";
  const commentId = `comment-${approval.id}`;

  return (
    <article data-testid={`approval-${approval.id}`} className="rounded-md border border-slate-200 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-semibold">{approval.title}</h2>
        <StatusBadge status={approval.status} testId="approval-status" />
      </header>
      <p className="mt-1 text-sm text-slate-600">
        Hire {approval.hire_id} · run{" "}
        <Link to={`/runs/${approval.run_id}`} className="underline">
          {approval.run_id}
        </Link>{" "}
        · task {approval.task_id}
      </p>
      <Why approval={approval} />
      <p className="mt-2 text-sm">Items: {approval.items.length > 0 ? approval.items.join(", ") : "none"}</p>
      <Packet approval={approval} />

      {approval.decision && (
        <p data-testid="approval-decision" className="mt-2 text-sm">
          {approval.decision.decision === "approve" ? "Approved" : "Rejected"} by {approval.decision.decided_by} via{" "}
          {approval.decision.channel}
          {approval.decision.comment ? `: “${approval.decision.comment}”` : ""}
        </p>
      )}

      {pending && !decide.isSuccess && (
        <div className="mt-3 flex flex-col gap-2">
          <label htmlFor={commentId} className="text-sm">
            Comment (optional)
          </label>
          <textarea
            id={commentId}
            value={comment}
            maxLength={1000}
            onChange={(event) => setComment(event.target.value)}
            className="rounded border border-slate-300 p-2 text-sm"
          />
          <div className="flex gap-2">
            <Button disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
              Approve
            </Button>
            <Button variant="outline" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
              Reject
            </Button>
          </div>
        </div>
      )}
      {decide.isSuccess && (
        <p role="status" className="mt-2 text-sm text-green-800">
          Decision recorded. The run goes on.
        </p>
      )}
      {decide.isError &&
        (isAlreadyDecided(decide.error) ? (
          <p role="alert" className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
            {ALREADY_DECIDED}
          </p>
        ) : (
          <div className="mt-2">
            <ErrorNotice error={decide.error} />
          </div>
        ))}
    </article>
  );
}

function Why({ approval }: { approval: ApprovalRecord }) {
  if (approval.kind === "tool_call") {
    return (
      <p className="mt-1 text-sm text-amber-900">
        An agent&apos;s call was refused: approving grants the rule {approval.rule} for the whole task.
      </p>
    );
  }
  if (approval.kind === "needs_human") {
    return <p className="mt-1 text-sm">The agent asked for a human: approving lets the task go on.</p>;
  }
  return null;
}

function Packet({ approval }: { approval: ApprovalRecord }) {
  const packet = approval.packet;
  if (!packet) {
    return <p className="mt-2 text-sm text-slate-500">No AI summary is available: please review the request yourself.</p>;
  }
  return (
    <div className="mt-2 rounded bg-slate-50 p-3 text-sm">
      <p>{packet.summary}</p>
      <p className="mt-1">AI recommendation: {packet.recommendation}</p>
      {packet.risk_flags && packet.risk_flags.length > 0 && <p>Risk flags: {packet.risk_flags.join(", ")}</p>}
      {packet.cited_policies && packet.cited_policies.length > 0 && (
        <p>Policies cited: {packet.cited_policies.join(", ")}</p>
      )}
    </div>
  );
}
