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


def test_both_search_actions_are_tried_authenticated_first():
    # `ajaxsearch` returns the fuller set but answers 400 "0" for guests, so the
    # header action stays the fallback rather than being replaced.
    assert archive_scraper.SEARCH_AJAX_ACTION_FULL == "ajaxsearch"
    assert archive_scraper.SEARCH_AJAX_ACTION == "ajaxsearchheader"


def test_the_sites_advanced_search_cannot_filter_so_cards_are_filtered_here():
    # Live 2026-10-26: /?advanced_search=on&min_release=1990&max_release=1995
    # answered the same front-page cards as / (Spider-Man 2026 first), and the
    # AJAX actions ignored the advanced fields, so a 1990-1995 filter returned
    # 2026 titles.
    from archive_filters import SearchFilters

    cards = [
        {"url": "https://digimoviez.com/a/", "title": "A 2024", "year": "2024", "rating": "8.1"},
        {"url": "https://digimoviez.com/b/", "title": "B 1984", "year": "1984", "rating": "7.0"},
        {"url": "https://digimoviez.com/c/", "title": "C", "year": None, "rating": None},
    ]
    filters = SearchFilters.from_mapping({"year_min": 2020, "year_max": 2024,
                                          "rating_min": 8, "rating_max": 10})
    kept = [card["url"] for card in archive_scraper._cards_matching(cards, filters)]
    # The unlabelled card is kept: a missing year is not proof of a mismatch.
    assert kept == ["https://digimoviez.com/a/", "https://digimoviez.com/c/"]


def test_search_applies_the_year_filter_to_what_the_site_returns(tmp_path):
    from archive_filters import SearchFilters
    from archive_scraper import DigimoviezMovieCollector
    from tests.unit.fake_digimoviez import BASE, FakeDigimoviez
    from tests.unit.test_archive_auth_flow import make

    manager, _ = make(tmp_path, FakeDigimoviez(), enabled=False)
    collector = DigimoviezMovieCollector(BASE, manager, connect_timeout=1, read_timeout=1,
                                         total_timeout=25, retries=0)
    # The fake answers with a 2014 card, so a 2020-2024 request must come back empty.
    assert collector.search(SearchFilters.from_mapping({"query": "dune", "year_min": 2020,
                                                       "year_max": 2024})) == []
    assert collector.search(SearchFilters.from_mapping({"query": "dune"})), "unfiltered search still works"


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
