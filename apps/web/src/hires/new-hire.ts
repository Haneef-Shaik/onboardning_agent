/** The add-hire form's values, and the request built from them. The API validates every value (PII-3). */
import type { NewHireRequest } from "@onb/ts-api-client";

export type AlternativeId = "" | "passport" | "voter_id" | "driving_licence";

export interface HireFormValues {
  name: string;
  role: string;
  location: string;
  startDate: string;
  managerId: string;
  notes: string;
  email: string;
  mobile: string;
  aadhaar: string;
  aadhaarConsent: string;
  alternativeId: AlternativeId;
  pan: string;
  bankAccount: string;
  ifsc: string;
  accountHolderName: string;
}

export const EMPTY_FORM: HireFormValues = {
  name: "",
  role: "",
  location: "",
  startDate: "",
  managerId: "",
  notes: "",
  email: "",
  mobile: "",
  aadhaar: "",
  aadhaarConsent: "",
  alternativeId: "",
  pan: "",
  bankAccount: "",
  ifsc: "",
  accountHolderName: "",
};

export type FormErrors = Partial<Record<keyof HireFormValues, string>>;
export type BuildResult = { ok: true; request: NewHireRequest } | { ok: false; errors: FormErrors };

const REQUIRED: readonly (keyof HireFormValues)[] = ["name", "role", "location", "startDate", "managerId", "email"];
const REQUIRED_MESSAGE = "Required.";
const CONSENT_MESSAGE = "Say what the Aadhaar is stored for; the hire must have consented.";

export function buildNewHire(values: HireFormValues): BuildResult {
  const v = Object.fromEntries(Object.entries(values).map(([k, x]) => [k, x.trim()])) as unknown as HireFormValues;
  const errors: FormErrors = {};
  for (const key of REQUIRED) if (v[key] === "") errors[key] = REQUIRED_MESSAGE;
  if (v.aadhaar !== "" && v.aadhaarConsent === "") errors.aadhaarConsent = CONSENT_MESSAGE;
  if (Object.keys(errors).length > 0) return { ok: false, errors };

  const optional = {
    mobile: v.mobile,
    aadhaar: v.aadhaar,
    pan: v.pan,
    bank_account: v.bankAccount,
    ifsc: v.ifsc,
    account_holder_name: v.accountHolderName,
  };
  const given = Object.fromEntries(Object.entries(optional).filter(([, x]) => x !== ""));
  const request: NewHireRequest = {
    name: v.name,
    role: v.role,
    location: v.location,
    start_date: v.startDate,
    manager_id: v.managerId,
    notes: v.notes,
    pii: { email: v.email, ...given },
    ...(v.aadhaar !== "" ? { aadhaar_consent: { purpose: v.aadhaarConsent } } : {}),
    ...(v.alternativeId !== "" ? { alternative_id: v.alternativeId } : {}),
  };
  return { ok: true, request };
}
