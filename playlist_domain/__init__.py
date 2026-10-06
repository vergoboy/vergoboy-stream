"""The playlist domain: adding, removing and ordering items in a room.

Named ``playlist_domain`` for the same reason as :mod:`media_domain` -- the
on-disk HLS output tree is ``<repo>/media/...``, so a package called
``playlist`` would sit inside runtime output and drag real user media into any
import scan.

Layered downward-only (see ``AGENTS.md`` rule 5'):

* ``services`` -- the decisions about an item. Pure; no Flask, no SocketIO.
  ``handlers`` is not created yet: rule 2' forbids empty packages, so it appears
  with the first endpoint that moves out of ``app.py``.

Distinct from :mod:`media_domain`, which owns *fetching and inspecting* a
source. Deciding that a URL needs no transcode because it is already an HLS
playlist is a playlist question; rewriting a YouTube URL to its media stream is a
media question, and lives there.
"""