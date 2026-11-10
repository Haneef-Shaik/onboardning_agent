import type { Role } from "@onb/ts-api-client";
import { cn } from "@onb/ts-ui";
import { NavLink } from "react-router";
import { navFor } from "./nav";

export function RoleNav({ role }: { role: Role }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {navFor(role).map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          className={({ isActive }) =>
            cn("rounded-md px-3 py-2 text-sm", isActive ? "bg-slate-200 font-medium" : "hover:bg-slate-100")
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
