"""Explicit command argument privacy without corrupting unrelated diagnostics."""

import json

import pytest

from autospan.api import CommandContribution, CommandShell, Runtime
from autospan._core.evidence_store import LocalEvidenceStore


def test_numeric_argument_does_not_corrupt_other_numbers(tmp_path):
    def handler(ctx, args):
        raise ValueError("expected 10 bytes, got 1")

    store = LocalEvidenceStore(tmp_path)
    try:
        shell = CommandShell(Runtime({}, store), (CommandContribution(("check",), handler),))
        result = shell.execute("check 1", capture=True)
        assert "expected 10 bytes" in result.diagnostic
        assert "got <redacted>" in result.diagnostic
    finally:
        store.close()


@pytest.mark.parametrize("secret", ["short", "1", "λ μ", "a'b"])
def test_declared_secret_output_errors_and_evidence_are_masked(tmp_path, secret):
    original = ValueError(f"public=10 secret={secret}")

    def handler(ctx, args):
        ctx.write("public=" + args[0] + " secret=" + args[1])
        raise original

    store = LocalEvidenceStore(tmp_path)
    try:
        shell = CommandShell(
            Runtime({}, store), (CommandContribution(("check",), handler, sensitive_args=(1,)),)
        )
        from autospan.api import Completion

        line, _ = Completion(len("check 10 "), len("check 10 "), (secret,)).apply("check 10 ")
        result = shell.execute(line, capture=True)
        assert "secret=<redacted>" in result.output
        assert result.error is original  # Explicit in-memory cause remains available.
        assert shell.label(line) == "check"
        assert "<redacted>" in result.diagnostic
    finally:
        store.close()
    events = [json.loads(line) for line in (store.root / "events.jsonl").read_text().splitlines()]
    errors = [event["attributes"]["error"] for event in events if "error" in event["attributes"]]
    assert errors and all("secret=<redacted>" in error for error in errors)


def test_explicit_public_arguments_remain_in_diagnostics(tmp_path):
    def handler(ctx, args):
        raise ValueError("expected 10 bytes, got 1")

    store = LocalEvidenceStore(tmp_path)
    try:
        shell = CommandShell(
            Runtime({}, store), (CommandContribution(("check",), handler, sensitive_args=()),)
        )
        assert "expected 10 bytes, got 1" in shell.execute("check 1", capture=True).diagnostic
    finally:
        store.close()


def test_completion_error_uses_declared_privacy(tmp_path):
    def complete(runtime, args):
        raise ValueError("public 10; password=" + args[1])

    store = LocalEvidenceStore(tmp_path)
    try:
        shell = CommandShell(
            Runtime({}, store),
            (
                CommandContribution(
                    ("check",), lambda ctx, args: 0, complete=complete, sensitive_args=(1,)
                ),
            ),
        )
        result = shell.completion("check 10 --password=secret ")
        assert "secret" not in result.diagnostic and "public 10" in result.diagnostic
    finally:
        store.close()
