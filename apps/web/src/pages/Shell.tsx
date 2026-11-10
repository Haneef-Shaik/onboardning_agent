import type { Me } from "@onb/ts-api-client";
import { Button } from "@onb/ts-ui";
import { Outlet } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { RoleNav } from "../nav/RoleNav";

const ROLE_LABEL: Record<Me["role"], string> = { HR_ADMIN: "HR admin", HR: "HR", MANAGER: "Manager" };

/** The signed-in frame: who you are, your role's nav, and the current section. */
export function Shell({ me }: { me: Me }) {
  const { logout } = useAuth();
  return (
    <div className="flex min-h-screen bg-slate-50">
      <aside className="flex w-56 flex-col gap-6 border-r border-slate-200 bg-white p-4">
        <p className="px-3 text-base font-semibold">Onboarding</p>
        <RoleNav role={me.role} />
      </aside>
      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-end gap-3 border-b border-slate-200 bg-white px-6 py-3 text-sm">
          <span>{me.name}</span>
          <span data-testid="role" className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            {ROLE_LABEL[me.role]}
          </span>
          <Button variant="outline" size="sm" onClick={logout}>
            Sign out
          </Button>
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
