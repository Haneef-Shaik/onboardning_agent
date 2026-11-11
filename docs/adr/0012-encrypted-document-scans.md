# 0012 · Document scans: a GridFS-layout bucket through the tenant repository, raw-body upload

- **Status:** accepted · 2026-11-11 · goal HIRE-2
- **Context:** PRD FR-P3 and §7.10 Documents: Aadhaar/PAN scans are encrypted before they are written
  to GridFS, bound to the hire like vault records, and agents see only metadata (`type`,
  `uploaded_at`, `verified`). §9 names `documents` (GridFS, encrypted) and `documents_meta`; §10 names
  `POST /hires/{id}/documents`. §7.9 requires every database access to go through `TenantRepository`.
  The PRD leaves open how GridFS meets that rule, what the upload looks like, and the limits.

## Decision

1. **GridFS layout, written through `TenantRepository`.** `DataStore` never hands out a database
   object, which PyMongo's `AsyncGridFSBucket` needs, so the API writes the GridFS collections itself:
   `documents.files` `{_id, org_id, length, chunkSize, uploadDate, filename, metadata}` and
   `documents.chunks` `{org_id, files_id, n, data}` (255 KiB chunks, GridFS's default). Every document
   carries `org_id` and every query is org-scoped, so tenant isolation and the T-2 safety net hold
   unchanged. Indexes: `{org_id, files_id, n}` unique on chunks, `{org_id, metadata.hire_id}` on files.
   Any GridFS reader still sees a well-formed bucket (of ciphertext).
2. **One AEAD over the whole scan, then chunked.** The scan is sealed with the org keyring's AES-256-GCM
   (`Keyring.encrypt`); the ciphertext is split into chunks. Associated data:
   `onb.document.v1 | org_id | hire_id | document id | type | media type`, plus org, key purpose and key
   version (added by the keyring). Re-pointing a files document at another hire, changing its type or
   media type, pasting another document's chunks, or dropping/duplicating/altering a chunk fails the tag
   (`DecryptionError`); a structural defect (chunk numbering, length, header) fails closed before
   decryption. No record signature is added: unlike a vault record, there is no token to re-point, and
   the AEAD already covers every field a reader trusts.
3. **Aadhaar scans use the org's Aadhaar key** (`KeyPurpose.AADHAAR`), like the Aadhaar number; other
   scans use the data key. An Aadhaar scan is accepted only for a hire whose `id_proof.aadhaar_consent`
   is true (consent is captured with the number, ADR 0007); a hire who declined gives an alternative ID,
   whose scan types (`passport`, `voter_id`, `driving_licence`) are accepted alongside `aadhaar`, `pan`.
4. **`documents_meta` is exactly `{_id, org_id, hire_id, type, uploaded_at, verified}`.** No file name,
   size, media type, digest or token. The media type needed to decrypt lives in the encrypted bucket's
   files document, which no MCP user can reach (mcp-mongo's roles list only `documents_meta`). The
   original file name is discarded: it can carry a person's name or ID number.
5. **The upload is the raw request body**, `Content-Type` `application/pdf`, `image/jpeg` or `image/png`,
   `type` in the query. Not multipart: Starlette spools multipart files over 1 MiB to a temporary file,
   which would put a plaintext scan on disk; a raw body is streamed into memory under a byte cap and held
   only until it is encrypted. The bytes must start with the declared type's file signature.
6. **Limits:** at most 5 MiB (`MAX_DOCUMENT_BYTES`); a larger `Content-Length` is refused before reading,
   and the bytes read are counted, so a chunked or understated body cannot lift it. Too large → 413
   `PAYLOAD_TOO_LARGE`; a disallowed or mismatched type, an empty body or an unknown `type` → 422
   `VALIDATION_ERROR` naming `header.content-type`, `body` or `query.type` (no new error code).
7. **Order and undo:** chunks, then the files document, then `documents_meta`, then the audit record
   (`document.upload`, target `hire:<id>/document/<doc id>`). If the metadata or the audit write fails,
   everything written for the upload is removed (AUD-1) and the request fails. Agents never see a
   `documents_meta` record whose bytes are missing.

## Consequences

- No route returns a scan's bytes in this goal. `DocumentService.read` (bound to the hire) is there for a
  later audited download or verification step; `verified` stays `false` until such a goal sets it.
- Key rotation keeps old scans readable (the files document records its key version). Purging scans at
  retention time (FR-P6) is PII-5's: delete the files document and its chunks, keep the metadata.
- Reverse proxies in front of the API must allow bodies of at least 5 MiB for this route.
