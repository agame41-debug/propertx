"""bin/regen.py — admin CLI wrapping engine.generate_report_in_process.

Two invocation modes:
    python bin/regen.py SLUG YEAR MONTH        — single property
    python bin/regen.py --all YEAR MONTH       — all active properties
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest


def _load_regen():
    """Load bin/regen.py as a module — bin/ isn't a package."""
    spec = importlib.util.spec_from_file_location(
        "regen",
        pathlib.Path(__file__).parent.parent / "bin" / "regen.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parser_accepts_single_property_form():
    regen = _load_regen()
    args = regen.parse_args(["my-slug", "2026", "4"])
    assert args.slug == "my-slug"
    assert args.year == 2026
    assert args.month == 4
    assert args.all is False


def test_parser_accepts_all_form():
    regen = _load_regen()
    args = regen.parse_args(["--all", "2026", "4"])
    assert args.all is True
    assert args.year == 2026
    assert args.month == 4
    assert args.slug is None


def test_parser_rejects_invalid_month_high():
    regen = _load_regen()
    with pytest.raises(SystemExit):
        regen.parse_args(["slug", "2026", "13"])


def test_parser_rejects_invalid_month_low():
    regen = _load_regen()
    with pytest.raises(SystemExit):
        regen.parse_args(["slug", "2026", "0"])


def test_parser_rejects_unreasonable_year():
    regen = _load_regen()
    with pytest.raises(SystemExit):
        regen.parse_args(["slug", "1999", "4"])


def test_parser_rejects_slug_with_all():
    regen = _load_regen()
    with pytest.raises(SystemExit):
        regen.parse_args(["--all", "myslug", "2026", "4"])


def test_parser_rejects_no_slug_no_all():
    regen = _load_regen()
    with pytest.raises(SystemExit):
        regen.parse_args(["2026", "4"])


def test_help_message_runs_without_error():
    """--help should print and SystemExit(0)."""
    regen = _load_regen()
    with pytest.raises(SystemExit) as exc:
        regen.parse_args(["--help"])
    assert exc.value.code == 0
