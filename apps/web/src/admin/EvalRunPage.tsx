/** One eval run: its gates, metrics per agent and category, and each case's passes. Read-only. */
import { Card, CardContent, CardHeader, CardTitle } from "@onb/ts-ui";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { getEvalRun, type EvalRun } from "./adminApi";
import { GatesBadge } from "./EvalRunsPage";
import { formatRate, formatWhen } from "./format";
import { Badge, Cell, Empty, Loaded, PageTitle, Table, useAdminClient } from "./parts";

function GatesCard({ run }: { run: EvalRun }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Gates</CardTitle>
      </CardHeader>
      <CardContent>
        <Table label="Gates" headers={["Gate", "Agent", "Result", "Detail"]}>
          {run.gates.length === 0 && <Empty columns={4}>No gates.</Empty>}
          {run.gates.map((gate) => (
            <tr key={`${gate.agent}/${gate.name}`}>
              <Cell>{gate.name}</Cell>
              <Cell>{gate.agent}</Cell>
              <Cell>
                <GatesBadge passed={gate.passed} />
              </Cell>
              <Cell>{gate.detail}</Cell>
            </tr>
          ))}
        </Table>
      </CardContent>
    </Card>
  );
}

const METRIC_HEADERS = ["Agent", "Category", "Cases", "pass@1", "pass^k", "Tool calls", "Steps", "p95 ms"] as const;

function MetricsCard({ run }: { run: EvalRun }) {
  const rows = Object.entries(run.metrics).flatMap(([agent, groups]) =>
    Object.entries(groups).map(([category, metrics]) => ({ agent, category, metrics })),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Metrics</CardTitle>
      </CardHeader>
      <CardContent>
        <Table label="Metrics" headers={METRIC_HEADERS}>
          {rows.length === 0 && <Empty columns={METRIC_HEADERS.length}>No metrics.</Empty>}
          {rows.map(({ agent, category, metrics }) => (
            <tr key={`${agent}/${category}`}>
              <Cell>{agent}</Cell>
              <Cell>{category}</Cell>
              <Cell>{metrics.cases}</Cell>
              <Cell>{formatRate(metrics.pass_at_1)}</Cell>
              <Cell>{formatRate(metrics.pass_hat_k)}</Cell>
              <Cell>{formatRate(metrics.tool_call_accuracy)}</Cell>
              <Cell>{metrics.avg_steps}</Cell>
              <Cell>{metrics.p95_latency_ms}</Cell>
            </tr>
          ))}
        </Table>
      </CardContent>
    </Card>
  );
}

function CasesCard({ run }: { run: EvalRun }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cases</CardTitle>
      </CardHeader>
      <CardContent>
        <Table label="Cases" headers={["Case", "Agent", "Category", "Passes"]}>
          {run.cases.length === 0 && <Empty columns={4}>No cases.</Empty>}
          {run.cases.map((c) => (
            <tr key={c.case_id}>
              <Cell>{c.case_id}</Cell>
              <Cell>{c.agent}</Cell>
              <Cell>{c.category}</Cell>
              <Cell>
                <Badge tone={c.passes === c.repetitions ? "good" : "bad"}>
                  {c.passes}/{c.repetitions}
                </Badge>
              </Cell>
            </tr>
          ))}
        </Table>
      </CardContent>
    </Card>
  );
}

export function EvalRunPage() {
  const runId = useParams()["runId"] ?? "";
  const client = useAdminClient();
  const query = useQuery({ queryKey: ["admin", "eval-run", runId], queryFn: () => getEvalRun(client, runId) });
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>Eval run</PageTitle>
      <Loaded query={query}>
        {(run) => (
          <>
            <p className="text-sm text-slate-600">
              <span className="font-mono">{run.id}</span> · {run.mode} · {run.agent} on {run.llm} · {run.dataset} /{" "}
              {run.fixture} · ×{run.repeat} · {formatWhen(run.started_at)} · <GatesBadge passed={run.passed} />
            </p>
            <GatesCard run={run} />
            <MetricsCard run={run} />
            <CasesCard run={run} />
          </>
        )}
      </Loaded>
    </section>
  );
}
