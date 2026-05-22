"""
report/cnb.py — Czech National Bank EUR/CZK exchange rate client.

Fetches daily rates from https://api.cnb.cz/cnbapi/exrates/daily
Uses urllib only (no external dependencies).

Two-level cache:
  1. In-memory dict  — ultra-fast within one run
  2. SQLite DB       — persists across runs (cache/rentero.db)

Falls back up to 5 calendar days for weekends/holidays.
"""
import json
import logging
import urllib.request
import urllib.parse
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_CNB_BASE = "https://api.cnb.cz/cnbapi/exrates/daily"

# In-memory cache: date_str -> rate (float)
_rate_cache: dict[str, float] = {}
# Tracks whether we already loaded SQLite into memory cache this session
_db_loaded = False


class CnbRateError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  SQLite integration                                                          #
# --------------------------------------------------------------------------- #

def _get_db():
    """Lazy import to avoid circular imports."""
    from report.db import get_connection, get_all_cnb_rates, save_cnb_rate, save_cnb_rates_bulk
    return get_connection, get_all_cnb_rates, save_cnb_rate, save_cnb_rates_bulk


def _load_db_into_memory() -> None:
    """Load all stored CNB rates from SQLite into in-memory cache (once per session).

    Uses a direct sqlite3.connect() instead of report.db.get_connection() to
    avoid running migrations from this entry point. Otherwise migrations call
    _backfill_payout_batches_from_active_sources → build_airbnb_payout_data →
    _cnb_rate_for_batch_date → get_rate_for_reservation → _load_db_into_memory,
    which is an infinite recursion. Setting _db_loaded=True up-front is a belt-
    and-braces guard against any other re-entry path.
    """
    global _db_loaded
    if _db_loaded:
        return
    _db_loaded = True  # set BEFORE any DB call to break recursion
    try:
        import sqlite3
        from report.db import _DB_PATH, get_all_cnb_rates
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            stored = get_all_cnb_rates(conn)
        finally:
            conn.close()
        _rate_cache.update(stored)
        if stored:
            logger.debug("Loaded %d CNB rates from SQLite cache", len(stored))
    except Exception as e:
        logger.warning("Could not load CNB rates from SQLite: %s", e)


def _persist_rate(date_str: str, rate: float) -> None:
    """Save a single rate to SQLite."""
    try:
        get_connection, _, save_cnb_rate, _ = _get_db()
        conn = get_connection()
        save_cnb_rate(conn, date_str, rate)
        conn.close()
    except Exception as e:
        logger.warning("Could not save CNB rate to SQLite: %s", e)


def _persist_rates_bulk(rates: dict[str, float]) -> None:
    """Save multiple rates to SQLite."""
    if not rates:
        return
    try:
        get_connection, _, _, save_cnb_rates_bulk = _get_db()
        conn = get_connection()
        save_cnb_rates_bulk(conn, rates)
        conn.close()
    except Exception as e:
        logger.warning("Could not bulk-save CNB rates to SQLite: %s", e)


# --------------------------------------------------------------------------- #
#  CNB HTTP                                                                    #
# --------------------------------------------------------------------------- #

def _fetch_cnb_rate(date_str: str) -> float | None:
    """
    Make one HTTP GET to CNB API for the given date.
    Returns the EUR/CZK rate, or None if CNB has no rate for that date (holiday/weekend).
    Raises CnbRateError on HTTP or JSON errors.
    """
    params = urllib.parse.urlencode({"date": date_str, "lang": "EN"})
    url = f"{_CNB_BASE}?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise CnbRateError(f"CNB HTTP error for {date_str}: {e}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise CnbRateError(f"CNB invalid JSON for {date_str}: {e}") from e

    rates = data.get("rates") or []
    for entry in rates:
        if entry.get("currencyCode") == "EUR":
            return float(entry["rate"]) / float(entry.get("amount", 1))
    return None  # date has no rates (weekend / public holiday)


# --------------------------------------------------------------------------- #
#  Public API                                                                  #
# --------------------------------------------------------------------------- #

