/**
 * Which roles may reveal which sensitive field (PRD §3, FR-P2). It mirrors the API's rule
 * (`POST /hires/{id}/pii/{field}/reveal`: HR and HR_ADMIN, Aadhaar HR_ADMIN only) so the UI never
 * offers a control the API would refuse. The API stays the boundary.
 */
import type { PiiField, Role } from "@onb/ts-api-client";

export interface PiiFieldInfo {
  readonly field: PiiField;
  readonly label: string;
}

export const PII_FIELDS: readonly PiiFieldInfo[] = [
  { field: "email", label: "Email" },
  { field: "mobile", label: "Mobile" },
  { field: "aadhaar", label: "Aadhaar" },
  { field: "pan", label: "PAN" },
  { field: "bank_account", label: "Bank account" },
  { field: "ifsc", label: "IFSC" },
  { field: "account_holder_name", label: "Account holder name" },
];

export function canRevealField(role: Role, field: PiiField): boolean {
  if (role === "HR_ADMIN") return true;
  if (role === "HR") return field !== "aadhaar";
  return false;
}

/** Who may add hires, change sensitive values and upload documents. */
export function canManageHires(role: Role): boolean {
  return role === "HR" || role === "HR_ADMIN";
}
