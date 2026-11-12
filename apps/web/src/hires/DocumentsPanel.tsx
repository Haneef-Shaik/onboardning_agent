/** A hire's scans: metadata only (type, upload time, verified flag) and, for HR, the upload form (FR-P3). */
import type { ApiClient, DocumentType } from "@onb/ts-api-client";
import { Button, Card, CardContent, CardHeader, CardTitle, Label } from "@onb/ts-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ErrorNotice } from "../components/ErrorNotice";
import { DOCUMENT_TYPES, isDocumentMediaType, listDocuments, uploadDocument } from "./hires-api";

const TYPE_LABEL = new Map(DOCUMENT_TYPES.map((t) => [t.value, t.label]));
const WRONG_FILE = "Choose a PDF, JPEG or PNG file.";

const formatTime = (iso: string) =>
  new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(iso)) + " UTC";

export function DocumentsPanel({ client, hireId, canUpload }: { client: ApiClient; hireId: string; canUpload: boolean }) {
  const queryClient = useQueryClient();
  const key = ["hires", hireId, "documents"] as const;
  const documents = useQuery({ queryKey: key, queryFn: () => listDocuments(client, hireId) });
  const [type, setType] = useState<DocumentType>("pan");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileKey, setFileKey] = useState(0);

  const upload = useMutation({
    mutationFn: (scan: File & { type: Parameters<typeof uploadDocument>[3]["type"] }) =>
      uploadDocument(client, hireId, type, scan),
    onSuccess: async () => {
      setFile(null);
      setFileKey((n) => n + 1);
      await queryClient.invalidateQueries({ queryKey: key });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    upload.reset();
    if (file === null || !isDocumentMediaType(file.type)) {
      setFileError(WRONG_FILE);
      return;
    }
    setFileError(null);
    upload.mutate(file as File & { type: Parameters<typeof uploadDocument>[3]["type"] });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {documents.isError && <ErrorNotice error={documents.error} />}
        {documents.data?.length === 0 && <p className="text-sm text-slate-500">No documents yet.</p>}
        {documents.data && documents.data.length > 0 && (
          <table aria-label="Documents" className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-500">
                <th className="py-1 pr-4 font-medium">Type</th>
                <th className="py-1 pr-4 font-medium">Uploaded</th>
                <th className="py-1 font-medium">Verified</th>
              </tr>
            </thead>
            <tbody>
              {documents.data.map((doc) => (
                <tr key={doc.id} className="border-t border-slate-100">
                  <td className="py-1 pr-4">{TYPE_LABEL.get(doc.type) ?? doc.type}</td>
                  <td className="py-1 pr-4">
                    <time dateTime={doc.uploaded_at}>{formatTime(doc.uploaded_at)}</time>
                  </td>
                  <td className="py-1">{doc.verified ? "Verified" : "Not verified"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canUpload && (
          <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="document-type">Document type</Label>
              <select
                id="document-type"
                value={type}
                onChange={(e) => setType(e.target.value as DocumentType)}
                className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm"
              >
                {DOCUMENT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="document-file">Document file</Label>
              <input
                key={fileKey}
                id="document-file"
                type="file"
                accept="application/pdf,image/jpeg,image/png"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setFileError(null);
                }}
                className="text-sm"
              />
            </div>
            <Button type="submit" disabled={upload.isPending}>
              Upload document
            </Button>
            {fileError !== null && (
              <p role="alert" className="w-full rounded-md bg-red-50 p-3 text-sm text-red-900">
                {fileError}
              </p>
            )}
            {upload.isError && <ErrorNotice error={upload.error} />}
          </form>
        )}
      </CardContent>
    </Card>
  );
}
