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

    uvicorn.run(
        "report.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
