"""
history.py — day-by-day GDELT history per country
=================================================

The live pipeline (/analyze) studies one day in depth and keeps only the last
five raw files. Questions such as "which day in the last five years was New
Zealand's most conflictual?" need every day, but only a few numbers per country
per day. This module builds that index from GDELT 1.0:

    data/history/days/YYYYMMDD.parquet     one row per country: event count, mean,
                                           min and max Goldstein, conflict share,
                                           article count, mean tone
    data/history/events/YYYYMMDD.parquet   per country, the day's three most
                                           severe and two most cooperative events,
                                           so an answer can cite them
    data/history/missing.json              days GDELT has no events for

A country's events are the rows where it is Actor1 or Actor2 (counted once when
it is both), the same rule /analyze uses.

Two kinds of source file, read differently:

  * From 2013-04-01, one export per day (YYYYMMDD.export.CSV.zip). The day is
    the file's date, as in /analyze, and each event carries its source URL.
  * Before that, GDELT's historical backfile: one file per year up to 2005
    (YYYY.zip), then one per month to March 2013 (YYYYMM.zip). These have no
    source URLs, and the day is the event's own date (SQLDATE). They are read
    in chunks, since a year is over a gigabyte unzipped. GDELT drew on far fewer
    news sources then, so these days have far fewer events.

A background thread (start_background) fills the index newest-first over the
last HISTORY_DAYS days (five years by default), then tops it up as GDELT
publishes new days; days that fall out of the window are dropped. With the
default window only daily files are needed; setting HISTORY_START (e.g.
20000101) makes the window fixed and reaches into the backfile, read after the
daily files, newest file first. Queries read an in-memory table of the day rows;
a day's events are read from disk only for the days an answer cites.

Environment:
    HISTORY_BACKFILL   "0" disables the background thread (default on)
    HISTORY_DAYS       how many days back to index (default 1830, five years)
    HISTORY_START      optional fixed first day, YYYYMMDD; overrides HISTORY_DAYS
    HISTORY_WORKERS    parallel downloads of daily files (default 2)
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

log = logging.getLogger("history")

DATA_DIR = Path("data")
HISTORY_DIR = DATA_DIR / "history"
GDELT_BASE = "https://data.gdeltproject.org/events"
DAILY_FIRST_DAY = date(2013, 4, 1)    # GDELT 1.0 daily exports start here
ARCHIVE_FIRST_DAY = date(1979, 1, 1)  # the historical backfile starts here
YEARLY_ARCHIVE_LAST = 2005            # one backfile per year up to here, then per month
RECENT_GRACE_DAYS = 3                 # a missing recent day may still be published
ARCHIVE_CHUNK_ROWS = 1_000_000

# GDELT 1.0 columns (0-based). The daily export has 58 columns, the last being the
# source URL; the backfile has 57 and no URL. Both start with the same 57.
COLUMNS = {6: "actor1", 7: "a1c", 16: "actor2", 17: "a2c", 26: "event_code", 29: "quad", 30: "goldstein", 33: "articles", 34: "tone"}
DAILY_COLUMNS = {**COLUMNS, 57: "url"}
ARCHIVE_COLUMNS = {1: "date", **COLUMNS}
_TEXT = {6: str, 7: str, 16: str, 17: str, 26: str, 57: str, 1: str}
KEEP = ["country", "actor1", "a1c", "actor2", "a2c", "event_code", "goldstein", "articles", "tone", "url"]

EVENT_ROOTS = {
    "01": "Verbal Cooperation", "02": "Material Cooperation", "03": "Diplomatic Cooperation", "04": "Consultation",
    "05": "Mediation", "06": "Engagement", "07": "Provision of Aid", "08": "Yield / Concession",
    "09": "Investigation", "10": "Demand", "11": "Disapprove", "12": "Reject", "13": "Threaten",
    "14": "Protest", "15": "Exhibit Force", "16": "Reduce Relations", "17": "Coerce", "18": "Assault",
    "19": "Fight", "20": "Mass Violence",
}


def _fixed_start() -> Optional[date]:
    raw = os.environ.get("HISTORY_START", "").strip()
    try:
        return max(ARCHIVE_FIRST_DAY, datetime.strptime(raw, "%Y%m%d").date()) if raw else None
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Aggregating rows
# ---------------------------------------------------------------------------
def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    for column in ("goldstein", "articles", "tone", "quad"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "url" not in frame:
        frame["url"] = None
    return frame.dropna(subset=["goldstein"])


def _country_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, country involved): Actor1's country, and Actor2's when different."""
    first = frame[frame["a1c"].notna() & (frame["a1c"] != "")]
    second = frame[frame["a2c"].notna() & (frame["a2c"] != "") & (frame["a2c"] != frame["a1c"])]
    rows = pd.concat([first.assign(country=first["a1c"]), second.assign(country=second["a2c"])], ignore_index=True)
    if not rows.empty:
        rows["articles"] = rows["articles"].fillna(1).astype(int)
        rows["conflict"] = rows["quad"] >= 3            # QuadClass 3 verbal and 4 material conflict
    return rows


