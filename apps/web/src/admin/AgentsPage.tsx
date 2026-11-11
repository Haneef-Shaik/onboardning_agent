/** Every registered agent, with the connector and model it runs on and how many tools it may call (FR-A2). */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { listAgents, type AgentRecord } from "./adminApi";
import { Badge, Cell, Empty, Loaded, PageTitle, Table, useAdminClient } from "./parts";

/** The connector and model an agent resolves to, or its manifest's own choice when unresolved. */
export function modelOf(agent: AgentRecord): { connector: string; model: string } {
  const resolved = agent.resolved_llm;
  if (resolved) return { connector: resolved.connector, model: resolved.model };
  return { connector: agent.llm.connector ?? "—", model: agent.llm.model ?? `alias: ${agent.llm.alias ?? "—"}` };
}

/** `server.tool` for every MCP tool the manifest grants. */
export function mcpToolsOf(agent: AgentRecord): string[] {
  return Object.entries(agent.mcp).flatMap(([server, grant]) => grant.tools.map((tool) => `${server}.${tool}`));
}

const HEADERS = ["Agent", "Version", "Status", "Connector", "Model", "MCP tools"] as const;

export function AgentsPage() {
  const client = useAdminClient();
  const query = useQuery({ queryKey: ["admin", "agents"], queryFn: () => listAgents(client) });
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>Agents</PageTitle>
      <Loaded query={query}>
        {(agents) => (
          <Table label="Agents" headers={HEADERS}>
            {agents.length === 0 && <Empty columns={HEADERS.length}>No agents are registered.</Empty>}
            {agents.map((agent) => {
              const { connector, model } = modelOf(agent);
              return (
                <tr key={agent.name}>
                  <Cell>
                    <Link className="font-medium text-blue-700 hover:underline" to={`/admin/agents/${agent.name}`}>
                      {agent.name}
                    </Link>
                  </Cell>
                  <Cell>v{agent.version}</Cell>
                  <Cell>{agent.active ? <Badge tone="good">active</Badge> : <Badge tone="neutral">retired</Badge>}</Cell>
                  <Cell>{connector}</Cell>
                  <Cell>{model}</Cell>
                  <Cell>{mcpToolsOf(agent).length}</Cell>
                </tr>
              );
            })}
          </Table>
        )}
      </Loaded>
    </section>
  );
}
