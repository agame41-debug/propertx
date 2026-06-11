import os
import sqlite3
from datetime import datetime, timedelta, timezone

MONTH_STATUS_OPEN = "OPEN"
MONTH_STATUS_LOCKED = "LOCKED"
MONTH_DATA_STATE_EMPTY = "EMPTY"
MONTH_DATA_STATE_READY = "READY_TO_GENERATE"
MONTH_DATA_STATE_GENERATED = "GENERATED"
MONTH_DATA_STATE_STALE = "STALE"
GENERATION_JOB_PENDING = "PENDING"
GENERATION_JOB_RUNNING = "RUNNING"
GENERATION_JOB_SUCCEEDED = "SUCCEEDED"
GENERATION_JOB_FAILED = "FAILED"
BULK_GENERATION_PENDING = "PENDING"
BULK_GENERATION_RUNNING = "RUNNING"
BULK_GENERATION_SUCCEEDED = "SUCCEEDED"
BULK_GENERATION_FAILED = "FAILED"


class LockedReportMonthError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_report_month_state(slug: str, year: int, month: int) -> dict:
    return {
        "slug": slug,
        "year": year,
        "month": month,
        "status": MONTH_STATUS_OPEN,
        "locked_at": None,
        "locked_by": None,
        "unlocked_at": None,
        "unlocked_by": None,
        "last_generated_at": None,
        "last_recalculated_at": None,
        "data_state": MONTH_DATA_STATE_EMPTY,
        "has_new_data_since_generation": 0,
        "notes": "",
    }


