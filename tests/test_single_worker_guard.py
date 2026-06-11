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


# ── Single-instance file lock ────────────────────────────────────────────────
#
# `uvicorn --workers N` does NOT set the env vars above in its children, so
# the env check alone can't catch it. The OS-level lock on cache/web.lock
# does: the second process fails fast instead of racing on SQLite writes.

def test_instance_lock_skipped_in_insecure_dev_mode(monkeypatch, tmp_path):
    import report.web as web_module
    monkeypatch.setenv("RENTERO_ALLOW_INSECURE_DEFAULTS", "1")
    monkeypatch.setattr(web_module, "_DB_PATH", str(tmp_path / "rentero.db"))
    assert web_module._acquire_single_instance_lock() is None


def test_instance_lock_blocks_second_acquirer(monkeypatch, tmp_path):
    import report.web as web_module
    monkeypatch.delenv("RENTERO_ALLOW_INSECURE_DEFAULTS", raising=False)
    monkeypatch.setattr(web_module, "_DB_PATH", str(tmp_path / "rentero.db"))

    first = web_module._acquire_single_instance_lock()
    assert first is not None
    try:
        with pytest.raises(RuntimeError, match="web.lock"):
            web_module._acquire_single_instance_lock()
    finally:
        web_module._release_single_instance_lock(first)

    # After release the lock is acquirable again (clean restart).
    second = web_module._acquire_single_instance_lock()
    assert second is not None
    web_module._release_single_instance_lock(second)
