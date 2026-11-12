/** One hire: details, every sensitive field masked with an audited reveal for the roles allowed one, and documents. */
import type { ApiClient, PiiField, Role } from "@onb/ts-api-client";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label, MaskedField } from "@onb/ts-ui";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useState, type FormEvent } from "react";
import { ErrorNotice } from "../components/ErrorNotice";
import { DocumentsPanel } from "./DocumentsPanel";
import { getHire, revealPii } from "./hires-api";
import { PII_FIELDS, canManageHires, canRevealField } from "./policy";
import { useReveal } from "./useReveal";

interface HireDetailProps {
  client: ApiClient;
  role: Role;
  hireId: string;
}

export function HireDetail({ client, role, hireId }: HireDetailProps) {
  const hire = useQuery({ queryKey: ["hires", hireId], queryFn: () => getHire(client, hireId) });
  const revealFn = useCallback((field: PiiField, reason: string) => revealPii(client, hireId, field, reason), [client, hireId]);
  const { values, reveal, hide } = useReveal(revealFn);
  const [asking, setAsking] = useState<PiiField | null>(null);

  if (hire.isError) return <ErrorNotice error={hire.error} />;
  if (!hire.data) return <p className="text-sm text-slate-500">Loading…</p>;
  const record = hire.data;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-xl font-semibold">{record.name}</h1>
        <p className="text-sm text-slate-500">
          {record.role} · {record.location} · starts {record.start_date}
        </p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Sensitive details</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {PII_FIELDS.map(({ field, label }) => (
            <div key={field} className="flex flex-col gap-2">
              <MaskedField
                label={label}
                masked={record.pii[field]}
                revealed={values[field]}
                onReveal={canRevealField(role, field) ? () => setAsking(field) : undefined}
                onHide={() => hide(field)}
              />
              {asking === field && (
                <ReasonForm
                  label={label}
                  onCancel={() => setAsking(null)}
                  onConfirm={async (reason) => {
                    await reveal(field, reason);
                    setAsking(null);
                  }}
                />
              )}
            </div>
          ))}
        </CardContent>
      </Card>
      <DocumentsPanel client={client} hireId={hireId} canUpload={canManageHires(role)} />
    </div>
  );
}

function ReasonForm({
  label,
  onConfirm,
  onCancel,
}: {
  label: string;
  onConfirm: (reason: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const id = `reason-${label.replace(/\s+/g, "-").toLowerCase()}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (reason.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
    } catch (caught) {
      setError(caught);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-md border border-slate-200 p-3">
      <Label htmlFor={id}>{`Reason for revealing ${label}`}</Label>
      <Input id={id} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
      <p className="text-xs text-slate-500">The reveal is recorded in the access log and hides again after 30 seconds.</p>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || reason.trim() === ""}>
          Confirm reveal
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {error !== null && <ErrorNotice error={error} />}
    </form>
  );
}