def _partial(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sums and extremes per group, which can be added across chunks."""
    grouped = rows.groupby(keys)
    return pd.DataFrame({
        "events": grouped.size(),
        "g_sum": grouped["goldstein"].sum(),
        "min_goldstein": grouped["goldstein"].min(),
        "max_goldstein": grouped["goldstein"].max(),
        "conflict_n": grouped["conflict"].sum(),
        "articles": grouped["articles"].sum(),
        "tone_sum": grouped["tone"].sum(),
        "tone_n": grouped["tone"].count(),
    })


def _candidates(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Each group's three most severe and two most cooperative events."""
    worst = rows.sort_values([*keys, "goldstein", "articles"], ascending=[True] * len(keys) + [True, False]).groupby(keys).head(3)
    best = rows.sort_values([*keys, "goldstein", "articles"], ascending=[True] * len(keys) + [False, False]).groupby(keys).head(2)
    columns = list(dict.fromkeys([*keys, *KEEP]))
    return pd.concat([worst[columns].assign(kind="conflict"), best[columns].assign(kind="cooperation")], ignore_index=True)


def _finish(partials: list[pd.DataFrame], candidates: list[pd.DataFrame], keys: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Combine chunk partials into day rows, and keep the final cited events."""
    totals = pd.concat(partials).groupby(level=list(range(len(keys)))).agg(
        {"events": "sum", "g_sum": "sum", "min_goldstein": "min", "max_goldstein": "max", "conflict_n": "sum",
         "articles": "sum", "tone_sum": "sum", "tone_n": "sum"})
    days = pd.DataFrame({
        "events": totals["events"],
        "avg_goldstein": totals["g_sum"] / totals["events"],
        "min_goldstein": totals["min_goldstein"],
        "max_goldstein": totals["max_goldstein"],
        "conflict_share": totals["conflict_n"] / totals["events"],
        "articles": totals["articles"],
        "avg_tone": totals["tone_sum"] / totals["tone_n"].where(totals["tone_n"] > 0),
    })
    pool = pd.concat(candidates, ignore_index=True)
    worst = pool[pool["kind"] == "conflict"].sort_values([*keys, "goldstein", "articles"], ascending=[True] * len(keys) + [True, False]).groupby(keys).head(3)
    best = pool[pool["kind"] == "cooperation"].sort_values([*keys, "goldstein", "articles"], ascending=[True] * len(keys) + [False, False]).groupby(keys).head(2)
    # How widely the day's most severe / most cooperative event was reported.
    days["worst_articles"] = worst.groupby(keys)["articles"].first()
    days["best_articles"] = best.groupby(keys)["articles"].first()
    days = days.reset_index().round({"avg_goldstein": 3, "conflict_share": 3, "avg_tone": 3})
    return days, pd.concat([worst, best], ignore_index=True)


def aggregate(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one row per country, the events kept for citing) for one day's rows."""
    rows = _country_rows(_clean(frame))
    if rows.empty:
        return pd.DataFrame(), pd.DataFrame()
    return _finish([_partial(rows, ["country"])], [_candidates(rows, ["country"])], ["country"])


def read_export(raw: bytes) -> pd.DataFrame:
    """The columns the index needs from one zipped GDELT daily export."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            frame = pd.read_csv(handle, sep="\t", header=None, usecols=list(DAILY_COLUMNS), dtype={k: v for k, v in _TEXT.items() if k in DAILY_COLUMNS},
                                quoting=3, on_bad_lines="skip", low_memory=False)
    return frame.rename(columns=DAILY_COLUMNS)


def aggregate_archive(path: Path, period: Optional[tuple[str, str]] = None) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """Day rows and cited events for every day in one zipped backfile, read in chunks.

    ``period`` (first, last day as YYYYMMDD) drops events dated outside it."""
    partials, candidates = [], []
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            for chunk in pd.read_csv(handle, sep="\t", header=None, usecols=list(ARCHIVE_COLUMNS), dtype={k: v for k, v in _TEXT.items() if k in ARCHIVE_COLUMNS},
                                     quoting=3, on_bad_lines="skip", chunksize=ARCHIVE_CHUNK_ROWS):
                frame = _clean(chunk.rename(columns=ARCHIVE_COLUMNS))
                frame["date"] = frame["date"].astype(str).str[:8]
                if period:
                    frame = frame[(frame["date"] >= period[0]) & (frame["date"] <= period[1])]
                rows = _country_rows(frame)
                if rows.empty:
                    continue
                partials.append(_partial(rows, ["date", "country"]))
                candidates.append(_candidates(rows, ["date", "country"]))
                if len(candidates) >= 8:
                    # Keep memory flat on a year-long file: reduce the candidate pool as it grows.
                    pool = pd.concat(candidates, ignore_index=True)
                    candidates = [_candidates(pool, ["date", "country"])]
    if not partials:
        return {}
    days, events = _finish(partials, candidates, ["date", "country"])
    out = {}
    for day, day_rows in days.groupby("date"):
        out[day] = (day_rows.drop(columns="date").reset_index(drop=True), events[events["date"] == day].drop(columns="date").reset_index(drop=True))
    return out


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Which file holds a day
# ---------------------------------------------------------------------------
def archive_name(day: date) -> str:
    """The backfile holding a pre-2013-04 day: YYYY up to 2005, YYYYMM after."""
    return str(day.year) if day.year <= YEARLY_ARCHIVE_LAST else f"{day.year}{day.month:02d}"


def archive_period(name: str) -> tuple[str, str]:
    """First and last day (YYYYMMDD) a backfile covers."""
    if len(name) == 4:
        return f"{name}0101", f"{name}1231"
    year, month = int(name[:4]), int(name[4:])
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return f"{name}01", (nxt - timedelta(days=1)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------
class HistoryIndex:
    def __init__(self, root: Path = HISTORY_DIR, start: Optional[date] = None):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._table = pd.DataFrame()
        self._pending: list[pd.DataFrame] = []
        self.dates: set[str] = set()
        self.missing: set[str] = set()
        self.loaded = False
        self.running = False
        self.stage: Optional[str] = None
        self.last_error: Optional[str] = None
        self.fixed_start = start or _fixed_start()
        self.window_days = _env_int("HISTORY_DAYS", 1830)

    # -- storage
    @property
    def days_dir(self) -> Path:
        return self.root / "days"

    @property
    def events_dir(self) -> Path:
        return self.root / "events"

    def load(self) -> None:
        """Read every indexed day from disk into memory, dropping days the window no longer covers."""
        window = self.window()
        oldest = window[-1] if window else "99999999"
        for folder in (self.days_dir, self.events_dir):
            for path in folder.glob("*.parquet"):
                if path.stem < oldest:
                    path.unlink(missing_ok=True)
        frames = []
        for path in sorted(self.days_dir.glob("*.parquet")):
            try:
                frames.append(pd.read_parquet(path).assign(date=path.stem))
            except Exception as exc:  # noqa: BLE001 - a corrupt file is re-built, not fatal
                log.warning("history: dropping unreadable %s (%s)", path.name, exc)
                path.unlink(missing_ok=True)
        missing_path = self.root / "missing.json"
        missing = set(json.loads(missing_path.read_text())) if missing_path.exists() else set()
        with self._lock:
            self._table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            self._pending = []
            self.dates = {p.stem for p in self.days_dir.glob("*.parquet")}
            self.missing = missing
            self.loaded = True

    def add(self, day: str, days: pd.DataFrame, events: pd.DataFrame) -> None:
        _write(days, self.days_dir / f"{day}.parquet")
        _write(events, self.events_dir / f"{day}.parquet")
        with self._lock:
            self._pending.append(days.assign(date=day))
            self.dates.add(day)

    def mark_missing(self, days: Iterable[str]) -> None:
        with self._lock:
            self.missing.update(days)
            snapshot = sorted(self.missing)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "missing.json").write_text(json.dumps(snapshot))

    def table(self) -> pd.DataFrame:
        with self._lock:
            if self._pending:
                self._table = pd.concat([self._table, *self._pending], ignore_index=True)
                self._pending = []
            return self._table

    def events_of(self, day: str, country: str, kind: str, limit: int = 3) -> list[dict]:
        path = self.events_dir / f"{day}.parquet"
        if not path.exists():
            return []
        frame = pd.read_parquet(path)
        frame = frame[(frame["country"] == country) & (frame["kind"] == kind)].head(limit)
        return [_event_record(day, row) for row in frame.to_dict("records")]

    # -- what to build
    def window(self, today: Optional[date] = None) -> list[str]:
        """The days the index should hold, newest first."""
        end = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
        start = max(ARCHIVE_FIRST_DAY, self.fixed_start or end - timedelta(days=self.window_days - 1))
        return [(end - timedelta(days=i)).strftime("%Y%m%d") for i in range((end - start).days + 1)]

    def status(self) -> dict:
        window = self.window()
        have = [d for d in window if d in self.dates]
        return {
            "loaded": self.loaded,
            "backfill_running": self.running,
            "stage": self.stage,
            "window_start": window[-1] if window else None,
            "window_days": len(window),
            "days_indexed": len(have),
            "days_missing_upstream": sum(1 for d in window if d in self.missing),
            "first": min(have) if have else None,
            "last": max(have) if have else None,
            "last_error": self.last_error,
        }


INDEX = HistoryIndex()


def _event_record(day: str, row: dict) -> dict:
    code = str(row.get("event_code") or "")

    def actor(name, cc):
        name = name if isinstance(name, str) and name.strip() else None
        cc = cc if isinstance(cc, str) and cc.strip() else None
        return name.title() if name else (cc or "an unidentified party")

    return {
        "date": day,
        "actor1": actor(row.get("actor1"), row.get("a1c")),
        "actor2": actor(row.get("actor2"), row.get("a2c")),
        "actor1_country": row.get("a1c") if isinstance(row.get("a1c"), str) else None,
        "actor2_country": row.get("a2c") if isinstance(row.get("a2c"), str) else None,
        "event_code": code,
        "event_type": EVENT_ROOTS.get(code[:2], "Other"),
        "goldstein": _num(row.get("goldstein")),
        "num_articles": int(row["articles"]) if _num(row.get("articles")) is not None else None,
        "tone": _num(row.get("tone"), 2),
        "url": row.get("url") if isinstance(row.get("url"), str) else None,
    }


def _num(value, digits: int = 3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else round(number, digits)


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
def build_day(day: str, session: Optional[requests.Session] = None) -> Optional[tuple[pd.DataFrame, pd.DataFrame]]:
    """Download and aggregate one daily export; None when GDELT has no file for it."""
    getter = session or requests
    response = getter.get(f"{GDELT_BASE}/{day}.export.CSV.zip", timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return aggregate(read_export(response.content))


def build_archive(name: str, session: Optional[requests.Session] = None) -> Optional[dict[str, tuple[pd.DataFrame, pd.DataFrame]]]:
    """Download one backfile to a temporary file and aggregate it; None when absent."""
    getter = session or requests
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with getter.get(f"{GDELT_BASE}/{name}.zip", timeout=600, stream=True) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(dir=HISTORY_DIR, suffix=".zip", delete=False) as tmp:
            for block in response.iter_content(chunk_size=1 << 20):
                tmp.write(block)
            path = Path(tmp.name)
    try:
        return aggregate_archive(path, archive_period(name))
    finally:
        path.unlink(missing_ok=True)


def _backfill_daily(index: HistoryIndex, todo: list[str], today: date, stop: Optional[threading.Event], workers: int) -> int:
    added = 0
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Small batches, so a stop request is honoured quickly.
        for start in range(0, len(todo), workers * 4):
            if stop is not None and stop.is_set():
                break
            batch = todo[start:start + workers * 4]
            futures = {pool.submit(build_day, day, session): day for day in batch}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    built = future.result()
                except Exception as exc:  # noqa: BLE001 - one bad day must not stop the backfill
                    index.last_error = f"{day}: {type(exc).__name__}: {exc}"
                    log.warning("history: %s", index.last_error)
                    continue
                if built is None:
                    if (today - datetime.strptime(day, "%Y%m%d").date()).days > RECENT_GRACE_DAYS:
                        index.mark_missing([day])
                    continue
                days, events = built
                if not days.empty:
                    index.add(day, days, events)
                    added += 1
    return added


def _backfill_archives(index: HistoryIndex, todo: list[str], stop: Optional[threading.Event]) -> int:
    added = 0
    session = requests.Session()
    names = list(dict.fromkeys(archive_name(datetime.strptime(d, "%Y%m%d").date()) for d in todo))  # newest first
    wanted = set(todo)
    for name in names:
        if stop is not None and stop.is_set():
            break
        index.stage = f"backfile {name}"
        try:
            built = build_archive(name, session)
        except Exception as exc:  # noqa: BLE001
            index.last_error = f"{name}: {type(exc).__name__}: {exc}"
            log.warning("history: %s", index.last_error)
            continue
        first, last = archive_period(name)
        expected = {d for d in wanted if first <= d <= last}
        built = built or {}
        for day, (days, events) in built.items():
            if day in expected and not days.empty:
                index.add(day, days, events)
                added += 1
        index.mark_missing(sorted(expected - set(built)))
    return added


def backfill(index: HistoryIndex = INDEX, stop: Optional[threading.Event] = None, workers: Optional[int] = None) -> int:
    """Index every day of the window not yet indexed: daily files first, then the
    backfile, newest first. Returns days added."""
    workers = workers or _env_int("HISTORY_WORKERS", 2)
    today = datetime.now(timezone.utc).date()
    cutoff = DAILY_FIRST_DAY.strftime("%Y%m%d")
    todo = [d for d in index.window(today) if d not in index.dates and d not in index.missing]
    index.stage = "daily files"
    added = _backfill_daily(index, [d for d in todo if d >= cutoff], today, stop, workers)
    added += _backfill_archives(index, [d for d in todo if d < cutoff], stop)
    index.stage = None
    return added


def run_forever(index: HistoryIndex = INDEX, stop: Optional[threading.Event] = None, every_s: float = 3 * 3600) -> None:
    stop = stop or threading.Event()
    try:
        index.load()
    except Exception as exc:  # noqa: BLE001
        index.last_error = f"load: {type(exc).__name__}: {exc}"
        index.loaded = True
    while not stop.is_set():
        index.running = True
        try:
            added = backfill(index, stop)
            log.info("history: indexed %d new days (%d total)", added, len(index.dates))
        except Exception as exc:  # noqa: BLE001
            index.last_error = f"backfill: {type(exc).__name__}: {exc}"
        finally:
            index.running = False
        stop.wait(every_s)


_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()


def start_background() -> None:
    """Start the backfill thread once, unless HISTORY_BACKFILL=0."""
    global _THREAD
    if os.environ.get("HISTORY_BACKFILL", "1").strip().lower() in ("0", "false", "no"):
        threading.Thread(target=INDEX.load, daemon=True).start()
        return
    if _THREAD is None or not _THREAD.is_alive():
        _THREAD = threading.Thread(target=run_forever, args=(INDEX, _STOP), daemon=True, name="gdelt-history")
        _THREAD.start()


def stop_background() -> None:
    _STOP.set()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _days_between(start: str, end: str) -> list[str]:
    a, b = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
    return [(a + timedelta(days=i)).strftime("%Y%m%d") for i in range((b - a).days + 1)] if b >= a else []


def extremes(country: str, start: str, end: str, direction: str = "conflict", limit: int = 5, index: HistoryIndex = INDEX) -> dict:
    """The most conflictual (or cooperative) days for one country between two dates.

    Days are ranked by the mean Goldstein score of the country's events, among
    days with enough events for a mean to mean something: a quarter of the
    country's median daily count in that year, and at least 5. Judging each day
    against its own year matters because GDELT's volume grew many times over, so
    a typical day in 2002 has a fraction of a typical day in 2022. Separately, the
    single most severe events are the days whose worst (or best) event scored
    lowest (or highest), most widely reported first.
    """
    conflict = direction != "cooperation"
    span = _days_between(start, end)
    indexed = [d for d in span if d in index.dates]
    missing = [d for d in span if d in index.missing]
    coverage = {
        "days_in_range": len(span),
        "days_indexed": len(indexed),
        "days_missing_upstream": len(missing),
        "first": min(indexed) if indexed else None,
        "last": max(indexed) if indexed else None,
        "complete": len(indexed) + len(missing) >= len(span),
        "before_daily_files": bool(span) and span[0] < DAILY_FIRST_DAY.strftime("%Y%m%d"),
        "index": index.status(),
    }
    result = {"country_code": country, "start": start, "end": end, "direction": "conflict" if conflict else "cooperation",
              "coverage": coverage, "method": "GDELT 1.0 (daily exports from 2013-04, the historical backfile before); a country's events are those where it is Actor1 or Actor2",
              "days": [], "severe_events": [], "series": [], "baseline": None, "min_events": None}
    table = index.table()
    if table.empty:
        return result
    rows = table[(table["country"] == country) & (table["date"] >= start) & (table["date"] <= end)]
    if rows.empty:
        return result

    year = rows["date"].str[:4]
    floor = (rows.groupby(year)["events"].transform("median") * 0.25).clip(lower=5).astype(int)
    eligible = rows[rows["events"] >= floor]
    ranked = eligible.sort_values(["avg_goldstein", "events"], ascending=[conflict, False]).head(limit)
    kind = "conflict" if conflict else "cooperation"
    result["min_events"] = int(floor.min())
    result["min_events_rule"] = "a quarter of the country's median daily events in that year, at least 5"
    result["days"] = [{
        "date": row["date"],
        "events": int(row["events"]),
        "avg_goldstein": _num(row["avg_goldstein"]),
        "min_goldstein": _num(row["min_goldstein"]),
        "max_goldstein": _num(row["max_goldstein"]),
        "conflict_share": _num(row["conflict_share"]),
        "avg_tone": _num(row["avg_tone"], 2),
        "top_events": index.events_of(row["date"], country, kind),
    } for row in ranked.to_dict("records")]

    peak, reach = ("min_goldstein", "worst_articles") if conflict else ("max_goldstein", "best_articles")
    severe_days = rows.sort_values([peak, reach], ascending=[conflict, False]).head(limit)
    severe = []
    for row in severe_days.to_dict("records"):
        top = index.events_of(row["date"], country, kind, limit=1)
        if top:
            severe.append(top[0])
    result["severe_events"] = severe

    weights = rows["events"].clip(lower=1)
    result["baseline"] = {
        "avg_goldstein": _num((rows["avg_goldstein"] * weights).sum() / weights.sum()),
        "events_per_day": _num(rows["events"].mean(), 1),
        "days_with_events": int(len(rows)),
        "conflict_share": _num((rows["conflict_share"] * weights).sum() / weights.sum()),
    }

    # The shape of the whole window: weekly, monthly over three years, yearly over fifteen.
    stamps = pd.to_datetime(rows["date"], format="%Y%m%d")
    days_spanned = (stamps.max() - stamps.min()).days
    period = "W" if days_spanned <= 3 * 365 else "M" if days_spanned <= 15 * 365 else "Y"
    buckets = rows.assign(_bucket=stamps.dt.to_period(period).dt.start_time, _weighted=rows["avg_goldstein"] * weights, _w=weights)
    grouped = buckets.groupby("_bucket").agg(weighted=("_weighted", "sum"), w=("_w", "sum"), events=("events", "sum"))
    result["series_period"] = {"W": "week", "M": "month", "Y": "year"}[period]
    result["series"] = [{"start": stamp.strftime("%Y%m%d"), "avg_goldstein": _num(r.weighted / r.w), "events": int(r.events)} for stamp, r in grouped.iterrows()]
    return result
