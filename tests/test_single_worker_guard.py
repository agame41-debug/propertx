"""The lifespan guard must reject WEB_CONCURRENCY/UVICORN_NUM_WORKERS > 1."""

from __future__ import annotations

import pytest

from report.web import _enforce_single_worker


def test_allows_unset_env(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_NUM_WORKERS", raising=False)
    _enforce_single_worker()  # no raise


def test_allows_explicit_one(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("UVICORN_NUM_WORKERS", "1")
    _enforce_single_worker()  # no raise


def test_rejects_web_concurrency_above_one(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.delenv("UVICORN_NUM_WORKERS", raising=False)
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY=4"):
        _enforce_single_worker()


def test_rejects_uvicorn_num_workers_above_one(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setenv("UVICORN_NUM_WORKERS", "2")
    with pytest.raises(RuntimeError, match="UVICORN_NUM_WORKERS=2"):
        _enforce_single_worker()


def test_ignores_non_numeric_values(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "auto")
    _enforce_single_worker()  # no raise — not a digit, can't enforce