def get_eur_czk_rate(date_str: str, *, persist: bool = True) -> dict:
    """
    Return EUR/CZK rate for the given date string "YYYY-MM-DD".

    Lookup order:
      1. In-memory cache
      2. SQLite DB (loaded once at first call)
      3. CNB API (with fallback up to 5 days back for weekends/holidays)

    Result dict: {rate, valid_for, requested_date, from_cache}
    When `persist=False`, fetched rates stay in the in-memory cache only and are not
    written to SQLite. This is used by preview/dry-run flows that must remain read-only.
    Raises CnbRateError if no rate found within 5 days.
    """
    # Warm memory from DB on first call
    _load_db_into_memory()

    if date_str in _rate_cache:
        return {
            "rate": _rate_cache[date_str],
            "valid_for": date_str,
            "requested_date": date_str,
            "from_cache": True,
        }

    d = date.fromisoformat(date_str)
    last_exc = None

    for delta in range(6):  # try date, date-1 ... date-5
        candidate = (d - timedelta(days=delta)).isoformat()

        if candidate in _rate_cache:
            _rate_cache[date_str] = _rate_cache[candidate]
            return {
                "rate": _rate_cache[candidate],
                "valid_for": candidate,
                "requested_date": date_str,
                "from_cache": True,
            }

        try:
            rate = _fetch_cnb_rate(candidate)
        except CnbRateError as e:
            last_exc = e
            continue

        if rate is not None:
            _rate_cache[candidate] = rate
            _rate_cache[date_str] = rate
            if persist:
                _persist_rate(candidate, rate)
                if date_str != candidate:
                    _persist_rate(date_str, rate)
            logger.debug("CNB rate %s: %.3f (fetched from API)", candidate, rate)
            return {
                "rate": rate,
                "valid_for": candidate,
                "requested_date": date_str,
                "from_cache": False,
            }

    raise CnbRateError(
        f"No CNB EUR/CZK rate found for {date_str} or the 5 preceding days."
        + (f" Last error: {last_exc}" if last_exc else "")
    )


def get_rate_for_reservation(confirmed_at: str, *, persist: bool = True) -> dict:
    """
    Convenience wrapper: accepts ISO datetime string (e.g. "2026-03-15 09:22:11")
    or date string, extracts date part, calls get_eur_czk_rate().
    """
    date_part = confirmed_at[:10]  # "YYYY-MM-DD"
    return get_eur_czk_rate(date_part, persist=persist)


def preload_rates_for_month(year: int, month: int, *, persist: bool = True) -> dict[str, float]:
    """
    Ensure CNB rates for all days in the given month are available.

    1. Load DB into memory (if not already done).
    2. Fetch from API only the days not yet cached.
    3. Persist new rates to SQLite in bulk unless `persist=False`.

    Returns {date_str: rate} for all available dates in the month.
    Raises CnbRateError only if API is completely unreachable AND no cached data exists.
    """
    from calendar import monthrange
    _load_db_into_memory()

    _, last_day = monthrange(year, month)
    results: dict[str, float] = {}
    to_fetch: list[str] = []

    for day in range(1, last_day + 1):
        ds = f"{year}-{month:02d}-{day:02d}"
        if ds in _rate_cache:
            results[ds] = _rate_cache[ds]
        else:
            to_fetch.append(ds)

    if to_fetch:
        logger.debug("Fetching %d CNB rates from API for %d-%02d", len(to_fetch), year, month)

    new_rates: dict[str, float] = {}
    http_errors = 0

    for ds in to_fetch:
        try:
            rate = _fetch_cnb_rate(ds)
            if rate is not None:
                _rate_cache[ds] = rate
                results[ds] = rate
                new_rates[ds] = rate
        except CnbRateError:
            http_errors += 1

    # Bulk persist new rates
    if persist:
        _persist_rates_bulk(new_rates)

    if http_errors > 0 and not results:
        raise CnbRateError(
            f"CNB API unreachable: all days in {year}-{month:02d} failed and no cached data."
        )

    logger.info(
        "CNB rates for %d-%02d: %d from cache, %d fetched from API",
        year, month, len(results) - len(new_rates), len(new_rates)
    )
    return results
