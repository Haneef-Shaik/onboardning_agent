import type { DocumentMeta, HireRecord, PiiField, Role } from "@onb/ts-api-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAppClient } from "../api";
import { createSessionStore } from "../auth/session";
import { HireDetail } from "./HireDetail";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const HIRE: HireRecord = {
  id: "h_demo_1",
  name: "Asha Verma",
  role: "Engineer",
  location: "Pune",
  start_date: "2026-12-01",
  manager_id: "u_acme_mgr_ravi",
  notes: "",
  pii: {
    email: "a***@example.com",
    mobile: "XXXXXX3210",
    aadhaar: "XXXX XXXX 1234",
    pan: "XXXXX1234X",
    bank_account: "XXXXXX7890",
    ifsc: "HDFC0XXXXXX",
    account_holder_name: "A**** V****",
  },
  id_proof: { aadhaar_consent: true, alternative_id: null },
  created_by: "u_acme_hr1",
  created_at: "2026-11-12T09:00:00Z",
};

function envelope(status: number, data: unknown, error: unknown = null): Response {
  const body = { success: error === null, data, error, meta: { trace_id: "t-1", page: null } };
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

interface Calls {
  reveals: { field: string; reason: string }[];
  uploads: { type: string | null; contentType: string | null; size: number }[];
}

function fakeApi(documents: DocumentMeta[] = []) {
  const calls: Calls = { reveals: [], uploads: [] };
  const docs = [...documents];
  const handler = async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/api/, "");
    if (path === `/hires/${HIRE.id}` && request.method === "GET") return envelope(200, HIRE);
    if (path === `/hires/${HIRE.id}/documents` && request.method === "GET") return envelope(200, docs);
    if (path === `/hires/${HIRE.id}/documents` && request.method === "POST") {
      const bytes = await request.arrayBuffer();
      calls.uploads.push({ type: url.searchParams.get("type"), contentType: request.headers.get("content-type"), size: bytes.byteLength });
      const meta: DocumentMeta = {
        id: "doc_" + "a".repeat(24),
        hire_id: HIRE.id,
        type: url.searchParams.get("type") as DocumentMeta["type"],
        uploaded_at: "2026-11-12T10:30:00Z",
        verified: false,
      };
      docs.push(meta);
      return envelope(201, meta);
    }
    const reveal = /^\/hires\/h_demo_1\/pii\/([a-z_]+)\/reveal$/.exec(path);
    if (reveal && request.method === "POST") {
      const { reason } = (await request.json()) as { reason: string };
      calls.reveals.push({ field: reveal[1] ?? "", reason });
      if (reason.includes("refuse")) {
        return envelope(403, null, { code: "FORBIDDEN", message: "Not allowed", details: [] });
      }
      return envelope(200, { hire_id: HIRE.id, field: reveal[1], value: `plain-${reveal[1]}`, access_id: "acc_1", expires_in_seconds: 30 });
    }
    return envelope(404, null, { code: "NOT_FOUND", message: "Not found", details: [] });
  };
  return { handler, calls };
}

function renderDetail(role: Role, api = fakeApi()) {
  const session = createSessionStore();
  const client = createAppClient({ session, onUnauthenticated: () => undefined, baseUrl: "http://web.test/api", fetch: api.handler });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <HireDetail client={client} role={role} hireId={HIRE.id} />
    </QueryClientProvider>,
  );
  return api;
}

const revealButton = (label: string) => screen.queryByRole("button", { name: `Reveal ${label}` });

describe("HireDetail: masked by default (FR-P1)", () => {
  it("shows every sensitive field masked and never a vault token", async () => {
    renderDetail("HR_ADMIN");

    expect(await screen.findByText("XXXXXX3210")).toBeInTheDocument();
    expect(screen.getAllByTestId("masked-field")).toHaveLength(7);
    expect(screen.getAllByTestId("masked-field").every((el) => el.getAttribute("data-state") === "masked")).toBe(true);
    expect(document.body.textContent).not.toMatch(/ptk_/i);
  });
});

