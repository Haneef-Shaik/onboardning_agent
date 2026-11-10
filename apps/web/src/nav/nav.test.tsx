import type { Role } from "@onb/ts-api-client";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { NAV_ITEMS, ROLES, canAccess, navFor } from "./nav";
import { RoleNav } from "./RoleNav";

afterEach(cleanup);

// The contract (PRD §3): who sees which section. Each later UI goal fills one of these sections.
const EXPECTED: Record<Role, string[]> = {
  HR_ADMIN: ["Runs", "Approvals", "Hires", "Templates", "Change requests", "Admin"],
  HR: ["Runs", "Approvals", "Hires", "Templates", "Change requests"],
  MANAGER: ["Runs", "Approvals", "Templates", "Change requests"],
};

function labelsOf(role: Role): string[] {
  return navFor(role).map((item) => item.label);
}

describe("navFor", () => {
  it.each(ROLES)("gives %s exactly its sections, in order", (role) => {
    expect(labelsOf(role)).toEqual(EXPECTED[role]);
  });

  it("never shows Hires to a manager: managers never see hire PII (PRD §3)", () => {
    expect(labelsOf("MANAGER")).not.toContain("Hires");
  });

  it("shows Admin to HR_ADMIN only", () => {
    expect(ROLES.filter((role) => labelsOf(role).includes("Admin"))).toEqual(["HR_ADMIN"]);
  });

  it("covers every role the API defines", () => {
    expect([...ROLES].sort()).toEqual(["HR", "HR_ADMIN", "MANAGER"]);
  });
});

describe("canAccess", () => {
  it("allows a role into its own sections and their sub-paths", () => {
    expect(canAccess("HR", "/hires")).toBe(true);
    expect(canAccess("HR", "/hires/h_123")).toBe(true);
    expect(canAccess("HR_ADMIN", "/admin/agents")).toBe(true);
  });

  it("refuses a section the role does not have, however the path is written", () => {
    expect(canAccess("MANAGER", "/hires")).toBe(false);
    expect(canAccess("MANAGER", "/hires/h_123")).toBe(false);
    expect(canAccess("HR", "/admin")).toBe(false);
    expect(canAccess("HR", "/admin/")).toBe(false);
    expect(canAccess("MANAGER", "/Admin")).toBe(false);
  });

  it("does not treat a lookalike prefix as the section", () => {
    expect(canAccess("HR", "/runsx")).toBe(false);
  });

  it("refuses an unknown path", () => {
    expect(canAccess("HR_ADMIN", "/nowhere")).toBe(false);
  });

  it("agrees with navFor for every role and section", () => {
    for (const role of ROLES) {
      for (const item of NAV_ITEMS) {
        expect(canAccess(role, item.path)).toBe(labelsOf(role).includes(item.label));
      }
    }
  });
});

describe("RoleNav", () => {
  it.each(ROLES)("renders the links for %s and nothing else", (role) => {
    render(
      <MemoryRouter>
        <RoleNav role={role} />
      </MemoryRouter>,
    );

    const nav = screen.getByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(EXPECTED[role]);
  });

  it("links each section to its path", () => {
    render(
      <MemoryRouter>
        <RoleNav role="HR_ADMIN" />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/admin");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/runs");
  });

  it("marks the current section", () => {
    render(
      <MemoryRouter initialEntries={["/templates/t_1"]}>
        <RoleNav role="HR" />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Templates" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Runs" })).not.toHaveAttribute("aria-current");
  });
});
