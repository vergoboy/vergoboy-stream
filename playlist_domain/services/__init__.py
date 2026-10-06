"""Playlist services: deciding what an item *is*, with no I/O.

No Flask, no SocketIO, no module globals, no filesystem. Everything is a
function of its arguments so the rules can be pinned without a database, a
socket or a running server.

What lives here is the part of ``add-url`` that decides *content*: whether a URL
needs transcoding at all, what to call the resulting item, and which fields the
playlist row starts with. What deliberately stays behind is everything with a
side effect -- writing the room, bumping the quota, deleting HLS directories,
broadcasting -- because those belong to the caller that owns the session.
"""