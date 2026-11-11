/**
 * The Admin section (UI-8; HR_ADMIN only, by the nav table's guard in front of it and the API's role
 * matrix behind it): agents, the audit log, the PII access log and eval runs, all read-only.
 */
import type { ApiClient } from "@onb/ts-api-client";
import { cn } from "@onb/ts-ui";
import { Navigate, NavLink, Route, Routes } from "react-router";
import { NotAvailablePage } from "../pages/SectionPage";
import { AgentPage } from "./AgentPage";
import { AgentsPage } from "./AgentsPage";
import { AuditPage } from "./AuditPage";
import { EvalRunPage } from "./EvalRunPage";
import { EvalRunsPage } from "./EvalRunsPage";
import { AdminClientProvider } from "./parts";
import { PiiAccessPage } from "./PiiAccessPage";

export const ADMIN_VIEWS = [
  { label: "Agents", path: "/admin/agents" },
  { label: "Audit log", path: "/admin/audit" },
  { label: "PII access log", path: "/admin/pii-access" },
  { label: "Eval runs", path: "/admin/evals" },
] as const;

function AdminNav() {
  return (
    <nav aria-label="Admin" className="mb-6 flex gap-1 border-b border-slate-200">
      {ADMIN_VIEWS.map((view) => (
        <NavLink
          key={view.path}
          to={view.path}
          className={({ isActive }) =>
            cn("-mb-px border-b-2 px-3 py-2 text-sm", isActive ? "border-slate-900 font-medium" : "border-transparent hover:text-slate-900")
          }
        >
          {view.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function AdminSection({ client }: { client: ApiClient }) {
  return (
    <AdminClientProvider client={client}>
      <AdminNav />
      <Routes>
        <Route index element={<Navigate to="agents" replace />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:name" element={<AgentPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="pii-access" element={<PiiAccessPage />} />
        <Route path="evals" element={<EvalRunsPage />} />
        <Route path="evals/:runId" element={<EvalRunPage />} />
        <Route path="*" element={<NotAvailablePage />} />
      </Routes>
    </AdminClientProvider>
  );
}
