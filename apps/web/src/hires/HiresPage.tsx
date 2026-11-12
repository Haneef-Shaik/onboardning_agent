/** The Hires section: the list with an add form, and one hire's detail (UI-4). */
import type { ApiClient, Role } from "@onb/ts-api-client";
import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams } from "react-router";
import { ErrorNotice } from "../components/ErrorNotice";
import { HireDetail } from "./HireDetail";
import { NewHireForm } from "./NewHireForm";
import { listHires } from "./hires-api";
import { canManageHires } from "./policy";

export function HiresPage({ client, role }: { client: ApiClient; role: Role }) {
  return (
    <Routes>
      <Route index element={<HiresList client={client} role={role} />} />
      <Route path=":hireId" element={<HireRoute client={client} role={role} />} />
    </Routes>
  );
}

function HireRoute({ client, role }: { client: ApiClient; role: Role }) {
  const { hireId } = useParams();
  return (
    <div className="flex flex-col gap-4">
      <Link to="/hires" className="text-sm underline">
        ← All hires
      </Link>
      {/* Keyed by id: another hire never inherits this one's revealed values. */}
      {hireId && <HireDetail key={hireId} client={client} role={role} hireId={hireId} />}
    </div>
  );
}

function HiresList({ client, role }: { client: ApiClient; role: Role }) {
  const navigate = useNavigate();
  const hires = useQuery({ queryKey: ["hires"], queryFn: () => listHires(client) });
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">Hires</h1>
      {canManageHires(role) && <NewHireForm client={client} onCreated={(hire) => void navigate(`/hires/${hire.id}`)} />}
      {hires.isError && <ErrorNotice error={hires.error} />}
      {hires.data?.length === 0 && <p className="text-sm text-slate-500">No hires yet.</p>}
      {hires.data && hires.data.length > 0 && (
        <table aria-label="Hires" className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-slate-500">
              <th className="py-1 pr-4 font-medium">Name</th>
              <th className="py-1 pr-4 font-medium">Role</th>
              <th className="py-1 pr-4 font-medium">Location</th>
              <th className="py-1 font-medium">Start date</th>
            </tr>
          </thead>
          <tbody>
            {hires.data.map((hire) => (
              <tr key={hire.id} className="border-t border-slate-100">
                <td className="py-1 pr-4">
                  <Link to={`/hires/${hire.id}`} className="underline">
                    {hire.name}
                  </Link>
                </td>
                <td className="py-1 pr-4">{hire.role}</td>
                <td className="py-1 pr-4">{hire.location}</td>
                <td className="py-1">{hire.start_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
