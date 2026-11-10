/**
 * Role-based navigation (PRD §3). One table decides both what the nav shows and which routes a role
 * may open, so the two cannot drift. It is a convenience, not the security boundary: the API checks
 * the token's role on every route (AUTH-1's role matrix).
 */
import type { Role } from "@onb/ts-api-client";

export const ROLES: readonly Role[] = ["HR_ADMIN", "HR", "MANAGER"];

export interface NavItem {
  readonly label: string;
  readonly path: string;
  readonly roles: readonly Role[];
  /** The goal that builds the screen behind this section. */
  readonly goal: string;
}

const ALL: readonly Role[] = ROLES;
const HR_STAFF: readonly Role[] = ["HR_ADMIN", "HR"];

export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Runs", path: "/runs", roles: ALL, goal: "UI-5" },
  { label: "Approvals", path: "/approvals", roles: ALL, goal: "UI-6" },
  // Managers never see hire PII (PRD §3), so they have no hires section.
  { label: "Hires", path: "/hires", roles: HR_STAFF, goal: "UI-4" },
  { label: "Templates", path: "/templates", roles: ALL, goal: "UI-2" },
  { label: "Change requests", path: "/change-requests", roles: ALL, goal: "UI-3" },
  { label: "Admin", path: "/admin", roles: ["HR_ADMIN"], goal: "UI-8" },
];

export function navFor(role: Role): NavItem[] {
  return NAV_ITEMS.filter((item) => item.roles.includes(role));
}

function inSection(pathname: string, sectionPath: string): boolean {
  return pathname === sectionPath || pathname.startsWith(`${sectionPath}/`);
}

/** The nav section a path belongs to, if any. Paths are matched exactly (case-sensitive). */
export function sectionOf(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => inSection(pathname, item.path));
}

/** Whether `role` may open `pathname`; an unknown path is refused. */
export function canAccess(role: Role, pathname: string): boolean {
  const section = sectionOf(pathname);
  return section !== undefined && section.roles.includes(role);
}
