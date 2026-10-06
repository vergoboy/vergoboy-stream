"""The media domain: URL intake and media metadata.

Named `media_domain`, not `media`, because Config.MEDIA_DIR *is* `<repo>/media`
at runtime — that directory holds uploads, subtitles, HLS renditions, chat
images and avatars. A Python package called `media/` would sit inside the
user's media directory, and any import-boundary scan of it would walk real
user uploads.

Layered so that dependencies only ever point downward (see BACKEND_RULES.md):

* :mod:`media_domain.handlers` — thin Flask boundary. Not created yet; rule 2'
  forbids empty packages, so it appears with the first endpoint that needs it.
* :mod:`media_domain.services` — decision logic. No Flask, no SocketIO.
* :mod:`media_domain.adapters` — the outside world: network, filesystem.

Network access is always injected as an explicit ``client`` argument rather
than reached for through a module global. The callers in ``app.py`` own the
session (``MEDIA_CLIENT``); this package never constructs one, which is what
keeps the services testable without a socket and keeps proxy configuration the
caller's decision.

Deliberately distinct from :mod:`media_pipeline`, which owns the
probe -> plan -> argv -> run pipeline and is pure by contract. The ffprobe
wrapper and the encode orchestration stay in ``app.py`` until their own slice.
"""