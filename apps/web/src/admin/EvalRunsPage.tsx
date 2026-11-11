/** The org's eval runs (PRD §9, §10 Evals), newest first. Read-only. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { listEvalRuns } from "./adminApi";
import { formatWhen } from "./format";
import { Badge, Cell, Empty, Loaded, PageTitle, Pager, Table, useAdminClient } from "./parts";

export const EVAL_RUNS_PAGE_SIZE = 20;
const HEADERS = ["Run", "Started", "Mode", "Agent", "Model", "Repeat", "Gates"] as const;

export function GatesBadge({ passed }: { passed: boolean }) {
  return passed ? <Badge tone="good">passed</Badge> : <Badge tone="bad">failed</Badge>;
}

export function EvalRunsPage() {
  const client = useAdminClient();
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["admin", "eval-runs", page],
    queryFn: () => listEvalRuns(client, page, EVAL_RUNS_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>Eval runs</PageTitle>
      <Loaded query={query}>
        {({ items, page: meta }) => (
          <>
            <Table label="Eval runs" headers={HEADERS}>
              {items.length === 0 && <Empty columns={HEADERS.length}>No eval runs yet.</Empty>}
              {items.map((run) => (
                <tr key={run.id}>
                  <Cell>
                    <Link className="font-mono text-xs text-blue-700 hover:underline" to={`/admin/evals/${run.id}`}>
                      {run.id}
                    </Link>
                  </Cell>
                  <Cell>{formatWhen(run.started_at)}</Cell>
                  <Cell>{run.mode}</Cell>
                  <Cell>{run.agent}</Cell>
                  <Cell>{run.llm}</Cell>
                  <Cell>×{run.repeat}</Cell>
                  <Cell>
                    <GatesBadge passed={run.passed} />
                  </Cell>
                </tr>
              ))}
            </Table>
            <Pager page={meta} onPage={setPage} />
          </>
        )}
      </Loaded>
    </section>
  );
}
