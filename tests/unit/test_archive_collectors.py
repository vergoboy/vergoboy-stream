from types import SimpleNamespace

from archive_scraper import enabled_collectors


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
