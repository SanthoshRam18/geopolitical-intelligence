"""Visitor reviews: a star rating, what they thought, suggestions, a name and an email.

Stored in a SQLite file (REVIEWS_DB, default data/reviews.db). On the server
that folder is a Docker volume, so reviews survive redeploys.

What is public: the count and average rating, and reviews longer than
PUBLIC_MIN_WORDS words (rating, review text, name and date), which the site shows
under "Hear what people think". Emails and suggestions are never served; the team
reads everything over SSH (scripts/get_reviews.ps1), and can hide a public review
that should not be shown (scripts/hide_review.ps1).

    python -m app.reviews export        all reviews as CSV on stdout, emails included
    python -m app.reviews summary       count and average rating
    python -m app.reviews hide ID       keep review ID off the site
    python -m app.reviews unhide ID     show it again (if it is long enough)
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PAGES = ("features", "engine")
PUBLIC_MIN_WORDS = 100        # a review is shown on the site only when longer than this
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# A visitor may post this many reviews per window; enough for honest use, not for spam.
RATE_LIMIT = 5
RATE_WINDOW_S = 3600
COLUMNS = ["id", "created_at", "page", "rating", "review", "suggestions", "name", "email", "hidden"]

_lock = threading.Lock()
_recent: dict[str, list[float]] = {}


def db_path() -> Path:
    return Path(os.environ.get("REVIEWS_DB", "data/reviews.db"))


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS reviews (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at TEXT NOT NULL,
               page TEXT NOT NULL,
               rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
               review TEXT NOT NULL DEFAULT '',
               suggestions TEXT NOT NULL DEFAULT '',
               name TEXT NOT NULL DEFAULT '',
               email TEXT NOT NULL DEFAULT '',
               hidden INTEGER NOT NULL DEFAULT 0
           )"""
    )
    # Tables created before email and hiding existed get the new columns.
    present = {row[1] for row in connection.execute("PRAGMA table_info(reviews)")}
    if "email" not in present:
        connection.execute("ALTER TABLE reviews ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "hidden" not in present:
        connection.execute("ALTER TABLE reviews ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
    return connection


@contextmanager
def _db():
    """A connection that commits on success and is always closed."""
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def allow(visitor: str) -> bool:
    """Rate limit per visitor. The address is hashed and kept only in memory."""
    key = hashlib.sha256(visitor.encode("utf-8")).hexdigest()[:16]
    now = time.monotonic()
    with _lock:
        stamps = [t for t in _recent.get(key, []) if now - t < RATE_WINDOW_S]
        if len(stamps) >= RATE_LIMIT:
            _recent[key] = stamps
            return False
        stamps.append(now)
        _recent[key] = stamps
        return True


def add(page: str, rating: int, review: str = "", suggestions: str = "", name: str = "", email: str = "") -> dict:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _lock, _db() as connection:
        cursor = connection.execute(
            "INSERT INTO reviews (created_at, page, rating, review, suggestions, name, email) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (created, page, int(rating), review.strip(), suggestions.strip(), name.strip(), email.strip().lower()),
        )
        review_id = cursor.lastrowid
    return {"id": review_id, "created_at": created, "public": word_count(review) > PUBLIC_MIN_WORDS}


def summary() -> dict:
    with _lock, _db() as connection:
        rows = connection.execute("SELECT page, COUNT(*), AVG(rating) FROM reviews WHERE hidden = 0 GROUP BY page").fetchall()
    by_page = {page: {"count": count, "average": round(avg, 2)} for page, count, avg in rows}
    total = sum(p["count"] for p in by_page.values())
    average = round(sum(p["count"] * p["average"] for p in by_page.values()) / total, 2) if total else None
    return {"count": total, "average": average, "by_page": by_page}


def public(limit: int = 50) -> list[dict]:
    """Reviews longer than PUBLIC_MIN_WORDS words and not hidden, newest first:
    only the rating, text, name, page and date."""
    with _lock, _db() as connection:
        rows = connection.execute("SELECT id, created_at, page, rating, review, name FROM reviews WHERE hidden = 0 ORDER BY id DESC").fetchall()
    shown = []
    for review_id, created, page, rating, text, name in rows:
        if word_count(text) > PUBLIC_MIN_WORDS:
            shown.append({"id": review_id, "date": created[:10], "page": page, "rating": rating, "review": text, "name": name or "Anonymous"})
            if len(shown) >= limit:
                break
    return shown


def set_hidden(review_id: int, hidden: bool = True) -> bool:
    with _lock, _db() as connection:
        cursor = connection.execute("UPDATE reviews SET hidden = ? WHERE id = ?", (1 if hidden else 0, int(review_id)))
        return cursor.rowcount == 1


def all_reviews() -> list[dict]:
    with _lock, _db() as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT {', '.join(COLUMNS)} FROM reviews ORDER BY id")]


def export_csv(out=sys.stdout) -> int:
    rows = all_reviews()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return len(rows)


def reset_rate_limits() -> None:
    with _lock:
        _recent.clear()


if __name__ == "__main__":
    command: Optional[str] = sys.argv[1] if len(sys.argv) > 1 else None
    if command == "export":
        export_csv()
    elif command == "summary":
        print(summary())
    elif command in ("hide", "unhide") and len(sys.argv) > 2:
        done = set_hidden(int(sys.argv[2]), hidden=command == "hide")
        print(f"review {sys.argv[2]} {'hidden' if command == 'hide' else 'shown'}" if done else f"no review {sys.argv[2]}")
    else:
        print(__doc__)
