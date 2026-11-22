"""Visitor reviews: posted from either second page with an email, stored, summarised,
shown publicly only when longer than 100 words, hidden on request, exported."""

import csv
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import main, reviews

LONG = " ".join(["insightful"] * 101)       # 101 words: long enough to be shown
SHORT = " ".join(["good"] * 100)            # exactly 100 words: not shown


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWS_DB", str(tmp_path / "reviews.db"))
    reviews.reset_rate_limits()
    with TestClient(main.app) as test_client:
        yield test_client


def post(client, **fields):
    body = {"page": "engine", "rating": 4, "email": "visitor@example.com", **fields}
    return client.post("/reviews", json=body)


def test_a_review_is_stored_with_its_email_and_counted(client):
    response = post(client, rating=5, review="Great run console", suggestions="Add maps", name="Asha", email="Asha@Example.com")
    assert response.status_code == 200 and response.json()["saved"] is True and response.json()["public"] is False
    post(client, page="features", rating=4)
    summary = client.get("/reviews/summary").json()
    assert summary["count"] == 2 and summary["average"] == 4.5
    stored = reviews.all_reviews()
    assert stored[0]["review"] == "Great run console" and stored[0]["email"] == "asha@example.com" and stored[0]["name"] == "Asha"


@pytest.mark.parametrize("change", [
    {"rating": 0}, {"rating": 6}, {"page": "home"}, {"review": "x" * 4001}, {"phone": "123"},
    {"email": ""}, {"email": "not-an-email"}, {"email": "a@b"},
])
def test_bad_reviews_are_rejected(client, change):
    assert post(client, **change).status_code == 422


def test_an_email_is_required(client):
    assert client.post("/reviews", json={"page": "engine", "rating": 4}).status_code == 422


def test_only_reviews_over_100_words_are_shown_and_never_their_emails(client):
    assert post(client, review=LONG, name="Ravi").json()["public"] is True
    post(client, review=SHORT, name="Mei")
    post(client, review="Nice.", suggestions=LONG)          # a long suggestion does not count
    shown = client.get("/reviews/public").json()
    assert shown["min_words"] == 100
    assert [r["name"] for r in shown["reviews"]] == ["Ravi"]
    assert set(shown["reviews"][0]) == {"id", "date", "page", "rating", "review", "name"}
    assert "example.com" not in str(shown)


def test_a_hidden_review_leaves_the_site_but_is_kept(client):
    review_id = post(client, review=LONG, rating=1).json()["id"]
    assert reviews.set_hidden(review_id) is True
    assert client.get("/reviews/public").json()["reviews"] == []
    assert client.get("/reviews/summary").json()["count"] == 0
    assert reviews.all_reviews()[0]["hidden"] == 1
    reviews.set_hidden(review_id, hidden=False)
    assert len(client.get("/reviews/public").json()["reviews"]) == 1


def test_one_visitor_cannot_flood_the_reviews(client):
    headers = {"X-Forwarded-For": "203.0.113.7"}
    codes = [client.post("/reviews", json={"page": "engine", "rating": 3, "email": "a@b.co"}, headers=headers).status_code for _ in range(reviews.RATE_LIMIT + 1)]
    assert codes[:-1] == [200] * reviews.RATE_LIMIT and codes[-1] == 429
    assert client.post("/reviews", json={"page": "engine", "rating": 3, "email": "c@d.co"}, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200


def test_reviews_stored_before_emails_existed_still_load(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, page TEXT NOT NULL, rating INTEGER NOT NULL, "
                    "review TEXT NOT NULL DEFAULT '', suggestions TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '')")
        old.execute("INSERT INTO reviews (created_at, page, rating, review) VALUES ('2026-11-21T10:00:00+00:00', 'engine', 5, ?)", (LONG,))
    monkeypatch.setenv("REVIEWS_DB", str(path))
    assert reviews.all_reviews()[0]["email"] == "" and len(reviews.public()) == 1


def test_export_includes_emails_for_the_team(client):
    post(client, rating=2, review="Slow, but useful", email="team@example.org")
    assert client.get("/reviews").status_code in (404, 405)
    out = io.StringIO()
    assert reviews.export_csv(out) == 1
    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert rows[0]["rating"] == "2" and rows[0]["email"] == "team@example.org" and rows[0]["hidden"] == "0"
