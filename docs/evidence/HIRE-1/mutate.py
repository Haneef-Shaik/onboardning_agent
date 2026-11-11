"""HIRE-1 mutation checks: break a guard, run its tests, record the failing test, restore the file."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/Adya/personals/onboardning_agent/.claude/worktrees/goal-hire-1")
API = "apps/api/src/onb_api/hires/"
UNIT = "apps/api/tests/test_hires.py"

MUTANTS = [
    ("M1", API + "fields.py", "    PiiField.AADHAAR: is_valid_aadhaar,", "    PiiField.AADHAAR: lambda _v: True,", [UNIT, "-k", "invalid_value"]),
    ("M2", API + "service.py", "        if details:\n            raise AppError(ErrorCode.VALIDATION_ERROR, details=details)\n        return given", "        return given", [UNIT, "-k", "invalid_value or consent"]),
    ("M3", API + "service.py", "_AADHAAR_REVEAL_ROLES: Final = frozenset({Role.HR_ADMIN})", "_AADHAAR_REVEAL_ROLES: Final = frozenset({Role.HR_ADMIN, Role.HR})", [UNIT, "-k", "aadhaar"]),
    ("M4", API + "routes.py", "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN))]", "Hr = Annotated[Principal, Depends(require_role(Role.HR, Role.HR_ADMIN, Role.MANAGER))]", [UNIT, "-k", "manager"]),
    ("M5", "libs/py-contracts/src/onb_contracts/hires.py", "    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH, pattern=REASON_PATTERN)", "    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)", [UNIT, "-k", "without_a_reason"]),
    ("M6", API + "service.py", "        access_id = await self._access_log.record_reveal(\n            user_id=principal.user_id, hire_id=hire_id, field=field, token=token, masked=masked, reason=safe_reason\n        )", "        access_id = \"0\" * 32", [UNIT, "-k", "access_log"]),
    ("M7", API + "service.py", "        access_id = await self._access_log.record_reveal(\n            user_id=principal.user_id, hire_id=hire_id, field=field, token=token, masked=masked, reason=safe_reason\n        )", "        try:\n            access_id = await self._access_log.record_reveal(\n                user_id=principal.user_id, hire_id=hire_id, field=field, token=token, masked=masked, reason=safe_reason\n            )\n        except Exception:\n            access_id = \"0\" * 32", [UNIT, "-k", "access_log_write_fails"]),
    ("M8", API + "service.py", "        if await self.store.email_taken(email_hash):\n            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN)\n        hire_id", "        hire_id", [UNIT, "-k", "duplicate_email"]),
    ("M9", API + "store.py", "            partial={\"email_hash\": {\"$exists\": True}},\n", "", [UNIT, "-k", "duplicate_email or same_email"]),
    ("M10", API + "store.py", "            [(\"org_id\", 1), (\"email_hash\", 1)],\n            unique=True,", "            [(\"org_id\", 1), (\"email_hash\", 1)],\n            unique=False,", [UNIT, "-k", "slipped_past"]),
    ("M11", API + "service.py", "await self.store.page(manager_filter(principal), page=page, limit=limit)", "await self.store.page({}, page=page, limit=limit)", [UNIT, "-k", "lists_only_their_reports"]),
    ("M12", API + "service.py", "        require_in_manager_scope(principal, document)\n", "", [UNIT, "-k", "another_managers_report"]),
    ("M13", API + "records.py", "    return {_FIELDS[name]: str(masks.get(name, UNMASKABLE)) for name in tokens if name in _FIELDS}", "    return {_FIELDS[name]: str(tokens[name]) for name in tokens if name in _FIELDS}", [UNIT, "-k", "masked"]),
    ("M14", API + "service.py", "            \"notes\": redact(request.notes),", "            \"notes\": request.notes,", [UNIT, "-k", "notes"]),
    ("M15", API + "service.py", "        safe_reason = redact(reason.strip())", "        safe_reason = reason.strip()", [UNIT, "-k", "reason_carrying_pii"]),
    ("M16", API + "service.py", "masked=fields.masked(field, value), index=index)", "masked=value, index=index)", [UNIT, "-k", "no_raw_value"]),
    ("M17", API + "routes.py", "    response.headers[\"Cache-Control\"] = \"no-store\"\n", "", [UNIT, "-k", "reveals_aadhaar_with_a_reason"]),
    ("M18", API + "routes.py", "async def list_pii_access_log(\n    _: HrAdmin,", "async def list_pii_access_log(\n    _: Hr,", [UNIT, "-k", "only_hr_admin_reads"]),
    ("M19", "libs/py-data/src/onb_data/store.py", " or field == ORG_FIELD:", ":", ["libs/py-data/tests/test_store.py", "-k", "partial"]),
    ("M20", API + "service.py", "        if user is None or user.role is not Role.MANAGER:", "        if user is None:", [UNIT, "-k", "must_be_a_manager"]),
    ("M21", API + "service.py", "        if has_aadhaar and request.aadhaar_consent is None:", "        if False:", [UNIT, "-k", "consent"]),
    ("M22", API + "routes.py", "async def update_hire_pii(\n    principal: Hr,", "async def update_hire_pii(\n    principal: CurrentPrincipal,", [UNIT, "-k", "cannot_update"]),
    ("M23", API + "service.py", "            try:\n                await self._keyring.create(org_id)", "            try:\n                raise", [UNIT, "-k", "org_keys"]),
    ("M24", API + "service.py", "        await self._audit(principal, \"pii.reveal\", target, AuditOutcome.SUCCESS, safe_reason)\n", "", [UNIT, "-k", "redacted or audit_write_fails"]),
    ("M25", API + "service.py", "        except EmailTakenError:\n            await self._purge(org_id, hire_id, written)\n            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN) from None\n        except BaseException:", "        except EmailTakenError:\n            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN) from None\n        except BaseException:", [UNIT, "-k", "slipped_past"]),
    ("X1", API + "service.py", "        if document is None:\n            raise AppError(ErrorCode.NOT_FOUND)\n        require_in_manager_scope", "        if document is None:\n            raise AppError(ErrorCode.FORBIDDEN)\n        require_in_manager_scope", ["tools/tests_xt/test_xt_api.py::test_the_routes_own_cross_tenant_checks[GET /hires/{hire_id}]"]),
    ("X2", API + "store.py", "            partial={\"email_hash\": {\"$exists\": True}},\n", "", ["apps/api/tests_int/test_mongo_hires.py", "-k", "unique_per_org"]),
    ("N1", API + "service.py", "        try:\n            found = await self.store.set_fields(hire_id, {\"email_hash\": email_hash})\n        except EmailTakenError:\n            raise AppError(ErrorCode.CONFLICT, _EMAIL_TAKEN) from None\n        if not found:\n            raise AppError(ErrorCode.NOT_FOUND)\n        return previous", "        return previous", ["apps/api/tests/test_hires_failures.py", "-k", "email_race"]),
    ("N2", API + "store.py", "        newer = {\"$or\": [{version_path: {\"$exists\": False}}, {version_path: {\"$lt\": pii.version}}]}", "        newer: dict[str, Any] = {}", ["apps/api/tests/test_hires_failures.py", "-k", "older_value"]),
    ("N3", API + "service.py", "        try:\n            await self._audit(principal, \"pii.update\", target, AuditOutcome.SUCCESS)\n            written = await self._write(", "        try:\n            written = await self._write(", ["apps/api/tests/test_hires_failures.py", "apps/api/tests/test_hires.py", "-k", "audit"]),
    ("N4", API + "service.py", 'except VaultConflictError:\n            raise AppError(ErrorCode.CONFLICT, "The value was written concurrently; retry") from None\n', "except ZeroDivisionError:\n            raise\n", ["apps/api/tests/test_hires_failures.py", "-k", "concurrent_vault"]),
    ("N5", API + "service.py", "            try:\n                await self.store.delete(hire_id)\n            except Exception as error:\n                log.error(\"undo of hire %s failed: %s\", hire_id, type(error).__name__)\n", "            await self.store.delete(hire_id)\n", ["apps/api/tests/test_hires_failures.py", "-k", "undo_both_fail"]),
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
