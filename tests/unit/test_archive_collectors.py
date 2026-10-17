from pathlib import Path
from types import SimpleNamespace

import archive_scraper
from archive_scraper import enabled_collectors

# Captured from the live site (2026-10-16): POST admin-ajax.php with the
# header search's action, answered as a JSON string of HTML.
HEADER_AJAX_FIXTURE = Path(__file__).parents[1] / "fixtures" / "archive" / "ajax_search_header_interstellar.json"


def test_only_movie_collector_is_registered():
    config = SimpleNamespace(ARCHIVE_MOVIES_ENABLED=True, ARCHIVE_SERIES_ENABLED=False,
        ARCHIVE_ANIME_ENABLED=False, ARCHIVE_ANIMATION_ENABLED=False)
    assert enabled_collectors(config) == ("digimoviez",)
    # The registry has no path that creates the legacy collectors, so disabled
    # categories have zero jobs and zero network requests by construction.


def test_no_archive_workers_when_movies_disabled():
    config = SimpleNamespace(ARCHIVE_MOVIES_ENABLED=False, ARCHIVE_SERIES_ENABLED=False,
        ARCHIVE_ANIME_ENABLED=False, ARCHIVE_ANIMATION_ENABLED=False)
    assert enabled_collectors(config) == ()


def test_search_uses_the_live_header_ajax_action():
    # The site's WP 7.1.2 update retired `ajaxsearch` for guests: it answers
    # 400 with body "0" (verified from two networks, 2026-10-16) while the
    # header action `ajaxsearchheader` answers 200 with real cards. The old
    # constant is what produced «دیجی‌موویز درخواست را رد کرد» for every search.
    assert archive_scraper.SEARCH_AJAX_ACTION == "ajaxsearchheader"


def test_header_ajax_response_parses_into_cards():
    raw = HEADER_AJAX_FIXTURE.read_text(encoding="utf-8")
    page = archive_scraper._unwrap_ajax_payload(raw)
    assert page.lstrip().startswith("<ul>"), "fixture must be the JSON-string form the site returns"

    results: list[dict] = []
    archive_scraper._cards_from(page, "https://digimoviez.com", set(), results)
    urls = [card["url"] for card in results]
    assert "https://digimoviez.com/interstellar-2014/" in urls, urls

    card = next(c for c in results if c["url"] == "https://digimoviez.com/interstellar-2014/")
    assert card["title"] == "Interstellar 2014"
    assert card["rating"] == "8.7"
    assert card["year"] == "2014"
    assert card["poster"] and card["poster"].startswith("https://digimoviez.com/wp-content/uploads/")
    # Every card in the fixture resolves to a single-root movie slug.
    assert len(results) >= 3
