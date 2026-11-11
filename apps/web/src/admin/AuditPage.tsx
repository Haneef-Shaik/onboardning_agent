/** The org's audit log (PRD §7.6, §10), newest first, filtered by action, outcome and actor. Read-only. */
import { Button, Input, Label } from "@onb/ts-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { listAudit, type AuditFilters, type AuditOutcome } from "./adminApi";
import { formatWhen } from "./format";
import { Badge, Cell, Empty, Loaded, PageTitle, Pager, Table, useAdminClient } from "./parts";

export const AUDIT_PAGE_SIZE = 50;
const OUTCOMES: readonly AuditOutcome[] = ["success", "denied", "failed"];
const HEADERS = ["When", "Actor", "Action", "Target", "Outcome", "Reason"] as const;

const outcomeTone = (outcome: AuditOutcome) => (outcome === "success" ? "good" : outcome === "denied" ? "bad" : "neutral");

function isOutcome(value: string): value is AuditOutcome {
  return (OUTCOMES as readonly string[]).includes(value);
}

function readFilters(form: HTMLFormElement): AuditFilters {
  const data = new FormData(form);
  const text = (key: string) => String(data.get(key) ?? "").trim() || undefined;
  const outcome = text("outcome");
  return { action: text("action"), actorId: text("actor"), outcome: outcome && isOutcome(outcome) ? outcome : undefined };
}

function FilterForm({ onApply }: { onApply: (filters: AuditFilters) => void }) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onApply(readFilters(event.currentTarget));
  };
  return (
    <form aria-label="Audit filters" onSubmit={submit} className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor="audit-action">Action</Label>
        <Input id="audit-action" name="action" placeholder="mcp.tool_call" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="audit-actor">Actor id</Label>
        <Input id="audit-actor" name="actor" placeholder="db_agent" />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="audit-outcome">Outcome</Label>
        <select id="audit-outcome" name="outcome" className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm">
          <option value="">any</option>
          {OUTCOMES.map((outcome) => (
            <option key={outcome} value={outcome}>
              {outcome}
            </option>
          ))}
        </select>
      </div>
      <Button type="submit">Apply</Button>
    </form>
  );
}

export function AuditPage() {
  const client = useAdminClient();
  const [filters, setFilters] = useState<AuditFilters>({});
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["admin", "audit", filters, page],
    queryFn: () => listAudit(client, filters, page, AUDIT_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const apply = (next: AuditFilters) => {
    setFilters(next);
    setPage(1);
  };
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>Audit log</PageTitle>
      <FilterForm onApply={apply} />
      <Loaded query={query}>
        {({ items, page: meta }) => (
          <>
            <Table label="Audit log" headers={HEADERS}>
              {items.length === 0 && <Empty columns={HEADERS.length}>No audit records match.</Empty>}
              {items.map((record) => (
                <tr key={record.id}>
                  <Cell>{formatWhen(record.at)}</Cell>
                  <Cell>
                    {record.actor.type}:{record.actor.id}
                  </Cell>
                  <Cell>{record.action}</Cell>
                  <Cell>{record.target}</Cell>
                  <Cell>
                    <Badge tone={outcomeTone(record.outcome)}>{record.outcome}</Badge>
                  </Cell>
                  <Cell>{record.reason ?? "—"}</Cell>
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
