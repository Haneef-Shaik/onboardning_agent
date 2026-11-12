"""The journey `make test-pii` takes through the API with the fixture PII (SEC-1): every way a sensitive
value enters the platform, then a full run of the seed template that resolves one of them at a sink.

1. HR creates a hire with every sensitive field, an Aadhaar consent, and notes holding raw values;
2. HR replaces the mobile (a new vault version; the run uses the new one);
3. HR reveals the email, HR_ADMIN the Aadhaar (reasons logged, values answered once);
4. HR uploads an Aadhaar scan whose content holds the Aadhaar number and a marker;
5. HR starts the seed-template run; the worker runs every task through its agent;
6. the manager approves the approval task in the web inbox; the run completes, and the welcome message
   goes out on the Telegram mock to the (new) mobile, resolved at the sink with a grant;
7. HR asks the run assistant a question holding raw values (redacted before storage and the model).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from .fixture import PiiFixture
from .platform import MANAGER_ID, TEMPLATE_KEY, USERS, user_email

RUN_TIMEOUT_S: Final = 180.0
POLL_S: Final = 0.5
HIRE_NAME: Final = "Priya Scanfixture"
PDF_HEADER: Final = b"%PDF-1.4\n"


@dataclass(frozen=True)
class Journey:
    hire_id: str
    run_id: str
    run_status: str
    tasks: Mapping[str, str]
    chat_reply: str
    responses: tuple[tuple[str, Any], ...]  # (route, body) of every API answer, reveals excluded
    rejected_input: int  # the status of the deliberately invalid request


class Api:
    def __init__(self, base_url: str, password: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=60)
        self._password = password
        self._tokens: dict[str, str] = {}
        self.responses: list[tuple[str, Any]] = []

    def login(self, handle: str) -> None:
        response = self._client.post("/auth/login", json={"email": user_email(handle), "password": self._password})
        response.raise_for_status()
        self._tokens[handle] = response.json()["data"]["access_token"]

    def call(self, handle: str, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._tokens[handle]}", **kwargs.pop("headers", {})}
        response = self._client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise AssertionError(f"{method} {path} as {handle}: {response.status_code} {_error_code(response)}")
        body = response.json()
        if not path.endswith("/reveal"):  # a reveal answers with the value by design (FR-P2)
            self.responses.append((f"{method} {path} as {handle}", body))
        return body["data"]

    def refused(self, handle: str, method: str, path: str, **kwargs: Any) -> int:
        """A request expected to fail; its answer is kept for the scan (an error must not echo input)."""
        headers = {"Authorization": f"Bearer {self._tokens[handle]}"}
        response = self._client.request(method, path, headers=headers, **kwargs)
        self.responses.append((f"{method} {path} as {handle} (refused)", response.text))
        return response.status_code

    def stream(self, handle: str, path: str, body: Mapping[str, Any]) -> str:
        """A server-sent-events answer (the chat route), as its raw text."""
        headers = {"Authorization": f"Bearer {self._tokens[handle]}"}
        response = self._client.post(path, headers=headers, json=dict(body))
        if response.status_code >= 400:
            raise AssertionError(f"POST {path} as {handle}: {response.status_code} {_error_code(response)}")
        self.responses.append((f"POST {path} as {handle}", response.text))
        return response.text

    def close(self) -> None:
        self._client.close()


def _error_code(response: httpx.Response) -> str:
    """The error code only: a response body could echo an input, and inputs here are fixture PII."""
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return "?"


def _new_hire(fx: PiiFixture) -> dict[str, Any]:
    return {
        "name": HIRE_NAME,
        "role": "engineer",
        "location": "Bangalore",
        "start_date": "2026-12-01",
        "manager_id": MANAGER_ID,
        "notes": f"Prefers calls on {fx.mobile}; PAN {fx.pan}; mail {fx.email}; salary acct no {fx.bank_account}.",
        "pii": {
            "email": fx.email,
            "mobile": fx.mobile,
            "aadhaar": fx.aadhaar,
            "pan": fx.pan,
            "bank_account": fx.bank_account,
            "ifsc": fx.ifsc,
            "account_holder_name": fx.account_holder_name,
        },
        "aadhaar_consent": {"purpose": "Statutory onboarding identity check"},
    }


def _scan_pdf(fx: PiiFixture) -> bytes:
    text = f"Aadhaar {fx.aadhaar} {fx.document_text} {fx.account_holder_name}"
    return PDF_HEADER + f"1 0 obj << /Length {len(text)} >> stream\n{text}\nendstream endobj\n%%EOF\n".encode()


def _wait(api: Api, run_id: str, done: Any, what: str) -> dict[str, Any]:
    deadline = time.monotonic() + RUN_TIMEOUT_S
    while time.monotonic() < deadline:
        run: dict[str, Any] = api.call("hr", "GET", f"/runs/{run_id}")
        if done(run):
            return run
        time.sleep(POLL_S)
    raise AssertionError(f"run {run_id} never reached {what}: {_states(run)}")


def _states(run: Mapping[str, Any]) -> dict[str, str]:
    return {t["task_id"]: t["status"] for t in run.get("tasks", [])}


def _read_back(api: Api, hire_id: str) -> None:
    """What each role reads back afterwards: masked hire, document list, outbox, access log, audit."""
    api.call("hr", "GET", f"/hires/{hire_id}")
    api.call("manager", "GET", f"/hires/{hire_id}")
    api.call("hr", "GET", f"/hires/{hire_id}/documents")
    api.call("hr", "GET", "/mock-outbox")
    api.call("hradmin", "GET", "/pii-access-log")
    api.call("hradmin", "GET", "/audit")
    api.call("manager", "GET", "/approvals")


def walk(base_url: str, password: str, fx: PiiFixture) -> Journey:
    api = Api(base_url, password)
    try:
        for _, handle, _, _ in USERS:
            api.login(handle)
        bad = {**_new_hire(fx), "pii": {**_new_hire(fx)["pii"], "pan": "NOTAPAN"}}  # a 422 must not echo values
        rejected = api.refused("hr", "POST", "/hires", json=bad)
        hire_id = api.call("hr", "POST", "/hires", json=_new_hire(fx))["id"]
        api.call("hr", "PUT", f"/hires/{hire_id}/pii/mobile", json={"value": fx.mobile_updated})
        reason = f"called {fx.mobile} to verify the address"  # reasons are free text: stored redacted
        api.call("hr", "POST", f"/hires/{hire_id}/pii/email/reveal", json={"reason": reason})
        api.call("hradmin", "POST", f"/hires/{hire_id}/pii/aadhaar/reveal", json={"reason": "statutory check"})
        api.call(
            "hr",
            "POST",
            f"/hires/{hire_id}/documents",
            params={"type": "aadhaar"},
            content=_scan_pdf(fx),
            headers={"Content-Type": "application/pdf"},
        )
        body = {"hire_id": hire_id, "template_key": TEMPLATE_KEY, "template_version": 1}
        run_id = api.call("hr", "POST", "/runs", json=body)["id"]
        waiting = _wait(api, run_id, lambda r: _states(r).get("manager_approval") == "waiting_approval", "approval")
        assert waiting["status"] == "running", _states(waiting)
        pending = [a for a in api.call("manager", "GET", "/approvals") if a["run_id"] == run_id]
        assert len(pending) == 1, pending
        api.call("manager", "POST", f"/approvals/{pending[0]['id']}/decision", json={"decision": "approve"})
        finished = _wait(api, run_id, lambda r: r["status"] in {"completed", "failed", "blocked"}, "an end")
        question = (
            f"Did the welcome reach {fx.mobile_updated}? Her PAN is {fx.pan}, Aadhaar {fx.aadhaar}, "
            f"account number {fx.bank_account}."
        )
        reply = api.stream("hr", f"/runs/{run_id}/chat", {"message": question})
        _read_back(api, hire_id)
        states = _states(finished)
        return Journey(hire_id, run_id, finished["status"], states, reply, tuple(api.responses), rejected)
    finally:
        api.close()
