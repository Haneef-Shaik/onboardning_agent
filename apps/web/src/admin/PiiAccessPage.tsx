/**
 * The PII access log (FR-P5): every reveal and token resolution, newest first. Values are shown only in
 * their masked form, through `MaskedField` with no reveal control; the vault token is never shown.
 */
import { MaskedField } from "@onb/ts-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { listPiiAccess } from "./adminApi";
import { formatWhen } from "./format";
import { Cell, Empty, Loaded, PageTitle, Pager, Table, useAdminClient } from "./parts";

export const PII_ACCESS_PAGE_SIZE = 50;
const HEADERS = ["When", "Kind", "By", "Hire", "Field", "Value", "Reason or sink"] as const;

export function PiiAccessPage() {
  const client = useAdminClient();
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["admin", "pii-access", page],
    queryFn: () => listPiiAccess(client, page, PII_ACCESS_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="flex flex-col gap-4">
      <PageTitle>PII access log</PageTitle>
      <Loaded query={query}>
        {({ items, page: meta }) => (
          <>
            <Table label="PII access log" headers={HEADERS}>
              {items.length === 0 && <Empty columns={HEADERS.length}>No sensitive value has been accessed.</Empty>}
              {items.map((entry) => (
                <tr key={entry.id}>
                  <Cell>{formatWhen(entry.at)}</Cell>
                  <Cell>{entry.kind}</Cell>
                  <Cell>
                    {entry.actor.type}:{entry.actor.id}
                  </Cell>
                  <Cell>{entry.hire_id}</Cell>
                  <Cell>{entry.field}</Cell>
                  <Cell>
                    <MaskedField label={entry.field} masked={entry.masked} />
                  </Cell>
                  <Cell>{entry.reason ?? entry.sink ?? "—"}</Cell>
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
