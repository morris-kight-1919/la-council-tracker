"""Smoke tests: the app boots and every page renders, in both modes.

Uses stdlib unittest + Flask's own built-in test client -- no pytest
dependency needed, keeping this consistent with the "only Flask itself"
rule. Run with:  python3 -m unittest tests/test_seed_smoke.py  (from the
project root, inside the venv).

LiveModeTest covers the real, default configuration (SEED_MODE=0, reading
corpus.db/watchlist.json) -- this is what actually ships. SeedModeTest
covers the placeholder-data demo path (SEED_MODE=1), which still exists
as an option but is no longer the default.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


def get_csrf_token(html_body):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html_body)
    return m.group(1) if m else None


class LiveModeTest(unittest.TestCase):
    """The real configuration: reads the actual corpus.db/watchlist.json."""

    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        assert app.config["SEED_MODE"] is False, "default should be real data, not seed mode"
        self.client = app.test_client()

    def assert_ok(self, path, *snippets):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, f"{path} returned {resp.status_code}")
        body = resp.get_data(as_text=True)
        for s in snippets:
            self.assertIn(s, body, f"{path} did not contain {s!r}")
        return resp

    def test_today(self):
        self.assert_ok("/today", "Today", "Needs Review", "Upcoming Hearings")

    def test_inbox(self):
        self.assert_ok("/inbox", "Inbox")

    def test_watchlist(self):
        self.assert_ok("/watchlist", "Watchlist")

    def test_council_file_detail_known(self):
        self.assert_ok("/council-files/20-0263", "20-0263", "Timeline", "Documents")

    def test_council_file_detail_404(self):
        resp = self.client.get("/council-files/00-0000")
        self.assertEqual(resp.status_code, 404)

    def test_sources(self):
        self.assert_ok("/sources", "Sources")

    def test_calendar(self):
        self.assert_ok("/calendar", "Calendar")

    def test_rules_readonly(self):
        self.assert_ok("/rules", "What to Watch", "Read-only for now")

    def test_activity(self):
        self.assert_ok("/activity", "Activity", "Pipeline run log")

    def test_search(self):
        self.assert_ok("/search?q=elysian", "Search")

    def test_remaining_stub_pages(self):
        for path in ("/council-files", "/settings"):
            self.assert_ok(path, "Coming soon")

    def test_root_shows_today_content(self):
        self.assert_ok("/", "Today")

    def test_post_without_csrf_token_rejected(self):
        resp = self.client.post("/inbox/1/decide", data={"decision": "confirmed"})
        self.assertEqual(resp.status_code, 400)

    def test_post_with_csrf_token_accepted(self):
        # Any outbox id works for this check -- a nonexistent one still
        # passes CSRF (400 only on a missing/invalid token) and just writes
        # a review row nothing will ever read.
        resp = self.client.get("/inbox")
        token = get_csrf_token(resp.get_data(as_text=True))
        self.assertIsNotNone(token, "inbox page should render a csrf_token hidden field")
        resp = self.client.post("/inbox/999999999/decide",
                                 data={"decision": "deferred", "csrf_token": token})
        self.assertIn(resp.status_code, (200, 302), "valid CSRF token should not be rejected")


class SeedModeTest(unittest.TestCase):
    """The placeholder-data demo path -- still supported, not the default."""

    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True, SEED_MODE=True)
        self.client = app.test_client()

    def assert_ok(self, path, *snippets):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, f"{path} returned {resp.status_code}")
        body = resp.get_data(as_text=True)
        for s in snippets:
            self.assertIn(s, body, f"{path} did not contain {s!r}")

    def test_today(self):
        self.assert_ok("/today", "Today", "Elysian Park")

    def test_inbox(self):
        self.assert_ok("/inbox", "Inbox", "Elysian Park", "Confirm")

    def test_council_file_detail(self):
        self.assert_ok("/council-files/26-0386", "26-0386", "Timeline")

    def test_seed_write_actions_are_noop_with_flash(self):
        resp = self.client.get("/inbox")
        token = get_csrf_token(resp.get_data(as_text=True))
        resp = self.client.post("/inbox/1/decide",
                                 data={"decision": "confirmed", "csrf_token": token},
                                 follow_redirects=True)
        self.assertIn("Demo mode", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
