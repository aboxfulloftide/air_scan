import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

MAX_CONNECTIONS_PER_IP = 10
REQUEST_TIMEOUT = 10  # seconds — hard cutoff for any request


class PerIPConnectionLimitMiddleware:
    """Pure ASGI middleware that caps concurrent connections per client IP
    and enforces a hard request timeout.

    When a new request would exceed max_per_ip, the oldest tracked
    connections are cancelled to make room.  Any request that exceeds
    request_timeout seconds is killed regardless."""

    def __init__(self, app, max_per_ip: int = MAX_CONNECTIONS_PER_IP,
                 request_timeout: float = REQUEST_TIMEOUT):
        self.app = app
        self.max_per_ip = max_per_ip
        self.request_timeout = request_timeout
        # ip -> {conn_id: (start_time, cancel_event)}
        self._active: dict[str, dict[int, tuple[float, asyncio.Event]]] = defaultdict(dict)
        self._next_id = 0

    def _evict_oldest(self, ip: str):
        """Cancel oldest connections until we're under the limit."""
        conns = self._active.get(ip)
        if not conns or len(conns) < self.max_per_ip:
            return
        by_age = sorted(conns.items(), key=lambda item: item[1][0])
        to_evict = len(conns) - self.max_per_ip + 1  # make room for the new one
        for cid, (_started, cancel_event) in by_age[:to_evict]:
            cancel_event.set()
            del conns[cid]
        logger.info("Evicted %d oldest connections for %s (%d remaining)",
                    to_evict, ip, len(conns))
        if not conns:
            del self._active[ip]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Long-running maintenance endpoints get extended timeout
        path = scope.get("path", "")
        timeout = self.request_timeout
        if path.startswith("/api/maintenance/cleanup"):
            timeout = 600  # 10 minutes for batch cleanup

        client = scope.get("client")
        ip = client[0] if client else "unknown"

        self._evict_oldest(ip)

        cancel_event = asyncio.Event()
        conn_id = self._next_id
        self._next_id += 1
        self._active[ip][conn_id] = (time.monotonic(), cancel_event)

        async def guarded_send(message):
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, guarded_send),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Request %d from %s timed out after %ds",
                           conn_id, ip, self.request_timeout)
            # Send 504 if headers haven't been sent yet
            try:
                await send({"type": "http.response.start", "status": 504,
                            "headers": [[b"content-type", b"text/plain"]]})
                await send({"type": "http.response.body",
                            "body": b"Request timed out"})
            except Exception:
                pass
        except asyncio.CancelledError:
            logger.debug("Connection %d for %s was evicted", conn_id, ip)
        finally:
            self._active[ip].pop(conn_id, None)
            if not self._active[ip]:
                del self._active[ip]
