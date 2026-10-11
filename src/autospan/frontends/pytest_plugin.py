"""Thin pytest adapter over the one canonical AutoSpan runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from autospan.api.errors import GateError, LifecycleError
from autospan.api.runtime import Runtime
from autospan._core.evidence_store import LocalEvidenceStore
from autospan._core.host import HostBuild
from autospan._core.runtime import RuntimeSession

_REPORTS_ATTR = "_autospan_phase_reports"


def _node_outcome(node: Any) -> str | None:
    """Derive the verdict known when function-scoped fixtures are torn down."""
    reports: dict[str, pytest.TestReport] = getattr(node, _REPORTS_ATTR, {})
    if not reports:
        return None
    if any(report.failed for report in reports.values()):
        return "failed"
    if any(report.skipped for report in reports.values()):
        return "skipped"
    return "passed"


class AutoSpanPytestBridge:
    """Pytest owns test execution; AutoSpan owns runtime/evidence lifecycle and gates."""

    def __init__(self, host: HostBuild, evidence: LocalEvidenceStore) -> None:
        self._host = host
        self._evidence = evidence
        self._enabled: frozenset[str] = frozenset()
        self._session_failed = False
        self._session_has_pass = False
        self._session_owner: RuntimeSession | None = None

    @property
    def enabled_gates(self) -> frozenset[str]:
        return self._enabled

    def pytest_addoption(self, parser: pytest.Parser) -> None:
        group = parser.getgroup("autospan")
        for gate in self._host.gates:
            implied = self._host.gate_closure(gate.name)[1:]
            suffix = f" (also requires {', '.join('--' + g for g in implied)})" if implied else ""
            group.addoption(
                gate.option,
                action="store_true",
                default=False,
                dest=f"autospan_gate_{gate.name}",
                help=(gate.help or f"allow tests marked {gate.name}") + suffix,
            )

    def pytest_configure(self, config: pytest.Config) -> None:
        for gate in self._host.gates:
            config.addinivalue_line("markers", f"{gate.name}: {gate.help or 'AutoSpan gate'}")
        requested = frozenset(
            gate.name
            for gate in self._host.gates
            if bool(config.getoption(f"autospan_gate_{gate.name}"))
        )
        self._enabled = frozenset(
            name for name in requested if requested.issuperset(self._host.gate_closure(name))
        )
        self._evidence.event("gates_configured", enabled=sorted(self._enabled))

    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        del config
        for item in items:
            required: list[str] = []
            for gate in self._host.gates:
                if item.get_closest_marker(gate.name) is not None:
                    for name in self._host.gate_closure(gate.name):
                        if name not in required:
                            required.append(name)
            missing = [name for name in required if name not in self._enabled]
            if missing:
                # Name the most basic missing gate first (hardware before destructive).
                basic = next(
                    (
                        name
                        for name in missing
                        if all(dep in self._enabled for dep in self._host.gate_closure(name)[1:])
                    ),
                    missing[-1],
                )
                item.add_marker(pytest.mark.skip(reason=f"requires --{basic.replace('_', '-')}"))

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Iterator[None]:
        del call
        outcome = yield
        report = outcome.get_result()  # type: ignore[attr-defined]
        reports = getattr(item, _REPORTS_ATTR, None)
        if reports is None:
            reports = {}
            setattr(item, _REPORTS_ATTR, reports)
        reports[report.when] = report

    @pytest.fixture
    def runtime(self, request: pytest.FixtureRequest) -> Iterator[Runtime]:
        session = RuntimeSession(
            plan=self._host.plan,
            factories=self._host.factories,
            evidence=self._evidence,
            enabled_gates=self._enabled,
        )
        try:
            entered = session.__enter__()
        except GateError as exc:
            pytest.skip(str(exc))
        try:
            yield entered
        except BaseException as exc:  # pragma: no cover - pytest never throws into fixtures
            session.set_outcome("failed")
            if not session.__exit__(type(exc), exc, exc.__traceback__):
                raise
        else:
            session.set_outcome(_node_outcome(request.node))
            session.__exit__(None, None, None)

    @pytest.fixture(scope="session")
    def session_runtime(self) -> Iterator[Runtime]:
        """Share one acquisition; close after all fixture phase reports are available."""
        session = RuntimeSession(
            plan=self._host.plan,
            factories=self._host.factories,
            evidence=self._evidence,
            enabled_gates=self._enabled,
        )
        try:
            entered = session.__enter__()
        except GateError as exc:
            pytest.skip(str(exc))
        self._session_owner = session
        yield entered

    def close_session_runtime(self, exit_code: int) -> None:
        """Close the shared owner once, including failures outside test call phases."""
        owner, self._session_owner = self._session_owner, None
        if owner is None:
            return
        owner.set_outcome(
            "failed"
            if self._session_failed or exit_code != 0
            else "passed"
            if self._session_has_pass
            else "skipped"
        )
        owner.__exit__(None, None, None)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        # The final teardown report arrives after session fixture finalizers.
        # Waiting for sessionfinish lets components retain evidence for that failure.
        try:
            self.close_session_runtime(exitstatus)
        except LifecycleError as exc:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            reporter = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_sep("=", str(exc), red=True)

    @pytest.fixture(scope="session")
    def autospan_evidence(self) -> LocalEvidenceStore:
        return self._evidence

    @pytest.fixture(scope="session")
    def autospan_gates(self) -> frozenset[str]:
        """Gates enabled for this run, for scenario-level conditional logic."""
        return self._enabled

    @pytest.fixture(scope="session")
    def autospan_config_path(self) -> Path | None:
        """Selected AutoSpan configuration file (``None`` for in-memory hosts)."""
        return self._host.config_path

    @pytest.fixture(autouse=True)
    def _autospan_test_scope(
        self,
        request: pytest.FixtureRequest,
        autospan_evidence: LocalEvidenceStore,
    ) -> Iterator[None]:
        with autospan_evidence.scope(test_id=request.node.nodeid):
            autospan_evidence.event("test_started", nodeid=request.node.nodeid)
            yield
            autospan_evidence.event("test_scope_finished", nodeid=request.node.nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.outcome == "failed":
            self._session_failed = True
        if report.when == "call" and report.outcome == "passed":
            self._session_has_pass = True
        # Log reports arrive after fixture teardown as well, so do not rely on
        # the fixture-bound ContextVar still being active for correlation.
        with self._evidence.scope(test_id=report.nodeid):
            self._evidence.event(
                "pytest_phase_finished",
                nodeid=report.nodeid,
                phase=report.when,
                outcome=report.outcome,
                duration_s=report.duration,
            )
