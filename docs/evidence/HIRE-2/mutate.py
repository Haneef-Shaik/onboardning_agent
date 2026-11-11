"""HIRE-2 mutation checks: break a guard, run its tests, record the failing test, restore the file.

Run from the worktree root: `uv run --frozen python docs/evidence/HIRE-2/mutate.py [IDS...]`.
X-mutants need the compose stack (`make up`) and `.env` (test-xt / test-int).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
D = "apps/api/src/onb_api/documents/"
BUCKET_T = "apps/api/tests/test_document_bucket.py"
ROUTE_T = "apps/api/tests/test_documents.py"
XT = "tools/tests_xt/"

MUTANTS = [
    # Box 1: encryption and the hire binding
    ("B1", D + "bucket.py", "        ciphertext = sealed.ciphertext\n", "        ciphertext = data\n", [BUCKET_T, "-k", "differ_from_the_plaintext"]),
    ("B2", D + "bucket.py", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type, media_type)", "    return associated_data(_BINDING_LABEL, org_id, document_id, document_type, media_type)", [BUCKET_T, "-k", "rebinding"]),
    ("B3", D + "bucket.py", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type, media_type)", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, media_type)", [BUCKET_T, "-k", "bound_metadata"]),
    ("B4", D + "bucket.py", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type, media_type)", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type)", [BUCKET_T, "-k", "bound_metadata"]),
    ("B5", D + "bucket.py", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_id, document_type, media_type)", "    return associated_data(_BINDING_LABEL, org_id, hire_id, document_type, media_type)", [BUCKET_T, "-k", "same_hire"]),
    ("B6", D + "bucket.py", 'find_one({"_id": document_id, "metadata.hire_id": hire_id})', 'find_one({"_id": document_id})', [BUCKET_T, "-k", "another_hire_cannot"]),
    ("B7", D + "bucket.py", "    return KeyPurpose.AADHAAR if document_type is DocumentType.AADHAAR else KeyPurpose.DATA", "    return KeyPurpose.DATA", [BUCKET_T, "-k", "aadhaar_key"]),
    ("B8", D + "bucket.py", "    if [c.get(\"n\") for c in chunks] != list(range(math.ceil(length / chunk_size))):\n        raise _refused(files.get(\"_id\"), \"missing or duplicated chunks\")\n", "", [BUCKET_T, "-k", "reordered"]),
    ("B9", D + "bucket.py", "    if len(data) != length:\n        raise _refused(files.get(\"_id\"), \"length mismatch\")\n", "", [BUCKET_T, "-k", "truncated"]),
    ("B10", D + "bucket.py", "        except BaseException:\n            await self._remove(doc_id)\n            raise\n", "        except BaseException:\n            raise\n", [BUCKET_T, "-k", "failed_chunk_write"]),
    ("B11", D + "bucket.py", "    if not isinstance(meta, dict) or meta.get(\"alg\") != ALG:", "    if not isinstance(meta, dict):", [BUCKET_T, "-k", "malformed_encryption_header"]),
    # Box 2: size and media type
    ("U1", D + "upload.py", "    if media_type not in ALLOWED_DOCUMENT_MEDIA_TYPES:", "    if not media_type:", [ROUTE_T, "-k", "disallowed_media_type"]),
    ("U2", D + "upload.py", "    if not data.startswith(_SIGNATURES[media_type]):", "    if False:", [ROUTE_T, "-k", "not_the_declared_type"]),
    ("U3", D + "upload.py", "        if len(received) + len(chunk) > limit:\n            raise _too_large(limit)\n", "", [ROUTE_T, "-k", "oversized_stream or understated"]),
    ("U4", D + "upload.py", "    if declared is not None and declared > limit:\n        raise _too_large(limit)\n", "", [ROUTE_T, "-k", "overstated"]),
    ("U5", D + "upload.py", "        if len(received) + len(chunk) > limit:", "        if len(received) + len(chunk) > limit + 1:", [ROUTE_T, "-k", "oversized_scan_is_refused or one_byte_past"]),
    ("U6", D + "upload.py", "    if not data:\n        raise AppError(ErrorCode.VALIDATION_ERROR, details=(ErrorDetail(field=\"body\", issue=\"The document is empty\"),))\n", "", [ROUTE_T, "-k", "empty_scan"]),
    ("U7", D + "service.py", "        max_bytes: int = MAX_DOCUMENT_BYTES,", "        max_bytes: int = MAX_DOCUMENT_BYTES * 2,", [ROUTE_T, "-k", "oversized_scan_is_refused"]),
    # Box 3: documents_meta holds only ids, type, uploaded_at, verified
    ("M1", D + "meta.py", "                \"verified\": record.verified,\n", "                \"verified\": record.verified,\n                \"media_type\": \"image/png\",\n", [BUCKET_T, "-k", "documents_meta_holds_only"]),
    ("M2", D + "meta.py", "                \"verified\": record.verified,\n", "", [ROUTE_T, "-k", "documents_meta_holds_only"]),
    # Who may upload, to which hire, audit
    ("S1", D + "service.py", "        if hire is None:\n            raise AppError(ErrorCode.NOT_FOUND)\n", "", [ROUTE_T, "-k", "another_orgs_hire"]),
    ("S2", D + "service.py", "        if document_type is DocumentType.AADHAAR and not _has_aadhaar_consent(hire):", "        if False:", [ROUTE_T, "-k", "needs_the_hires_aadhaar_consent"]),
    ("S3", D + "service.py", "            await self._audit(principal, f\"hire:{hire_id}/document/{stored.id}\")\n", "", [ROUTE_T, "-k", "audited_without_content"]),
    ("S4", D + "service.py", "        except BaseException:\n            await self._undo(stored.id)\n            raise\n", "        except BaseException:\n            raise\n", [ROUTE_T, "-k", "audit_fails_leaves_nothing"]),
    ("S5", D + "routes.py", "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]", "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN, Role.MANAGER))]", [ROUTE_T, "-k", "manager_may_not"]),
    # Box 4: test-xt covers the route (needs the compose stack)
    ("X1", XT + "test_xt_api.py", "    (\"POST\", \"/hires/{hire_id}/documents\"): RouteCase(\n", "    (\"POST\", \"/hires/{hire_id}/documents/off\"): RouteCase(\n", [XT + "test_xt_coverage.py", "-k", "every_api_route"]),
    ("X2", D + "service.py", "        if hire is None:\n            raise AppError(ErrorCode.NOT_FOUND)\n", "        if hire is None:\n            raise AppError(ErrorCode.FORBIDDEN)\n", [XT + "test_xt_api.py::test_the_routes_own_cross_tenant_checks[POST /hires/{hire_id}/documents]"]),
    ("X3", D + "bucket.py", "        ciphertext = sealed.ciphertext\n", "        ciphertext = data\n", ["apps/api/tests_int/test_mongo_documents.py", "-k", "ciphertext"]),
]


def run(ident: str, path: str, old: str, new: str, args: list[str]) -> str:
    target = ROOT / path
    original = target.read_text()
    if original.count(old) != 1:
        return f"{ident}: SETUP ERROR (pattern found {original.count(old)} times in {path})"
    target.write_text(original.replace(old, new))
    try:
        result = subprocess.run(
            ["uv", "run", "--frozen", "--env-file", ".env", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
        )
    finally:
        target.write_text(original)
    failed = re.findall(r"^FAILED (\S+)", result.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", result.stdout, re.M)
    if failed or errors:
        return f"{ident}: KILLED by {(failed or errors)[0]}"
    return f"{ident}: SURVIVED (exit {result.returncode}) {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''}"


if __name__ == "__main__":
    wanted = set(sys.argv[1:])
    for mutant in MUTANTS:
        if not wanted or mutant[0] in wanted:
            print(run(*mutant), flush=True)
