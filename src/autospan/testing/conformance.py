"""Conformance checks any provider of a kernel contract can run in its own tests.

Example (in a provider package)::

    def test_conforms():
        with MyFlasherFactory().open(None, {}, {...}) as flasher:
            check_flasher(flasher, allow_flash=True)
"""

from __future__ import annotations

from typing import Any

from autospan.capabilities.flasher.contract import FLASHER, FlashRequest, FlashResult


def check_flasher(
    flasher: object, request: FlashRequest | None = None, *, allow_flash: bool = False
) -> FlashResult:
    """Call flash and assert result shape; the caller owns acquisition/cleanup.

    Use a simulator or an explicitly approved destructive test request and set
    allow_flash=True. Checks remain active under optimized Python.
    """

    if not FLASHER.conforms(flasher):
        raise AssertionError("object does not implement the Flasher protocol")
    if not allow_flash:
        raise ValueError(
            "Flashing requires explicit allow_flash=True on an approved target or fake"
        )
    if request is not None and not isinstance(request, FlashRequest):
        raise TypeError("request must be a FlashRequest")
    result = flasher.flash(request or FlashRequest())  # type: ignore[attr-defined]
    if not isinstance(result, FlashResult):
        raise AssertionError("flash() must return a FlashResult")
    if result.ok and (result.return_code != 0 or result.error_type is not None):
        raise AssertionError("successful flash result must have return_code=0 and no error_type")
    return result


def check_debugger(
    debugger: object,
    *,
    allow_control: bool = False,
    scratch_variable: str | None = None,
    scratch_value: object = 7,
) -> None:
    """Check protocol shape without executing target operations by default.

    To exercise a fake or approved target, opt into control and name a safe
    scratch variable. The invasive check leaves the target halted and restores
    the scratch value. The caller owns resource acquisition and cleanup.
    """

    from autospan.capabilities.debugger.contract import DEBUGGER

    if not DEBUGGER.conforms(debugger):
        raise AssertionError("object does not implement the DebuggerDriver protocol")
    if not allow_control:
        if scratch_variable is not None:
            raise ValueError("scratch_variable requires allow_control=True")
        return
    if not isinstance(scratch_variable, str) or not scratch_variable.strip():
        raise ValueError("Invasive debugger checks require an explicit scratch_variable")
    driver: Any = debugger
    driver.halt()
    if driver.state() != "stopped" or driver.is_running():
        raise AssertionError("halt() must stop the target")
    error: BaseException | None = None
    try:
        driver.run()
        if not driver.is_running():
            raise AssertionError("run() must start the target")
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            driver.halt()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"Debugger halt cleanup failed: {cleanup!r}")
    original = driver.read_variable(scratch_variable)
    error = None
    try:
        driver.write_variable(scratch_variable, scratch_value)
        if driver.read_variable(scratch_variable) != scratch_value:
            raise AssertionError("scratch variable did not round-trip")
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            driver.write_variable(scratch_variable, original)
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"Debugger scratch cleanup failed: {cleanup!r}")
