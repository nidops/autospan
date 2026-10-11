"""Regression tests for the terminal's bounded line assembler."""

from __future__ import annotations

import pytest

from autospan.frontends.tui.streaming import LineAssembler


def test_arbitrary_chunks_do_not_create_new_lines() -> None:
    stream = LineAssembler(max_line=64)
    assert stream.push("a") == []
    assert stream.push("bc\ndef") == ["abc"]
    assert stream.push("ghi\n") == ["defghi"]
    assert stream.finish() == []


def test_empty_lines_and_windows_newlines() -> None:
    stream = LineAssembler()
    assert stream.push("\r\n\nhello\r") == ["", ""]
    assert stream.push("\n") == ["hello"]
    assert stream.finish() == []


def test_exact_boundary_before_newline_has_no_spurious_blank_line() -> None:
    stream = LineAssembler(max_line=4)
    assert stream.push("abcd") == []
    assert stream.push("\n") == ["abcd"]
    assert stream.push("abcde\n") == ["abcd", "e"]


def test_long_line_is_bounded_and_final_fragment_is_flushed() -> None:
    stream = LineAssembler(max_line=4)
    assert stream.push("abcdefghij") == ["abcd", "efgh"]
    assert stream.finish() == ["ij"]
    assert stream.finish() == []


def test_unicode_split_between_chunks_is_preserved() -> None:
    stream = LineAssembler(max_line=4)
    assert stream.push("雪" * 3) == []
    assert stream.push("雪" * 3 + "\n") == ["雪" * 4, "雪" * 2]


def test_reject_invalid_limit() -> None:
    try:
        LineAssembler(max_line=0)
    except ValueError as exc:
        assert "max_line" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_crlf_at_the_exact_boundary_even_when_chunked() -> None:
    stream = LineAssembler(max_line=4)
    assert stream.push("abcd\r") == []
    assert stream.push("\nnext\r\n") == ["abcd", "next"]


@pytest.mark.parametrize("limit", [1, 4, 4096])
def test_unterminated_cr_preserves_text_and_segment_bound(limit: int) -> None:
    stream = LineAssembler(max_line=limit)
    assert stream.push("a" * limit + "\r") == []
    assert stream.finish() == ["a" * limit, "\r"]
    assert stream.finish() == []
    assert stream.push("next\n") == ["next"[i : i + limit] for i in range(0, 4, limit)]
