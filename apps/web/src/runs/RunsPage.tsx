/** The Runs section: every run with its status (newest first), and one run's live board (UI-5). */
import type { ApiClient, Role } from "@onb/ts-api-client";
import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes, useParams } from "react-router";
import { ErrorNotice } from "../components/ErrorNotice";
import { RunDetail } from "./RunDetail";
import { StatusBadge } from "./StatusBadge";
import { listRuns } from "./runs-api";

const LIST_REFRESH_MS = 5000;
const DEFAULT_JAEGER = "https://jaeger.localhost";

/** Jaeger through the proxy (PRD §7.11); `VITE_JAEGER_URL` overrides it, e.g. to carry the proxy's port. */
export const JAEGER_BASE: string = import.meta.env.VITE_JAEGER_URL ?? DEFAULT_JAEGER;

export function RunsPage({ client, role }: { client: ApiClient; role: Role }) {
  return (
    <Routes>
      <Route index element={<RunsList client={client} />} />
      <Route path=":runId" element={<RunRoute client={client} role={role} />} />
    </Routes>
  );
}

function RunRoute({ client, role }: { client: ApiClient; role: Role }) {
  const { runId } = useParams();
  return (
    <div className="flex flex-col gap-4">
      <Link to="/runs" className="text-sm underline">
        ← All runs
      </Link>
      {/* Keyed by id: another run never inherits this one's stream or activity. */}
      {runId && <RunDetail key={runId} client={client} role={role} runId={runId} jaegerBase={JAEGER_BASE} />}
    </div>
  );
}

function RunsList({ client }: { client: ApiClient }) {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => listRuns(client), refetchInterval: LIST_REFRESH_MS });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Runs</h1>
      {runs.isError && <ErrorNotice error={runs.error} />}
      {runs.data?.length === 0 && <p className="text-sm text-slate-500">No runs yet.</p>}
      {runs.data && runs.data.length > 0 && (
        <table aria-label="Runs" className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-slate-500">
              <th className="py-1 pr-4 font-medium">Run</th>
              <th className="py-1 pr-4 font-medium">Hire</th>
              <th className="py-1 pr-4 font-medium">Template</th>
              <th className="py-1 pr-4 font-medium">Status</th>
              <th className="py-1 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.map((run) => (
              <tr key={run.id} data-testid={`run-${run.id}`} className="border-t border-slate-100">
                <td className="py-1 pr-4">
                  <Link to={`/runs/${run.id}`} className="underline">
                    {run.id}
                  </Link>
                </td>
                <td className="py-1 pr-4">{run.hire_id}</td>
                <td className="py-1 pr-4">
                  {run.template_key} v{run.template_version}
                </td>
                <td className="py-1 pr-4">
                  <StatusBadge status={run.status} />
                </td>
                <td className="py-1">
                  <time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleString()}</time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
