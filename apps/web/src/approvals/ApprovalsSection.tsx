/** The Approvals section (UI-6; PRD FR-H2, FR-N2, U5): the web inbox and the mock Telegram outbox. */
import type { ApiClient, Me } from "@onb/ts-api-client";
import { NavLink, Route, Routes } from "react-router";
import { ApprovalsInbox } from "./ApprovalsInbox";
import { MockOutbox } from "./MockOutbox";

export interface ApprovalsSectionProps {
  client: ApiClient;
  me: Me;
  /** Where the section is mounted ("/approvals"); the view links are built on it. */
  basePath: string;
}

const tab = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2 py-1 text-sm ${isActive ? "bg-slate-200 font-medium" : "underline"}`;

export function ApprovalsSection({ client, me, basePath }: ApprovalsSectionProps) {
  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Approvals views" className="flex gap-2">
        <NavLink to={basePath} end className={tab}>
          Inbox
        </NavLink>
        <NavLink to={`${basePath}/outbox`} className={tab}>
          Mock Telegram outbox
        </NavLink>
      </nav>
      <Routes>
        <Route index element={<ApprovalsInbox client={client} />} />
        <Route path="outbox" element={<MockOutbox client={client} me={me} />} />
      </Routes>
    </div>
  );
}
