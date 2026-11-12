/** Add a hire with its sensitive fields (FR-P4, U3). The values go to the API once; nothing is kept here after. */
import type { ApiClient, HireRecord } from "@onb/ts-api-client";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "@onb/ts-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorNotice } from "../components/ErrorNotice";
import { createHire } from "./hires-api";
import { EMPTY_FORM, buildNewHire, type AlternativeId, type FormErrors, type HireFormValues } from "./new-hire";

interface FieldSpec {
  key: keyof HireFormValues;
  label: string;
  type?: string;
  autoComplete?: string;
}

const DETAILS: FieldSpec[] = [
  { key: "name", label: "Name" },
  { key: "role", label: "Role" },
  { key: "location", label: "Location" },
  { key: "startDate", label: "Start date", type: "date" },
  { key: "managerId", label: "Manager ID" },
];

const SENSITIVE: FieldSpec[] = [
  { key: "email", label: "Email", type: "email" },
  { key: "mobile", label: "Mobile" },
  { key: "aadhaar", label: "Aadhaar" },
  { key: "pan", label: "PAN" },
  { key: "bankAccount", label: "Bank account" },
  { key: "ifsc", label: "IFSC" },
  { key: "accountHolderName", label: "Account holder name" },
];

export function NewHireForm({ client, onCreated }: { client: ApiClient; onCreated: (hire: HireRecord) => void }) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<HireFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FormErrors>({});
  const create = useMutation({
    mutationFn: (request: Parameters<typeof createHire>[1]) => createHire(client, request),
    onSuccess: async (hire) => {
      setValues(EMPTY_FORM); // the typed values do not outlive the request
      await queryClient.invalidateQueries({ queryKey: ["hires"] });
      onCreated(hire);
    },
  });

  const set = (key: keyof HireFormValues, value: string) => setValues((current) => ({ ...current, [key]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    const built = buildNewHire(values);
    if (!built.ok) {
      setErrors(built.errors);
      return;
    }
    setErrors({});
    create.mutate(built.request);
  }

  const field = ({ key, label, type }: FieldSpec) => (
    <div key={key} className="flex flex-col gap-1">
      <Label htmlFor={`hire-${key}`}>{label}</Label>
      <Input
        id={`hire-${key}`}
        type={type ?? "text"}
        value={values[key]}
        onChange={(e) => set(key, e.target.value)}
        autoComplete="off"
        aria-invalid={errors[key] !== undefined}
        aria-describedby={errors[key] ? `hire-${key}-error` : undefined}
      />
      {errors[key] && (
        <p id={`hire-${key}-error`} className="text-xs text-red-700">
          {errors[key]}
        </p>
      )}
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a hire</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-6" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">{DETAILS.map(field)}</div>
          <fieldset className="flex flex-col gap-4">
            <legend className="text-sm font-medium">Sensitive details</legend>
            <div className="grid gap-4 sm:grid-cols-2">{SENSITIVE.map(field)}</div>
            {values.aadhaar.trim() !== "" && field({ key: "aadhaarConsent", label: "Aadhaar consent purpose" })}
            <div className="flex flex-col gap-1">
              <Label htmlFor="hire-alternativeId">ID given instead of Aadhaar</Label>
              <select
                id="hire-alternativeId"
                value={values.alternativeId}
                onChange={(e) => set("alternativeId", e.target.value as AlternativeId)}
                className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm"
              >
                <option value="">None</option>
                <option value="passport">Passport</option>
                <option value="voter_id">Voter ID</option>
                <option value="driving_licence">Driving licence</option>
              </select>
            </div>
          </fieldset>
          {create.isError && <ErrorNotice error={create.error} />}
          <div>
            <Button type="submit" disabled={create.isPending}>
              Create hire
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
