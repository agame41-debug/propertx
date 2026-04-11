"""
Background runner for report generation jobs started from the web UI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from report.db import (
    GENERATION_JOB_FAILED,
    GENERATION_JOB_SUCCEEDED,
    finish_report_generation_job,
    get_connection,
    set_report_generation_job_running,
)


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _summarize_generation_error(detail: str) -> str:
    lines = [line.strip() for line in str(detail or "").splitlines() if line.strip()]
    if not lines:
        return "Generování reportu selhalo."
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line
    return lines[-1]


def _truncate_generation_detail(detail: str, max_chars: int = 40000) -> str:
    text = str(detail or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n... output truncated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a background Rentero generation job.")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()

    conn = get_connection(args.db_path)
    try:
        set_report_generation_job_running(conn, args.job_id, pid=os.getpid())
        cmd = [
            sys.executable,
            "-m",
            "report.main",
            "--year",
            str(args.year),
            "--month",
            str(args.month),
            "--property",
            args.slug,
            "--overwrite",
            "--legacy-autodiscover",
            "--config",
            args.config,
        ]
        result = subprocess.run(
            cmd,
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        detail = _truncate_generation_detail((result.stderr or result.stdout or "").strip())
        if result.returncode != 0:
            finish_report_generation_job(
                conn,
                args.job_id,
                status=GENERATION_JOB_FAILED,
                message=_summarize_generation_error(detail),
                detail=detail,
            )
            return result.returncode

        finish_report_generation_job(
            conn,
            args.job_id,
            status=GENERATION_JOB_SUCCEEDED,
            message=f"Report pro {args.month:02d}/{args.year} byl úspěšně vygenerován.",
            detail=detail,
        )
        return 0
    except Exception as exc:
        finish_report_generation_job(
            conn,
            args.job_id,
            status=GENERATION_JOB_FAILED,
            message="Background generování reportu selhalo.",
            detail=_truncate_generation_detail(str(exc)),
        )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
