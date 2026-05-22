"""Regression tests for _start_bulk_generation_runner subprocess hygiene.

Two guarantees:
  1) The subprocess inherits RENTERO_SKIP_PAYOUT_BACKFILL=1 so it does not
     repeat the parse work the webapp lifespan already did.
  2) A daemon thread reaps the child via Popen.wait(), preventing zombie
     entries in the process table.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def web_support(tmp_path, monkeypatch):
    """Import report.web_support with logs/ pointed at a temp dir."""
    import report.web_support as ws
    monkeypatch.setattr(ws, "_BASE_DIR", str(tmp_path), raising=True)
    monkeypatch.setattr(ws, "_CONFIG_PATH", str(tmp_path / "config.toml"), raising=True)
    return ws


def test_passes_skip_backfill_env_to_subprocess(web_support):
    """Popen must be called with env={"RENTERO_SKIP_PAYOUT_BACKFILL": "1", ...}."""
    fake_proc = MagicMock()
    fake_proc.pid = 42424
    fake_proc.wait.return_value = 0

    with patch.object(web_support.subprocess, "Popen", return_value=fake_proc) as mock_popen:
        web_support._start_bulk_generation_runner(
            run_id=999, year=2026, month=4, db_path="/tmp/test.db"
        )

    assert mock_popen.called, "subprocess.Popen should have been invoked"
    kwargs = mock_popen.call_args.kwargs
    assert "env" in kwargs, "Popen must receive an env mapping"
    assert kwargs["env"].get("RENTERO_SKIP_PAYOUT_BACKFILL") == "1"


def test_spawns_reaper_thread(web_support):
    """A daemon thread named 'bulk-reaper-<pid>' must be started after Popen."""
    fake_proc = MagicMock()
    fake_proc.pid = 31337
    wait_finished = threading.Event()

    def slow_wait():
        # Block briefly so we can observe the live thread before it exits.
        wait_finished.wait(timeout=2.0)
        return 0

    fake_proc.wait.side_effect = slow_wait

    threads_before = {t.name for t in threading.enumerate()}
    with patch.object(web_support.subprocess, "Popen", return_value=fake_proc):
        web_support._start_bulk_generation_runner(
            run_id=1000, year=2026, month=5, db_path="/tmp/test.db"
        )

    # Find the reaper among newly-started threads.
    reaper = None
    for _ in range(50):  # up to 5s of polling
        for t in threading.enumerate():
            if t.name == f"bulk-reaper-{fake_proc.pid}" and t.name not in threads_before:
                reaper = t
                break
        if reaper:
            break
        time.sleep(0.1)

    assert reaper is not None, "Expected a daemon thread named bulk-reaper-<pid>"
    assert reaper.daemon is True, "Reaper thread must be a daemon"

    wait_finished.set()
    reaper.join(timeout=5.0)
    assert not reaper.is_alive(), "Reaper thread should exit after Popen.wait() returns"
    fake_proc.wait.assert_called()


def test_reaper_closes_log_file_after_wait(web_support):
    """Once the child terminates, the reaper must close the log file handle."""
    fake_proc = MagicMock()
    fake_proc.pid = 55555
    fake_proc.wait.return_value = 0

    closed_handles: list[object] = []
    real_open = web_support.os.path  # placeholder to satisfy type checker

    class TrackingFile:
        def __init__(self, *a, **kw):
            self.closed = False
        def write(self, data):
            return len(data)
        def fileno(self):
            return 1
        def close(self):
            self.closed = True
            closed_handles.append(self)

    tracking = TrackingFile()
    with patch("builtins.open", return_value=tracking), \
         patch.object(web_support.subprocess, "Popen", return_value=fake_proc):
        web_support._start_bulk_generation_runner(
            run_id=1001, year=2026, month=5, db_path="/tmp/test.db"
        )

    # Wait for reaper to run.
    for _ in range(50):
        if tracking.closed:
            break
        time.sleep(0.05)

    assert tracking.closed, "Reaper thread must close the log file handle"
    assert tracking in closed_handles
