/** Building blocks the admin views share: the client, a query's loading/error state, tables, paging. */
import type { ApiClient, PageMeta } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import type { UseQueryResult } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { ErrorNotice } from "../components/ErrorNotice";

const ClientContext = createContext<ApiClient | null>(null);

export function AdminClientProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  return <ClientContext.Provider value={client}>{children}</ClientContext.Provider>;
}

export function useAdminClient(): ApiClient {
  const client = useContext(ClientContext);
  if (client === null) throw new Error("useAdminClient needs an AdminClientProvider");
  return client;
}

/** Renders the data once loaded; meanwhile "Loading…", and an API error as the error notice. */
export function Loaded<T>({ query, children }: { query: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (query.isError) return <ErrorNotice error={query.error} />;
  if (query.data === undefined) return <p className="text-sm text-slate-500">Loading…</p>;
  return <>{children(query.data)}</>;
}

export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className="text-xl font-semibold">{children}</h1>;
}

export function Table({ label, headers, children }: { label: string; headers: readonly string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
      <table aria-label={label} className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase text-slate-500">
          <tr>
            {headers.map((header) => (
              <th key={header} scope="col" className="px-3 py-2 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Cell({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={className ?? "px-3 py-2 align-top"}>{children}</td>;
}

export function Empty({ columns, children }: { columns: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={columns} className="px-3 py-4 text-center text-slate-500">
        {children}
      </td>
    </tr>
  );
}

/** "Showing 1–50 of 120" with previous/next; nothing when everything fits on one page. */
export function Pager({ page, onPage }: { page: PageMeta | null; onPage: (page: number) => void }) {
  if (page === null || page.total === 0) return null;
  const first = (page.page - 1) * page.limit + 1;
  const last = Math.min(page.page * page.limit, page.total);
  const hasNext = page.page * page.limit < page.total;
  return (
    <div className="mt-3 flex items-center gap-3 text-sm text-slate-600">
      <span>
        Showing {first}–{last} of {page.total}
      </span>
      <Button variant="outline" size="sm" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}>
        Previous
      </Button>
      <Button variant="outline" size="sm" disabled={!hasNext} onClick={() => onPage(page.page + 1)}>
        Next
      </Button>
    </div>
  );
}

export function Badge({ tone, children }: { tone: "good" | "bad" | "neutral"; children: ReactNode }) {
  const colors = { good: "bg-emerald-50 text-emerald-800", bad: "bg-red-50 text-red-800", neutral: "bg-slate-100 text-slate-700" };
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${colors[tone]}`}>{children}</span>;
}
