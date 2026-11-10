import type { NavItem } from "../nav/nav";

/** A section's placeholder until its goal builds the screen. */
export function SectionPage({ item }: { item: NavItem }) {
  return (
    <section>
      <h1 className="text-xl font-semibold">{item.label}</h1>
      <p className="mt-2 text-sm text-slate-500">This screen arrives with {item.goal}.</p>
    </section>
  );
}

export function NotAvailablePage() {
  return (
    <section>
      <h1 className="text-xl font-semibold">Not available</h1>
      <p className="mt-2 text-sm text-slate-500">This page is not part of your role, or does not exist.</p>
    </section>
  );
}
