"""Conformance checks any provider of a kernel contract can run in its own tests.

Example (in a provider package)::

    def test_conforms():
        with MyFlasherFactory().open(None, {}, {...}) as flasher:
            check_flasher(flasher)
"""

from __future__ import annotations

from typing import Any

from autospan.capabilities.flasher.contract import FLASHER, FlashRequest, FlashResult


def check_flasher(flasher: object, request: FlashRequest | None = None) -> FlashResult:
    """Call flash and assert result shape; the caller owns acquisition/cleanup.

    Use a simulator or an explicitly approved destructive test request.
    """

    assert FLASHER.conforms(flasher), "object does not implement the Flasher protocol"
    result = flasher.flash(request or FlashRequest())  # type: ignore[attr-defined]
    assert isinstance(result, FlashResult), "flash() must return a FlashResult"
    assert result.ok == (result.return_code == 0 and result.error_type is None) or not result.ok
    return result


def check_debugger(debugger: object) -> None:
    """Exercise run/halt and variable I/O on a caller-owned debugger.

    Leaves it halted with variable ``x`` set to 7. Use a simulator or an approved
    target; this helper performs operations and does not open or close resources.
    """

    from autospan.capabilities.debugger.contract import DEBUGGER

    assert DEBUGGER.conforms(debugger), "object does not implement the DebuggerDriver protocol"
    driver: Any = debugger
    driver.halt()
    assert driver.state() == "stopped" and not driver.is_running()
    driver.run()
    assert driver.is_running()
    driver.halt()
    driver.write_variable("x", 7)
    assert driver.read_variable("x") == 7
