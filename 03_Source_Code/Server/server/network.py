"""Asyncio TCP transport for SynCanvas.

Concurrency model
-----------------
* One asyncio event loop accepts connections.
* Each client gets a reader task, a small outbound writer queue and a heartbeat
  task.  No GUI thread ever performs socket I/O.
* Messages are newline-delimited UTF-8 JSON with a maximum frame size.
* The core protocol dispatcher is synchronous and runs on the event-loop
  thread, so session state has a single-writer guarantee without coarse locks.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from typing import Any, Iterable

from shared.constants import (
    AUTH_TIMEOUT_SECONDS,
    CLIENT_MESSAGE_TYPES,
    ERROR,
    ERR_PROTOCOL,
    ERR_RATE_LIMIT,
    ERR_TOO_LARGE,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    MAX_MESSAGE_BYTES,
    MAX_SEND_QUEUE,
    PING,
)
from shared.protocol import (
    LineFramer,
    PayloadTooLarge,
    ProtocolError,
    decode_message,
    encode_message,
    make_server_message,
    utc_now_iso,
)
from server.config import ServerConfig
from server.core import ServerCore
from server.database import Database
from server.monitoring.metrics import ServerMetrics
from server.security import RateLimiter

logger = logging.getLogger("syncanvas.server.network")


def lan_ip_addresses() -> list[str]:
    """Best-effort list of local IPv4 addresses without any internet request."""
    addresses: set[str] = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        # Does not send packets; used only to discover the preferred interface.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))
            addresses.add(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    return sorted(ip for ip in addresses if not ip.startswith("169.254."))


class ClientConnection:
    """A connected TCP client with an outbound queue and heartbeat state."""

    def __init__(
        self,
        server: "SynCanvasServer",
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        self.id = f"c-{id(self):x}"
        peer = writer.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self.participant = None
        self.session = None
        self.connected_at = time.monotonic()
        self.last_rx = time.monotonic()
        self.last_ping = 0.0
        self.closed = False
        self.cleanup_done = False
        self.seen_message_ids: set[str] = set()
        self.rate_limiter = RateLimiter(rate=600.0, capacity=1000.0)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_SEND_QUEUE)
        self._writer_task = asyncio.create_task(self._writer_loop())
        self._heartbeat_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    def send(self, message: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            raw = encode_message(message)
        except PayloadTooLarge:
            logger.error("Dropping oversized outbound message to %s", self.peer)
            self.close("outbound frame too large")
            return False
        except ProtocolError:
            logger.exception("Could not encode outbound message")
            return False
        try:
            self._queue.put_nowait(raw)
            return True
        except asyncio.QueueFull:
            logger.warning("Outbound queue full for %s; closing slow client", self.peer)
            self.close("client is too slow")
            return False

    async def _writer_loop(self) -> None:
        try:
            while not self.closed:
                data = await self._queue.get()
                if self.writer.is_closing():
                    break
                self.writer.write(data)
                await self.writer.drain()
                self.server.metrics.incr("messages_sent")
                self.server.metrics.add_bytes(sent=len(data))
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            logger.debug("Writer loop ended for %s", self.peer, exc_info=True)
        finally:
            if not self.writer.is_closing():
                with contextlib.suppress(Exception):
                    self.writer.close()

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        try:
            while not self.closed:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                now = time.monotonic()
                if self.session is None:
                    if now - self.connected_at > AUTH_TIMEOUT_SECONDS:
                        self.close("authentication timeout")
                        return
                if now - self.last_rx > HEARTBEAT_TIMEOUT:
                    logger.info("Heartbeat timeout for %s", self.peer)
                    self.close("heartbeat timeout")
                    return
                if now - self.last_ping >= HEARTBEAT_INTERVAL:
                    self.last_ping = now
                    self.send(
                        make_server_message(
                            PING,
                            {"t": time.time(), "server_time": utc_now_iso()},
                        )
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Heartbeat loop error", exc_info=True)

    def close(self, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        if reason:
            logger.info("Closing %s: %s", self.peer, reason)
        with contextlib.suppress(Exception):
            self.writer.close()
        # Abort the transport so a peer that never calls close cannot keep
        # ``wait_closed`` (and therefore server shutdown) blocked.
        transport = getattr(self.writer, "transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.abort()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self._writer_task.cancel()

    async def wait_closed(self) -> None:
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()

    def __repr__(self) -> str:
        who = self.participant.name if self.participant else "unauthenticated"
        return f"<ClientConnection {self.peer} {who}>"


class SynCanvasServer:
    def __init__(self, config: ServerConfig | None = None, *, database: Database | None = None) -> None:
        self.config = config or ServerConfig()
        self.metrics = ServerMetrics()
        self.db = database or Database(self.config.db_path)
        self.core = ServerCore(self.config, self.db, self.metrics)
        self._server: asyncio.AbstractServer | None = None
        self._tick_task: asyncio.Task | None = None
        self._connections: set[ClientConnection] = set()
        self._stopping = False
        self.bound_port: int = self.config.port
        self.loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    async def start(self) -> int:
        self.loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.host,
            port=self.config.port,
            limit=MAX_MESSAGE_BYTES + 1024,
            reuse_address=True,
        )
        sockets = self._server.sockets or []
        if sockets:
            self.bound_port = int(sockets[0].getsockname()[1])
        self.config.port = self.bound_port
        self.core.config.port = self.bound_port
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info(
            "SynCanvas server listening on %s:%s (TCP)",
            self.config.host,
            self.bound_port,
        )
        self.metrics.event(
            "server", f"Listening on {self.config.host}:{self.bound_port}"
        )
        return self.bound_port

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def _tick_loop(self) -> None:
        counter = 0
        try:
            while not self._stopping:
                await asyncio.sleep(1.0)
                self.core.tick()
                counter += 1
                if counter % 30 == 0:
                    snap = self.metrics.snapshot(
                        {
                            "active_sessions": len(self.core.sessions.sessions),
                            "online_clients": self.core.sessions.total_online(),
                        }
                    )
                    logger.info(
                        "Status: %d session(s), %d online, %d msgs in / %d out, %.1f KB in / %.1f KB out",
                        snap["active_sessions"],
                        snap["online_clients"],
                        snap["messages_received"],
                        snap["messages_sent"],
                        snap["bytes_received"] / 1024,
                        snap["bytes_sent"] / 1024,
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Server tick loop failed")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = ClientConnection(self, reader, writer)
        self._connections.add(conn)
        self.core.on_connect(conn)
        conn.start_heartbeat()
        logger.info("Accepted TCP connection from %s", conn.peer)
        try:
            await self._read_loop(conn)
        finally:
            if not conn.cleanup_done:
                conn.cleanup_done = True
                try:
                    self.core.on_disconnect(conn)
                except Exception:
                    logger.exception("Disconnect cleanup failed")
            conn.close("connection ended")
            await conn.wait_closed()
            self._connections.discard(conn)
            logger.info("Connection closed: %s", conn.peer)

    async def _read_loop(self, conn: ClientConnection) -> None:
        framer = LineFramer()
        invalid_frames = 0
        while not conn.closed:
            try:
                chunk = await asyncio.wait_for(conn.reader.read(65_536), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except (ConnectionError, asyncio.CancelledError):
                break
            if not chunk:
                break
            self.metrics.add_bytes(received=len(chunk))
            try:
                messages = framer.feed(chunk)
            except PayloadTooLarge:
                self._send_protocol_error(conn, ERR_TOO_LARGE, "Frame exceeds the maximum size")
                break
            except ProtocolError as exc:
                invalid_frames += 1
                self._send_protocol_error(conn, ERR_PROTOCOL, str(exc))
                if invalid_frames >= 5:
                    break
                continue
            for message in messages:
                if conn.closed:
                    break
                await self._process_message(conn, message)

    async def _process_message(self, conn: ClientConnection, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type not in CLIENT_MESSAGE_TYPES:
            self._send_protocol_error(conn, "UNKNOWN_TYPE", f"Unsupported message type: {msg_type}")
            return
        if not conn.rate_limiter.allow():
            self.metrics.incr("rejected_messages")
            if not getattr(conn, "_rate_warned_at", 0) or time.monotonic() - conn._rate_warned_at > 2:
                conn._rate_warned_at = time.monotonic()
                conn.send(
                    make_server_message(
                        ERROR,
                        {
                            "code": ERR_RATE_LIMIT,
                            "message": "Too many messages; slow down",
                            "details": {},
                        },
                        reply_to=message.get("msg_id"),
                    )
                )
            return
        conn.last_rx = time.monotonic()
        self.metrics.incr("messages_received")
        try:
            self.core.dispatch(conn, message)
        except Exception:
            logger.exception("Dispatch crashed for %s", msg_type)
            conn.send(
                make_server_message(
                    ERROR,
                    {
                        "code": "INTERNAL",
                        "message": "Internal server error",
                        "details": {},
                    },
                    reply_to=message.get("msg_id"),
                )
            )

    def _send_protocol_error(self, conn: ClientConnection, code: str, message: str) -> None:
        self.metrics.incr("rejected_messages")
        conn.send(
            make_server_message(
                ERROR,
                {"code": code, "message": message, "details": {}},
            )
        )

    # ------------------------------------------------------------------
    async def stop(self) -> None:
        self._stopping = True
        if self._tick_task:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        if self._server:
            self._server.close()
        for conn in list(self._connections):
            conn.close("server shutdown")
        # Persist the authoritative board immediately, before waiting for
        # potentially uncooperative TCP peers. This guarantees crash-safe
        # recovery even if a client disappears mid-shutdown.
        with contextlib.suppress(Exception):
            self.core.sessions.save_all(force=True)
        if self._server:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        await asyncio.sleep(0.05)
        for conn in list(self._connections):
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=0.8)
            except (asyncio.TimeoutError, Exception):
                pass
            if not conn.cleanup_done:
                conn.cleanup_done = True
                with contextlib.suppress(Exception):
                    self.core.on_disconnect(conn)
        for _ in range(10):
            if not self._connections:
                break
            await asyncio.sleep(0.03)
        self.core.shutdown()
        self.db.close()
        logger.info("Server stopped")

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def stats(self) -> dict[str, Any]:
        return self.metrics.snapshot(
            {
                "running": self.running,
                "port": self.bound_port,
                "host": self.config.display_host,
                "lan_ips": lan_ip_addresses(),
                "active_sessions": len(self.core.sessions.sessions),
                "online_clients": self.core.sessions.total_online(),
                "total_objects": self.core.sessions.total_objects(),
                "database": self.db.stats(),
                "sessions": self.core.sessions.active_sessions(),
            }
        )


class ServerThread:
    """Run a SynCanvas server on a dedicated thread (used by the server GUI)."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: SynCanvasServer | None = None
        self._thread = None
        self._ready = None
        self._stopped = None
        self._stop_event: asyncio.Event | None = None
        self.error: Exception | None = None

    @property
    def running(self) -> bool:
        return bool(self.server and self.server.running)

    def start(self, timeout: float = 8.0) -> int:
        import threading

        self._ready = threading.Event()
        self._stopped = threading.Event()

        def runner() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server = SynCanvasServer(self.config)
            try:
                self.loop.run_until_complete(_run())
            except Exception as exc:
                self.error = exc
                if self._ready is not None:
                    self._ready.set()
                logger.exception("Server thread stopped unexpectedly")
            finally:
                try:
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        self.loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                except Exception:
                    pass
                try:
                    self.loop.close()
                except Exception:
                    pass
                self._stopped.set()

        async def _run() -> None:
            assert self.server is not None
            self._stop_event = asyncio.Event()
            await self.server.start()
            self._ready.set()
            await self._stop_event.wait()
            await self.server.stop()

        self._thread = threading.Thread(target=runner, name="syncanvas-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("Server did not start in time")
        if self.error:
            raise self.error
        if self.server is None or not self.server.running:
            raise RuntimeError("Server failed to bind")
        return int(self.server.bound_port)

    def stop(self, timeout: float = 10.0) -> None:
        if not self.loop or not self._thread:
            return
        if self._stop_event and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._stop_event.set)
        if self._stopped:
            self._stopped.wait(timeout)

    def stats(self) -> dict[str, Any]:
        if self.server and self.loop and self.loop.is_running():
            async def _snapshot() -> dict[str, Any]:
                assert self.server is not None
                return self.server.stats()

            try:
                future = asyncio.run_coroutine_threadsafe(_snapshot(), self.loop)
                return future.result(3.0)
            except Exception:
                pass
        if self.server:
            return self.server.stats()
        return {"running": False, "error": str(self.error) if self.error else None}