def _parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        candidate = int(pid)
    except (TypeError, ValueError):
        return False
    if candidate <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is NOT a safe liveness probe on Windows: signal 0
        # maps to CTRL_C_EVENT and goes through GenerateConsoleCtrlEvent,
        # which can interrupt a live console process group (e.g. a running
        # bulk_generation_runner). Query the process handle instead; Windows
        # has no zombie semantics, so this answers liveness completely.
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, candidate
        )
        if not handle:
            # Access denied → the process exists but we may not open it.
            return kernel32.GetLastError() == ERROR_ACCESS_DENIED
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(candidate, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    # Process exists — but it might be a zombie (defunct).
    # Try to reap it; if it's our child zombie, waitpid succeeds and
    # the process is gone.  If it's not our child, waitpid raises.
    try:
        wpid, _ = os.waitpid(candidate, os.WNOHANG)
        if wpid != 0:
            return False  # was a zombie, now reaped
    except ChildProcessError:
        # Not our child — cannot reap.  Check /proc/<pid>/stat for zombie
        # state (Linux).  After server restart, the new process is not the
        # parent, so waitpid fails, but the zombie is still dead.
        try:
            with open(f"/proc/{candidate}/stat", "r") as f:
                stat_fields = f.read().split(")")
                if len(stat_fields) >= 2:
                    state = stat_fields[1].strip().split()[0]
                    if state == "Z":
                        return False  # zombie — not alive
        except (OSError, IndexError):
            pass
    except OSError:
        pass
    return True


def _assert_report_month_mutable(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> None:
    state = get_report_month_state(conn, slug, year, month)
    if state.get("status") == MONTH_STATUS_LOCKED:
        raise LockedReportMonthError(
            f"Month {int(month):02d}/{int(year)} for {slug} is locked."
        )


def get_report_month_state(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    *,
    create: bool = False,
) -> dict:
    row = conn.execute(
        """SELECT * FROM report_month_state
           WHERE slug = ? AND year = ? AND month = ?""",
        (slug, year, month),
    ).fetchone()
    if row:
        return dict(row)
    if create:
        state = _default_report_month_state(slug, year, month)
        conn.execute(
            """INSERT INTO report_month_state
               (slug, year, month, status, data_state, has_new_data_since_generation, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                year,
                month,
                state["status"],
                state["data_state"],
                state["has_new_data_since_generation"],
                state["notes"],
            ),
        )
        conn.commit()
    return _default_report_month_state(slug, year, month)


def get_report_month_states(
    conn: sqlite3.Connection,
    *,
    slugs: list[str] | None = None,
    months: list[tuple[int, int]] | None = None,
) -> list[dict]:
    sql = "SELECT * FROM report_month_state"
    clauses: list[str] = []
    params: list = []

    if slugs:
        clean_slugs = [s for s in dict.fromkeys(slugs) if s]
        if clean_slugs:
            clauses.append(f"slug IN ({','.join('?' for _ in clean_slugs)})")
            params.extend(clean_slugs)

    if months:
        clean_months = [(int(y), int(m)) for y, m in months]
        if clean_months:
            clauses.append(
                "(" + " OR ".join("(year = ? AND month = ?)" for _ in clean_months) + ")"
            )
            for y, m in clean_months:
                params.extend([y, m])

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY slug, year, month"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_report_month_locked(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    *,
    locked: bool,
    actor: str = "",
    notes: str | None = None,
) -> dict:
    state = get_report_month_state(conn, slug, year, month, create=True)
    now = _now()
    next_notes = state.get("notes", "") if notes is None else notes
    if locked:
        conn.execute(
            """UPDATE report_month_state
               SET status = ?, locked_at = ?, locked_by = ?, notes = ?
               WHERE slug = ? AND year = ? AND month = ?""",
            (MONTH_STATUS_LOCKED, now, actor or state.get("locked_by"), next_notes, slug, year, month),
        )
    else:
        conn.execute(
            """UPDATE report_month_state
               SET status = ?, unlocked_at = ?, unlocked_by = ?, notes = ?
               WHERE slug = ? AND year = ? AND month = ?""",
            (MONTH_STATUS_OPEN, now, actor or state.get("unlocked_by"), next_notes, slug, year, month),
        )
    conn.commit()
    return get_report_month_state(conn, slug, year, month)


def touch_report_month_generation(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict:
    get_report_month_state(conn, slug, year, month, create=True)
    now = _now()
    conn.execute(
        """UPDATE report_month_state
           SET last_generated_at = ?, last_recalculated_at = ?,
               data_state = ?, has_new_data_since_generation = 0
           WHERE slug = ? AND year = ? AND month = ?""",
        (now, now, MONTH_DATA_STATE_GENERATED, slug, year, month),
    )
    conn.commit()
    return get_report_month_state(conn, slug, year, month)


def mark_report_month_has_data(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict:
    state = get_report_month_state(conn, slug, year, month, create=True)
    if state.get("last_generated_at"):
        return state
    if state.get("data_state") == MONTH_DATA_STATE_READY:
        return state
    conn.execute(
        """UPDATE report_month_state
           SET data_state = ?
           WHERE slug = ? AND year = ? AND month = ?""",
        (MONTH_DATA_STATE_READY, slug, year, month),
    )
    conn.commit()
    return get_report_month_state(conn, slug, year, month)


def mark_report_month_stale(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict:
    state = get_report_month_state(conn, slug, year, month, create=True)
    if not state.get("last_generated_at"):
        return mark_report_month_has_data(conn, slug, year, month)
    if state.get("has_new_data_since_generation"):
        return state
    conn.execute(
        """UPDATE report_month_state
           SET data_state = ?, has_new_data_since_generation = 1
           WHERE slug = ? AND year = ? AND month = ?""",
        (MONTH_DATA_STATE_STALE, slug, year, month),
    )
    conn.commit()
    return get_report_month_state(conn, slug, year, month)


def mark_report_months_stale(
    conn: sqlite3.Connection,
    month_keys: list[tuple[str, int, int]],
) -> list[dict]:
    updated = []
    seen: set[tuple[str, int, int]] = set()
    for slug, year, month in month_keys:
        key = (slug, int(year), int(month))
        if not slug or key in seen:
            continue
        seen.add(key)
        updated.append(mark_report_month_stale(conn, key[0], key[1], key[2]))
    return updated


def is_report_month_locked(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> bool:
    state = get_report_month_state(conn, slug, year, month)
    return state.get("status") == MONTH_STATUS_LOCKED


def get_latest_report_generation_job(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict | None:
    row = conn.execute(
        """SELECT * FROM report_generation_jobs
           WHERE slug = ? AND year = ? AND month = ?
           ORDER BY id DESC
           LIMIT 1""",
        (slug, year, month),
    ).fetchone()
    return dict(row) if row else None


def get_active_report_generation_job(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
) -> dict | None:
    expire_stale_report_generation_jobs(conn)
    row = conn.execute(
        """SELECT * FROM report_generation_jobs
           WHERE slug = ? AND year = ? AND month = ?
             AND status IN (?, ?)
           ORDER BY id DESC
           LIMIT 1""",
        (slug, year, month, GENERATION_JOB_PENDING, GENERATION_JOB_RUNNING),
    ).fetchone()
    return dict(row) if row else None


def expire_stale_report_generation_jobs(
    conn: sqlite3.Connection,
    *,
    pending_timeout_seconds: int = 120,
    running_timeout_seconds: int = 60 * 60,
) -> int:
    now_dt = datetime.now(timezone.utc)
    pending_cutoff = (now_dt - timedelta(seconds=int(pending_timeout_seconds))).isoformat()
    rows = conn.execute(
        """SELECT id, status, pid, created_at, started_at, updated_at
             FROM report_generation_jobs
            WHERE status IN (?, ?)""",
        (GENERATION_JOB_PENDING, GENERATION_JOB_RUNNING),
    ).fetchall()
    stale_jobs: list[tuple[int, str]] = []
    for row in rows:
        status = str(row["status"] or "")
        job_id = int(row["id"])
        if status == GENERATION_JOB_PENDING and str(row["created_at"] or "") < pending_cutoff:
            stale_jobs.append(
                (
                    job_id,
                    "Background generování nezačalo včas a job byl automaticky ukončen.",
                )
            )
            continue
        if status != GENERATION_JOB_RUNNING:
            continue
        pid = row["pid"]
        started_at = (
            _parse_iso_datetime(row["updated_at"])
            or _parse_iso_datetime(row["started_at"])
            or _parse_iso_datetime(row["created_at"])
        )
        if pid and not _pid_is_alive(pid):
            stale_jobs.append(
                (
                    job_id,
                    "Background generování bylo ukončeno mimo aplikaci a job byl automaticky uzavřen.",
                )
            )
            continue
        if started_at is None:
            continue
        age_seconds = (now_dt - started_at).total_seconds()
        if age_seconds > float(running_timeout_seconds):
            stale_jobs.append(
                (
                    job_id,
                    "Background generování překročilo časový limit a job byl automaticky ukončen.",
                )
            )
    if not stale_jobs:
        return 0
    conn.executemany(
        """UPDATE report_generation_jobs
           SET status = ?, message = ?, finished_at = ?, updated_at = ?
           WHERE id = ?""",
        [
            (
                GENERATION_JOB_FAILED,
                message,
                _now(),
                _now(),
                job_id,
            )
            for job_id, message in stale_jobs
        ],
    )
    conn.commit()
    return len(stale_jobs)


def create_report_generation_job(
    conn: sqlite3.Connection,
    slug: str,
    year: int,
    month: int,
    *,
    requested_by: str = "",
) -> dict:
    active = get_active_report_generation_job(conn, slug, year, month)
    if active:
        return active

    now = _now()
    try:
        cursor = conn.execute(
            """INSERT INTO report_generation_jobs
               (slug, year, month, status, requested_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (slug, year, month, GENERATION_JOB_PENDING, requested_by, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        active = get_active_report_generation_job(conn, slug, year, month)
        if active:
            return active
        raise
    row = conn.execute(
        "SELECT * FROM report_generation_jobs WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def set_report_generation_job_running(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    pid: int | None = None,
) -> dict | None:
    now = _now()
    conn.execute(
        """UPDATE report_generation_jobs
           SET status = ?, pid = ?, started_at = COALESCE(started_at, ?), updated_at = ?
           WHERE id = ?""",
        (GENERATION_JOB_RUNNING, pid, now, now, int(job_id)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM report_generation_jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    return dict(row) if row else None


def finish_report_generation_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str,
    message: str = "",
    detail: str = "",
) -> dict | None:
    if status not in {GENERATION_JOB_SUCCEEDED, GENERATION_JOB_FAILED}:
        raise ValueError(f"Unsupported generation job status: {status}")
    now = _now()
    conn.execute(
        """UPDATE report_generation_jobs
           SET status = ?, message = ?, detail = ?, finished_at = ?, updated_at = ?
           WHERE id = ?""",
        (status, message, detail, now, now, int(job_id)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM report_generation_jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    return dict(row) if row else None


def get_bulk_generation_run(
    conn: sqlite3.Connection,
    run_id: int,
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM bulk_generation_runs WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    return dict(row) if row else None


def get_latest_bulk_generation_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """SELECT * FROM bulk_generation_runs
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    return dict(row) if row else None


def get_active_bulk_generation_run(conn: sqlite3.Connection) -> dict | None:
    expire_stale_bulk_generation_runs(conn)
    row = conn.execute(
        """SELECT * FROM bulk_generation_runs
           WHERE status IN (?, ?)
           ORDER BY id DESC
           LIMIT 1""",
        (BULK_GENERATION_PENDING, BULK_GENERATION_RUNNING),
    ).fetchone()
    return dict(row) if row else None


def expire_stale_bulk_generation_runs(
    conn: sqlite3.Connection,
    *,
    pending_timeout_seconds: int = 120,
    running_timeout_seconds: int = 6 * 60 * 60,
) -> int:
    now_dt = datetime.now(timezone.utc)
    pending_cutoff = (now_dt - timedelta(seconds=int(pending_timeout_seconds))).isoformat()
    rows = conn.execute(
        """SELECT id, status, pid, created_at, started_at, updated_at
             FROM bulk_generation_runs
            WHERE status IN (?, ?)""",
        (BULK_GENERATION_PENDING, BULK_GENERATION_RUNNING),
    ).fetchall()
    stale_runs: list[tuple[int, str]] = []
    for row in rows:
        status = str(row["status"] or "")
        run_id = int(row["id"])
        if status == BULK_GENERATION_PENDING and str(row["created_at"] or "") < pending_cutoff:
            stale_runs.append(
                (
                    run_id,
                    "Hromadné generování nezačalo včas a běh byl automaticky ukončen.",
                )
            )
            continue
        if status != BULK_GENERATION_RUNNING:
            continue
        pid = row["pid"]
        started_at = (
            _parse_iso_datetime(row["updated_at"])
            or _parse_iso_datetime(row["started_at"])
            or _parse_iso_datetime(row["created_at"])
        )
        if pid and not _pid_is_alive(pid):
            stale_runs.append(
                (
                    run_id,
                    "Sekvenční generování bylo ukončeno mimo aplikaci a běh byl automaticky uzavřen.",
                )
            )
            continue
        if started_at is None:
            continue
        age_seconds = (now_dt - started_at).total_seconds()
        if age_seconds > float(running_timeout_seconds):
            stale_runs.append(
                (
                    run_id,
                    "Sekvenční generování překročilo časový limit a běh byl automaticky ukončen.",
                )
            )
    if not stale_runs:
        return 0
    now = _now()
    conn.executemany(
        """UPDATE bulk_generation_runs
           SET status = ?, message = ?, current_slug = ?, finished_at = ?, updated_at = ?
           WHERE id = ?""",
        [
            (
                BULK_GENERATION_FAILED,
                message,
                "",
                now,
                now,
                run_id,
            )
            for run_id, message in stale_runs
        ],
    )
    conn.commit()
    return len(stale_runs)


def create_bulk_generation_run(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    *,
    total_objects: int = 0,
    requested_by: str = "",
) -> dict:
    active = get_active_bulk_generation_run(conn)
    if active:
        return active

    now = _now()
    try:
        cursor = conn.execute(
            """INSERT INTO bulk_generation_runs
               (year, month, status, requested_by, total_objects, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                int(year),
                int(month),
                BULK_GENERATION_PENDING,
                requested_by,
                int(total_objects),
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        active = get_active_bulk_generation_run(conn)
        if active:
            return active
        raise
    return get_bulk_generation_run(conn, int(cursor.lastrowid))


def set_bulk_generation_run_running(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    pid: int | None = None,
) -> dict | None:
    now = _now()
    conn.execute(
        """UPDATE bulk_generation_runs
           SET status = ?, pid = ?, started_at = COALESCE(started_at, ?), updated_at = ?
           WHERE id = ?""",
        (BULK_GENERATION_RUNNING, pid, now, now, int(run_id)),
    )
    conn.commit()
    return get_bulk_generation_run(conn, int(run_id))


def update_bulk_generation_run_progress(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    current_slug: str | None = None,
    processed_objects: int | None = None,
    succeeded_objects: int | None = None,
    failed_objects: int | None = None,
    skipped_locked_objects: int | None = None,
    skipped_no_data_objects: int | None = None,
    skipped_running_objects: int | None = None,
    message: str | None = None,
    detail: str | None = None,
) -> dict | None:
    current = get_bulk_generation_run(conn, int(run_id))
    if not current:
        return None
    values = {
        "current_slug": current["current_slug"] if current_slug is None else str(current_slug or ""),
        "processed_objects": current["processed_objects"] if processed_objects is None else int(processed_objects),
        "succeeded_objects": current["succeeded_objects"] if succeeded_objects is None else int(succeeded_objects),
        "failed_objects": current["failed_objects"] if failed_objects is None else int(failed_objects),
        "skipped_locked_objects": current["skipped_locked_objects"] if skipped_locked_objects is None else int(skipped_locked_objects),
        "skipped_no_data_objects": current["skipped_no_data_objects"] if skipped_no_data_objects is None else int(skipped_no_data_objects),
        "skipped_running_objects": current["skipped_running_objects"] if skipped_running_objects is None else int(skipped_running_objects),
        "message": current["message"] if message is None else str(message or ""),
        "detail": current["detail"] if detail is None else str(detail or ""),
    }
    conn.execute(
        """UPDATE bulk_generation_runs
           SET current_slug = ?, processed_objects = ?, succeeded_objects = ?, failed_objects = ?,
               skipped_locked_objects = ?, skipped_no_data_objects = ?, skipped_running_objects = ?,
               message = ?, detail = ?, updated_at = ?
           WHERE id = ?""",
        (
            values["current_slug"],
            values["processed_objects"],
            values["succeeded_objects"],
            values["failed_objects"],
            values["skipped_locked_objects"],
            values["skipped_no_data_objects"],
            values["skipped_running_objects"],
            values["message"],
            values["detail"],
            _now(),
            int(run_id),
        ),
    )
    conn.commit()
    return get_bulk_generation_run(conn, int(run_id))


def finish_bulk_generation_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    message: str = "",
    detail: str = "",
    current_slug: str | None = None,
) -> dict | None:
    if status not in {BULK_GENERATION_SUCCEEDED, BULK_GENERATION_FAILED}:
        raise ValueError(f"Unsupported bulk generation status: {status}")
    current = get_bulk_generation_run(conn, int(run_id))
    if not current:
        return None
    now = _now()
    conn.execute(
        """UPDATE bulk_generation_runs
           SET status = ?, message = ?, detail = ?, current_slug = ?, finished_at = ?, updated_at = ?
           WHERE id = ?""",
        (
            status,
            str(message or ""),
            str(detail or ""),
            current["current_slug"] if current_slug is None else str(current_slug or ""),
            now,
            now,
            int(run_id),
        ),
    )
    conn.commit()
    return get_bulk_generation_run(conn, int(run_id))
