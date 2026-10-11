"""Bounded command-path history, independent of the UI toolkit."""

from __future__ import annotations


class History:
    """History with draft restoration. Callers supply argument-free safe labels."""

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("History limit must be positive")
        self._limit = limit
        self._items: list[str] = []
        self._cursor = 0
        self._draft = ""

    @property
    def items(self) -> tuple[str, ...]:
        return tuple(self._items)

    def add(self, line: str) -> None:
        if line and (not self._items or self._items[-1] != line):
            self._items.append(line)
            del self._items[: -self._limit]
        self._cursor = len(self._items)
        self._draft = ""

    def previous(self, current: str) -> str:
        if self._cursor == len(self._items):
            self._draft = current
        self._cursor = max(0, self._cursor - 1)
        return self._items[self._cursor] if self._items else current

    def next(self, current: str) -> str:
        if self._cursor >= len(self._items):
            return current
        self._cursor += 1
        return self._items[self._cursor] if self._cursor < len(self._items) else self._draft
