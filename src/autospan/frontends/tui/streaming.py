"""Reconstruct logical lines across arbitrary command-output chunks.

CRLF is normalized and long lines are split into bounded display segments.
The assembler is UI-independent; rendering and retention belong to the UI.
"""

from __future__ import annotations


class LineAssembler:
    """Frame text into lines without introducing transport-chunk boundaries.

    A logical line longer than *max_line* is split into display segments. Memory
    consumed by the pending fragment is bounded by *max_line* plus one input
    chunk. Empty lines are preserved. Every command must call ``finish`` so a
    final unterminated line is visible before its result is accepted.
    """

    def __init__(self, *, max_line: int = 4096) -> None:
        if max_line < 1:
            raise ValueError("max_line must be positive")
        self._max_line = max_line
        self._pending = ""

    def push(self, chunk: str) -> list[str]:
        result: list[str] = []
        parts = chunk.split("\n")
        for index, part in enumerate(parts):
            terminated = index < len(parts) - 1
            self._pending += part
            if terminated:
                self._pending = self._pending.removesuffix("\r")
            # Keep a trailing CR temporarily: it may belong to CRLF split
            # across two queue chunks, even at the display-size boundary.
            while len(self._pending) > self._max_line:
                if (
                    not terminated
                    and len(self._pending) == self._max_line + 1
                    and self._pending.endswith("\r")
                ):
                    break
                result.append(self._pending[: self._max_line])
                self._pending = self._pending[self._max_line :]
            if terminated:
                result.append(self._pending)
                self._pending = ""
        return result

    def finish(self) -> list[str]:
        if not self._pending:
            return []
        last = self._pending
        self._pending = ""
        # A CR held for a possible CRLF may extend the fragment by one character.
        return [
            last[start : start + self._max_line] for start in range(0, len(last), self._max_line)
        ]
