"""Validated Digimoviez advanced-search requests.

``i.txt`` is a captured form from the target and is deliberately the single
source of truth for select values and parameter names.  Keeping this boundary
separate means browser input never becomes a partially constructed URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


MIN_YEAR = 1888


class FilterValidationError(ValueError):
    pass


class _FormOptions(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current: str | None = None
        self.values: dict[str, list[str]] = {}
        self._option: str | None = None
        self._in_option = False
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        if tag == "select":
            self.current = attrs_d.get("name")
        elif tag == "option" and self.current:
            self._option = attrs_d.get("value")
            self._in_option = True
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._in_option:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self.current:
            value = self._option or "".join(self._text).strip()
            if value and value != "0":
                self.values.setdefault(self.current, []).append(value)
            self._option = None
            self._in_option = False
        elif tag == "select":
            self.current = None


def available_options(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Read allowed select values from the checked-in captured form."""
    path = path or Path(__file__).with_name("i.txt")
    parser = _FormOptions()
    parser.feed(path.read_text(encoding="utf-8"))
    return {name: tuple(dict.fromkeys(values)) for name, values in parser.values.items()}


OPTIONS = available_options()


def _text(value: Any, field: str, max_length: int = 120) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FilterValidationError(f"{field} must be text")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise FilterValidationError(f"{field} is too long")
    return value


def _choice(value: Any, field: str, option_name: str) -> str | None:
    value = _text(value, field)
    if value is None:
        return None
    if value not in OPTIONS.get(option_name, ()):
        raise FilterValidationError(f"unsupported {field}")
    return value


@dataclass(frozen=True)
class SearchFilters:
    query: str | None = None
    type: str | None = None
    director: str | None = None
    actors: str | None = None
    country: str | None = None
    age_rating: str | None = None
    quality: str | None = None
    sort: str | None = None
    year_min: int = MIN_YEAR
    year_max: int = date.today().year
    rating_min: float = 0.0
    rating_max: float = 10.0

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SearchFilters":
        try:
            year_min = int(raw.get("year_min", MIN_YEAR))
            year_max = int(raw.get("year_max", date.today().year))
            rating_min = float(raw.get("rating_min", 0))
            rating_max = float(raw.get("rating_max", 10))
        except (TypeError, ValueError) as exc:
            raise FilterValidationError("year and rating ranges must be numeric") from exc
        current_year = date.today().year
        if not MIN_YEAR <= year_min <= current_year or not MIN_YEAR <= year_max <= current_year:
            raise FilterValidationError("year is outside the supported range")
        if year_min > year_max:
            raise FilterValidationError("year_min cannot exceed year_max")
        if not 0 <= rating_min <= 10 or not 0 <= rating_max <= 10:
            raise FilterValidationError("rating is outside the supported range")
        if rating_min > rating_max:
            raise FilterValidationError("rating_min cannot exceed rating_max")
        media_type = _text(raw.get("type"), "type")
        if media_type not in (None, "post", "series"):
            raise FilterValidationError("type must be post or series")
        return cls(
            query=_text(raw.get("query") or raw.get("q"), "query", 80),
            type=media_type,
            director=_text(raw.get("director"), "director"),
            actors=_text(raw.get("actors"), "actors"),
            country=_choice(raw.get("country"), "country", "adv_country"),
            age_rating=_choice(raw.get("age_rating"), "age_rating", "adv_age"),
            quality=_choice(raw.get("quality"), "quality", "adv_quality"),
            sort=_choice(raw.get("sort"), "sort", "adv_order"),
            year_min=year_min, year_max=year_max,
            rating_min=rating_min, rating_max=rating_max,
        )

    def query_params(self) -> dict[str, str]:
        params = {"advanced_search": "on", "min_release": str(self.year_min),
                  "max_release": str(self.year_max), "min_rate": str(self.rating_min),
                  "max_rate": str(self.rating_max)}
        fields = {"s": self.query, "adv_post_type": self.type,
                  "avg_director": self.director, "adv_cast": self.actors,
                  "adv_country": self.country, "adv_age": self.age_rating,
                  "adv_quality": self.quality, "adv_order": self.sort}
        params.update({key: value for key, value in fields.items() if value is not None})
        return params


def build_search_request(filters: SearchFilters, base_url: str) -> dict[str, Any]:
    """Return a structured GET request; callers never assemble parameters."""
    params = filters.query_params()
    return {"method": "GET", "url": f"{base_url}?{urlencode(params)}", "params": params}
