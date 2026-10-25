"""The raw uploaded files (full-quality originals) must not be served by Flask at all.

They used to be reachable at /media/uploads/<file> with no authentication. Native clients now get
them through a signed, short-lived nginx URL (see native_media.py); nothing else needs this route.
"""
from __future__ import annotations

import pytest

app_module = pytest.importorskip("app", reason="app requires a configured environment")


def test_no_flask_route_serves_the_uploads_directory():
    rules = [r.rule for r in app_module.app.url_map.iter_rules()]
    assert not any("/media/uploads" in r for r in rules), rules
    assert not hasattr(app_module, "serve_upload")


def test_requesting_a_raw_upload_is_a_404(tmp_path, monkeypatch):
    (tmp_path / "it1_src.mkv").write_bytes(b"secret movie bytes")
    monkeypatch.setattr(app_module.Config, "UPLOAD_DIR", str(tmp_path))
    client = app_module.app.test_client()
    for url in (f"{app_module.BP}/media/uploads/it1_src.mkv", f"{app_module.BP}/media/uploads/../uploads/it1_src.mkv"):
        r = client.get(url)
        assert r.status_code == 404
        assert b"secret movie bytes" not in r.data


def test_other_public_media_routes_are_untouched():
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert any(r.endswith("/media/hls/<item_id>/<path:filename>") for r in rules)
    assert any(r.endswith("/media/subs/<path:filename>") for r in rules)
