"""Services: media decision logic. No Flask, no SocketIO, no global session.

Everything here is a function of its arguments. The HTTP client is passed in
rather than imported, so callers own proxy/TLS policy and tests need no socket.
"""