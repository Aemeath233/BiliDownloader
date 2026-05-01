from __future__ import annotations

import socket
import threading
from collections.abc import Callable


class SingleInstance:
    HOST = "127.0.0.1"
    DEFAULT_PORT = 48379
    MESSAGE = b"BiliDownloader.SHOW\n"

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._activate_callback: Callable[[], None] | None = None
        self._pending_activate = False
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.HOST, self.port))
            sock.listen(4)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            return False

        self._socket = sock
        self.port = sock.getsockname()[1]
        self._running.set()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="single-instance-listener")
        self._thread.start()
        return True

    def notify_existing(self, timeout: float = 0.8) -> bool:
        try:
            with socket.create_connection((self.HOST, self.port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(self.MESSAGE)
                return sock.recv(16).startswith(b"OK")
        except OSError:
            return False

    def set_activate_callback(self, callback: Callable[[], None]) -> None:
        should_activate = False
        with self._lock:
            self._activate_callback = callback
            if self._pending_activate:
                self._pending_activate = False
                should_activate = True
        if should_activate:
            self._invoke_activate()

    def close(self) -> None:
        self._running.clear()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _serve(self) -> None:
        while self._running.is_set():
            try:
                assert self._socket is not None
                connection, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with connection:
                try:
                    message = connection.recv(64)
                    if message == self.MESSAGE:
                        self._invoke_activate()
                        connection.sendall(b"OK\n")
                    else:
                        connection.sendall(b"NO\n")
                except OSError:
                    pass

    def _invoke_activate(self) -> None:
        with self._lock:
            callback = self._activate_callback
            if callback is None:
                self._pending_activate = True
                return
        callback()