describe("HireDetail: who sees a reveal control", () => {
  const fields: [string, PiiField][] = [
    ["Email", "email"],
    ["Mobile", "mobile"],
    ["Aadhaar", "aadhaar"],
    ["PAN", "pan"],
    ["Bank account", "bank_account"],
    ["IFSC", "ifsc"],
    ["Account holder name", "account_holder_name"],
  ];

  it("gives HR_ADMIN a control on every field", async () => {
    renderDetail("HR_ADMIN");
    await screen.findByText("XXXXXX3210");
    expect(fields.every(([label]) => revealButton(label) !== null)).toBe(true);
  });

  it("gives HR a control on every field but Aadhaar", async () => {
    renderDetail("HR");
    await screen.findByText("XXXXXX3210");
    expect(revealButton("Aadhaar")).toBeNull();
    expect(fields.filter(([label]) => label !== "Aadhaar").every(([label]) => revealButton(label) !== null)).toBe(true);
  });

  it("gives a manager no control at all, and no upload form", async () => {
    renderDetail("MANAGER");
    await screen.findByText("XXXXXX3210");
    expect(screen.queryAllByRole("button", { name: /^Reveal / })).toHaveLength(0);
    expect(screen.queryByLabelText("Document file")).toBeNull();
  });
});

describe("HireDetail: audited reveal", () => {
  it("asks for a reason, reveals one field, and hides it again after the TTL", async () => {
    const user = userEvent.setup();
    const api = renderDetail("HR");
    await screen.findByText("XXXXXX3210");

    await user.click(revealButton("Mobile") as HTMLElement);
    const confirm = screen.getByRole("button", { name: "Confirm reveal" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for revealing Mobile"), "   ");
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for revealing Mobile"), "call about joining");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await user.click(confirm);

    expect(await screen.findByText("plain-mobile")).toBeInTheDocument();
    expect(api.calls.reveals).toEqual([{ field: "mobile", reason: "call about joining" }]);
    expect(screen.queryByText("XXXXXX3210")).toBeNull();
    // The other fields stay masked.
    expect(screen.getByText("XXXXX1234X")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTime(30_000));

    await waitFor(() => expect(screen.queryByText("plain-mobile")).toBeNull());
    expect(screen.getByText("XXXXXX3210")).toBeInTheDocument();
  });

  it("can be cancelled without calling the API", async () => {
    const user = userEvent.setup();
    const api = renderDetail("HR");
    await screen.findByText("XXXXXX3210");

    await user.click(revealButton("Mobile") as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByLabelText("Reason for revealing Mobile")).toBeNull();
    expect(api.calls.reveals).toEqual([]);
  });

  it("shows the API's refusal and keeps the field masked", async () => {
    const user = userEvent.setup();
    renderDetail("HR");
    await screen.findByText("XXXXXX3210");

    await user.click(revealButton("Mobile") as HTMLElement);
    await user.type(screen.getByLabelText("Reason for revealing Mobile"), "refuse this");
    await user.click(screen.getByRole("button", { name: "Confirm reveal" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to this.");
    expect(screen.getByText("XXXXXX3210")).toBeInTheDocument();
  });
});

describe("HireDetail: documents (FR-P3)", () => {
  it("lists existing scans with type, upload time and verified flag", async () => {
    renderDetail(
      "HR",
      fakeApi([
        { id: "doc_" + "b".repeat(24), hire_id: HIRE.id, type: "pan", uploaded_at: "2026-11-11T08:00:00Z", verified: true },
        { id: "doc_" + "c".repeat(24), hire_id: HIRE.id, type: "passport", uploaded_at: "2026-11-11T09:00:00Z", verified: false },
      ]),
    );

    const table = await screen.findByRole("table", { name: "Documents" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("PAN");
    expect(rows[0]).toHaveTextContent("Verified");
    expect(rows[0]).toHaveTextContent(/2026/);
    expect(rows[1]).toHaveTextContent("Passport");
    expect(rows[1]).toHaveTextContent("Not verified");
  });

  it("uploads a scan with its type and media type, then lists its metadata", async () => {
    const user = userEvent.setup();
    const api = renderDetail("HR");
    await screen.findByText("No documents yet.");

    await user.selectOptions(screen.getByLabelText("Document type"), "pan");
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3])], "scan.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Document file"), file);
    await user.click(screen.getByRole("button", { name: "Upload document" }));

    const table = await screen.findByRole("table", { name: "Documents" });
    expect(table).toHaveTextContent("PAN");
    expect(table).toHaveTextContent("Not verified");
    expect(api.calls.uploads).toEqual([{ type: "pan", contentType: "image/png", size: 7 }]);
    // The file name is never kept or shown.
    expect(document.body.textContent).not.toContain("scan.png");
  });

  it("refuses a file that is not a PDF, JPEG or PNG before sending it", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const api = renderDetail("HR");
    await screen.findByText("No documents yet.");

    await user.upload(screen.getByLabelText("Document file"), new File(["x"], "notes.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Upload document" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("PDF, JPEG or PNG");
    expect(api.calls.uploads).toEqual([]);
  });
});
