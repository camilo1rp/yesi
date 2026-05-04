"""Unit test for RFC 7396 JSON merge patch helper."""

from __future__ import annotations

from legalbot.artifacts.service import _json_merge_patch


def test_merge_adds_and_overwrites_keys() -> None:
    assert _json_merge_patch({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert _json_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}


def test_merge_null_removes_keys() -> None:
    assert _json_merge_patch({"a": 1, "b": 2}, {"a": None}) == {"b": 2}


def test_merge_replaces_non_dict_with_dict() -> None:
    assert _json_merge_patch([1, 2], {"a": 1}) == {"a": 1}


def test_merge_recursive() -> None:
    assert _json_merge_patch({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}) == {"a": {"x": 1, "y": 3}}
