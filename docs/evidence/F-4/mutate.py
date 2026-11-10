"""F-4 mutation checks: break each guard, run the named tests, restore the file byte-for-byte."""

import hashlib
import pathlib
import subprocess
import sys

SRC = pathlib.Path("libs/py-telemetry/src/onb_telemetry")
UNIT = "libs/py-telemetry/tests"
INT = "libs/py-telemetry/tests_int"

MUTATIONS = [
    (
        "M1 export stage runs before the hooks (pipeline.on_end order swapped)",
        SRC / "pipeline.py",
        """        current = span
        for hook in self._hooks:""",
        """        current = span
        if self._export is not None:
            self._export.on_end(span)
        for hook in self._hooks:""",
        [f"{UNIT}/test_pipeline.py::test_registered_processor_sees_every_span_before_export",
         f"{UNIT}/test_pipeline.py::test_spans_from_many_threads_all_pass_the_hook_first"],
        False,
    ),
    (
        "M2 fail-open: a raising hook no longer stops export (return removed)",
        SRC / "pipeline.py",
        """                _log_failure(hook, "on_end", exc)
                return""",
        """                _log_failure(hook, "on_end", exc)
                continue""",
        [f"{UNIT}/test_pipeline.py::test_a_failing_hook_stops_the_span_being_exported"],
        False,
    ),
    (
        "M3 hooks registered on the provider after the exporter (SDK add_span_processor order)",
        SRC / "setup.py",
        """        self.pipeline.add_hook(processor)""",
        """        self.provider.add_span_processor(processor)""",
        [f"{UNIT}/test_pipeline.py::test_handle_registers_processors_too",
         f"{UNIT}/test_pipeline.py::test_registered_processor_sees_every_span_before_export"],
        False,
    ),
    (
        "M4 continue_from ignores the stored traceparent",
        SRC / "propagation.py",
        """    parent = _PROPAGATOR.extract({TRACEPARENT: traceparent}) if traceparent else None""",
        """    parent = None""",
        [f"{UNIT}/test_propagation.py::test_span_continued_from_stored_traceparent_carries_original_trace_id"],
        False,
    ),
    (
        "M5 continue_from adopts the ambient context instead of a fresh root for a bad traceparent",
        SRC / "propagation.py",
        """context=parent or Context(),""",
        """context=parent,""",
        [f"{UNIT}/test_propagation.py::test_bad_traceparent_never_adopts_the_ambient_span"],
        False,
    ),
    (
        "M6 content capture on by default",
        SRC / "settings.py",
        """capture_content=_flag(source, CAPTURE_CONTENT_VAR, default=False)""",
        """capture_content=_flag(source, CAPTURE_CONTENT_VAR, default=True)""",
        [f"{UNIT}/test_settings.py::test_defaults_export_otlp_and_capture_no_content"],
        False,
    ),
    (
        "M7 GenAI content switches no longer forced off",
        SRC / "setup.py",
        """        _apply_content_capture(config.capture_content)\n""",
        "",
        [f"{UNIT}/test_setup.py::test_content_capture_is_off_by_default_for_genai_instrumentation"],
        False,
    ),
    (
        "M8 a foreign global provider is silently accepted",
        SRC / "setup.py",
        """    if trace.get_tracer_provider() is not provider:""",
        """    if False:""",
        [f"{UNIT}/test_setup.py::test_init_refuses_when_another_provider_owns_the_global"],
        False,
    ),
    (
        "M9 no auto-instrumentation (FastAPI/httpx/PyMongo)",
        SRC / "setup.py",
        """        instrumented = _instrument_libraries(provider) if instrument else ()""",
        """        instrumented = ()""",
        [f"{UNIT}/test_setup.py::test_fastapi_app_created_after_init_gets_server_spans",
         f"{UNIT}/test_setup.py::test_httpx_calls_get_client_spans",
         f"{INT}/test_jaeger_roundtrip.py::test_fastapi_span_is_returned_by_jaeger_through_the_collector"],
        True,
    ),
    (
        "M10 OTLP exporter replaced by none (spans never leave the process)",
        SRC / "setup.py",
        """        return OTLPSpanExporter()  # endpoint""",
        """        return None  # endpoint""",
        [f"{UNIT}/test_setup.py::test_otlp_exporter_is_batched_by_default",
         f"{INT}/test_jaeger_roundtrip.py::test_fastapi_span_is_returned_by_jaeger_through_the_collector"],
        True,
    ),
    (
        "M11 test reset keeps PyMongo's stale command listener",
        SRC / "testing.py",
        """    _forget_pymongo_tracer()\n\n\ndef""",
        """\n\ndef""",
        [f"{INT}/test_jaeger_roundtrip.py"],
        True,
    ),
    (
        "M12 the export stage gets the original span, not the redacted copy",
        SRC / "pipeline.py",
        """            export.on_end(current)""",
        """            export.on_end(span)""",
        [f"{UNIT}/test_redaction.py::test_a_redacting_hook_changes_what_is_exported"],
        False,
    ),
    (
        "M13 a hook failing in on_start is no longer contained",
        SRC / "pipeline.py",
        """            except Exception as exc:
                _log_failure(hook, "on_start", exc)""",
        """            except ZeroDivisionError as exc:
                _log_failure(hook, "on_start", exc)""",
        [f"{UNIT}/test_redaction.py::test_a_hook_failing_on_start_does_not_break_the_caller_and_the_span_is_dropped"],
        False,
    ),
    (
        "M14 spans ending after shutdown began are still exported",
        SRC / "pipeline.py",
        """        if self._closed or self._was_poisoned(span):""",
        """        if self._was_poisoned(span):""",
        [f"{UNIT}/test_redaction.py::test_spans_ending_after_shutdown_are_never_exported"],
        False,
    ),
    (
        "M15 hook failure log carries the exception message (may hold PII)",
        SRC / "pipeline.py",
        """type(stage).__name__, step, type(exc).__name__)""",
        """type(stage).__name__, step, exc)""",
        [f"{UNIT}/test_redaction.py::test_failure_log_names_no_span_content"],
        False,
    ),
    (
        "M16 a failed init leaves its provider exporting",
        SRC / "setup.py",
        """            _uninstrument_all()
            provider.shutdown()
            raise""",
        """            raise""",
        [f"{UNIT}/test_setup.py::test_a_failed_init_leaves_nothing_exporting"],
        False,
    ),
]


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    bad = 0
    for title, path, old, new, tests, live in MUTATIONS:
        original = path.read_bytes()
        before = digest(path)
        text = original.decode()
        assert text.count(old) == 1, f"{title}: anchor not found exactly once in {path}"
        path.write_text(text.replace(old, new))
        try:
            cmd = ["uv", "run", "--frozen"] + (["--env-file", ".env"] if live else []) + [
                "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf", *tests]
            out = subprocess.run(cmd, capture_output=True, text=True)
        finally:
            path.write_bytes(original)
        restored = digest(path) == before
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith(("FAILED", "ERROR")) or " passed" in ln or " failed" in ln]
        print(f"## {title}")
        print(f"   file: {path} · live stack: {'yes' if live else 'no'}")
        for ln in lines:
            print(f"   {ln}")
        caught = out.returncode != 0
        print(f"   caught: {'yes' if caught else 'NO'} · restored byte-for-byte: {'yes' if restored else 'NO'}\n")
        bad += (not caught) + (not restored)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
