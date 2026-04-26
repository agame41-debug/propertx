"""
Rentero webový přehled — spouštěč.

Použití:
    python3.14 run_web.py
    python3.14 run_web.py --port 8080

Poté otevřete: http://localhost:8000
Přihlašovací údaje: RENTERO_USERNAME / configured password
"""

import argparse
import os
import uvicorn

# Load .env file first
from dotenv import load_dotenv
load_dotenv()

from report.web import _get_auth_credentials, _validate_web_runtime_config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rentero web UI")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reload", action="store_true", default=False)
    args = parser.parse_args()
    _validate_web_runtime_config()
    username, _password = _get_auth_credentials()

    print(f"\n  Rentero běží na http://{args.host}:{args.port}")
    print(f"  Přihlášení: {username} / <configured password>\n")

    # Pinned to a single worker on purpose. The Hostify sync loop, the
    # import-triggered regeneration thread, and the bulk_generation_runner
    # subprocess all assume there is exactly one of them per host. Running
    # uvicorn with workers > 1 would start N parallel sync loops hitting
    # Hostify, race on report_rows writes, and split the cnb._rate_cache
    # across processes. See _enforce_single_worker() in report/web.py for
    # the corresponding runtime guard.
    uvicorn.run(
        "report.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
    )
