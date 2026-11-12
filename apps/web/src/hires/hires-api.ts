/** The hires screens' calls, all through the generated client (PRD §11). */
import {
  unwrap,
  type ApiClient,
  type DocumentMeta,
  type DocumentType,
  type HireRecord,
  type NewHireRequest,
  type PiiField,
  type RevealedValue,
} from "@onb/ts-api-client";

export const DOCUMENT_MEDIA_TYPES = ["application/pdf", "image/jpeg", "image/png"] as const;
export type DocumentMediaType = (typeof DOCUMENT_MEDIA_TYPES)[number];

export const DOCUMENT_TYPES: readonly { value: DocumentType; label: string }[] = [
  { value: "aadhaar", label: "Aadhaar" },
  { value: "pan", label: "PAN" },
  { value: "passport", label: "Passport" },
  { value: "voter_id", label: "Voter ID" },
  { value: "driving_licence", label: "Driving licence" },
];

export function isDocumentMediaType(value: string): value is DocumentMediaType {
  return (DOCUMENT_MEDIA_TYPES as readonly string[]).includes(value);
}

export async function listHires(client: ApiClient): Promise<HireRecord[]> {
  return unwrap(await client.GET("/hires"));
}

export async function getHire(client: ApiClient, hireId: string): Promise<HireRecord> {
  return unwrap(await client.GET("/hires/{hire_id}", { params: { path: { hire_id: hireId } } }));
}

export async function createHire(client: ApiClient, body: NewHireRequest): Promise<HireRecord> {
  return unwrap(await client.POST("/hires", { body }));
}

export async function revealPii(
  client: ApiClient,
  hireId: string,
  field: PiiField,
  reason: string,
): Promise<RevealedValue> {
  return unwrap(
    await client.POST("/hires/{hire_id}/pii/{field}/reveal", {
      params: { path: { hire_id: hireId, field } },
      body: { reason },
    }),
  );
}

export async function listDocuments(client: ApiClient, hireId: string): Promise<DocumentMeta[]> {
  return unwrap(await client.GET("/hires/{hire_id}/documents", { params: { path: { hire_id: hireId } } }));
}

/** The scan goes as the raw body, with its own media type (HIRE-2's upload contract). */
export async function uploadDocument(
  client: ApiClient,
  hireId: string,
  type: DocumentType,
  file: Blob & { type: DocumentMediaType },
): Promise<DocumentMeta> {
  return unwrap(
    await client.POST("/hires/{hire_id}/documents", {
      params: { path: { hire_id: hireId }, query: { type } },
      // The generated body type is a string; the bytes go through untouched.
      body: file as unknown as string,
      bodySerializer: (body) => body,
      headers: { "Content-Type": file.type },
    }),
  );
}
