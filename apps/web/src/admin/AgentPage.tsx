/**
 * One agent (FR-A2, U8): its connector and model, the MCP tools and collections it may use, its local
 * tools, approvals and limits, and its recent denied tool calls from the audit log.
 */
import { Card, CardContent, CardHeader, CardTitle } from "@onb/ts-ui";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useParams } from "react-router";
import { getAgent, listRecentDenials, RECENT_DENIALS, type AgentRecord } from "./adminApi";
import { modelOf } from "./AgentsPage";
import { formatWhen } from "./format";
import { Badge, Cell, Empty, Loaded, PageTitle, Table, useAdminClient } from "./parts";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

const listOrNone = (items: readonly string[]) => (items.length === 0 ? "none" : items.join(", "));

function ModelCard({ agent }: { agent: AgentRecord }) {
  const { connector, model } = modelOf(agent);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Model</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Fact label="Connector">
            <span data-testid="agent-connector">{connector}</span>
          </Fact>
          <Fact label="Model">
            <span data-testid="agent-model">{model}</span>
          </Fact>
          <Fact label="Alias">{agent.resolved_llm?.alias ?? agent.llm.alias ?? "—"}</Fact>
          <Fact label="Chosen by">{agent.resolved_llm?.source ?? "manifest"}</Fact>
        </dl>
      </CardContent>
    </Card>
  );
}

function ToolsCard({ agent }: { agent: AgentRecord }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Allowed tools</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Table label="Allowed MCP tools" headers={["Server", "Tools", "Reads", "Writes"]}>
          {Object.entries(agent.mcp).map(([server, grant]) => (
            <tr key={server}>
              <Cell>{server}</Cell>
              <Cell>{listOrNone(grant.tools)}</Cell>
              <Cell>{grant.collections ? listOrNone(grant.collections.read) : "—"}</Cell>
              <Cell>{grant.collections ? listOrNone(grant.collections.write) : "—"}</Cell>
            </tr>
          ))}
        </Table>
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Fact label="Local tools">{listOrNone(agent.tools)}</Fact>
          <Fact label="Needs approval">{listOrNone(agent.requires_approval)}</Fact>
          <Fact label="Limits">
            {agent.limits.max_steps} steps · {agent.limits.max_tokens} tokens · {agent.limits.timeout_s}s
          </Fact>
        </dl>
      </CardContent>
    </Card>
  );
}

function DenialsCard({ name }: { name: string }) {
  const client = useAdminClient();
  const query = useQuery({ queryKey: ["admin", "denials", name], queryFn: () => listRecentDenials(client, name) });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent denials</CardTitle>
        <p className="text-sm text-slate-500">The last {RECENT_DENIALS} tool calls the MCP hub refused this agent.</p>
      </CardHeader>
      <CardContent>
        <Loaded query={query}>
          {({ items, page }) => (
            <>
              <Table label="Recent denials" headers={["When", "Tool", "Reason"]}>
                {items.length === 0 && <Empty columns={3}>No denied calls.</Empty>}
                {items.map((record) => (
                  <tr key={record.id}>
                    <Cell>{formatWhen(record.at)}</Cell>
                    <Cell>{record.target}</Cell>
                    <Cell>{record.reason ?? "—"}</Cell>
                  </tr>
                ))}
              </Table>
              {page !== null && page.total > items.length && (
                <p className="mt-2 text-xs text-slate-500">{page.total} denials in all; see the audit log.</p>
              )}
            </>
          )}
        </Loaded>
      </CardContent>
    </Card>
  );
}

export function AgentPage() {
  const name = useParams()["name"] ?? "";
  const client = useAdminClient();
  const query = useQuery({ queryKey: ["admin", "agent", name], queryFn: () => getAgent(client, name) });
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>Agent {name}</PageTitle>
      <Loaded query={query}>
        {(agent) => (
          <>
            <p className="text-sm text-slate-600">
              {agent.purpose} · v{agent.version} ·{" "}
              {agent.active ? <Badge tone="good">active</Badge> : <Badge tone="neutral">retired</Badge>}
            </p>
            <ModelCard agent={agent} />
            <ToolsCard agent={agent} />
            <DenialsCard name={agent.name} />
          </>
        )}
      </Loaded>
    </section>
  );
}
