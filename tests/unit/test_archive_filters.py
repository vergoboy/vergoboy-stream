from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from archive_filters import FilterValidationError, SearchFilters, build_search_request


def test_every_supported_filter_is_encoded():
    raw = {
        "query": "فیلم & test", "type": "post", "director": "Christopher Nolan",
        "actors": "Leonardo DiCaprio", "country": "Iran", "age_rating": "PG-13",
        "quality": "1080p WEB-DL SoftSub", "sort": "imdb_rate",
        "year_min": 2000, "year_max": 2020, "rating_min": 7.5, "rating_max": 9,
    }
    request = build_search_request(SearchFilters.from_mapping(raw), "https://digimoviez.com")
    query = parse_qs(urlsplit(request["url"]).query)
    assert query == {key: [str(value)] for key, value in request["params"].items()}
    assert query["adv_post_type"] == ["post"]
    assert query["avg_director"] == ["Christopher Nolan"]
    assert query["adv_cast"] == ["Leonardo DiCaprio"]
    assert query["min_release"] == ["2000"] and query["max_rate"] == ["9.0"]


@pytest.mark.parametrize("raw", [
    {"year_min": 2021, "year_max": 2020}, {"year_min": 1800},
    {"rating_min": 9, "rating_max": 2}, {"rating_max": 11},
    {"country": "invented country"}, {"quality": "invented quality"},
])
def test_invalid_filters_fail_cleanly(raw):
    with pytest.raises(FilterValidationError):
        SearchFilters.from_mapping(raw)


def test_empty_filters_omit_optional_parameters():
    params = SearchFilters.from_mapping({}).query_params()
    assert "s" not in params and "adv_country" not in params
    assert params["advanced_search"] == "on"
