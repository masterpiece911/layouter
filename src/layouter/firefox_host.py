"""Native messaging transport. No browser policy, mutation, or persistent desktop state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import socket
import stat
import struct
import sys
import threading

from .errors import BackendError
from .runtime import Runtime

FIREFOX_PROTOCOL_VERSION = 2
MAX_MESSAGE = 1024 * 1024
HOST_NAME = "org.layouter.firefox"
EXTENSION_ID = "firefox@layouter.dev"


def firefox_runtime() -> Runtime:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        raise BackendError("Firefox native messaging requires XDG_RUNTIME_DIR")
    root = Runtime(Path(base) / "layouter")
    root.ensure()
    runtime = Runtime(root.path / "firefox")
    runtime.ensure()
    return runtime


def read_native(stream):
    """Read Firefox's native-endian length framing without accepting unbounded input."""
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = stream.read(size - len(data))
            if not part:
                raise EOFError
            data.extend(part)
        return bytes(data)
    size = struct.unpack("=I", exact(4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Invalid native message length")
    value = json.loads(exact(size))
    if not isinstance(value, dict):
        raise ValueError("Native message must be an object")
    return value


def write_native(stream, value):
    data = json.dumps(value, ensure_ascii=False).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("Native message too large")
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def serve(incoming, outgoing, runtime: Runtime):
    """Register one companion, multiplex requests, and terminate at browser EOF."""
    greeting = read_native(incoming)
    instance = greeting.get("instanceId", "")
    if greeting.get("op") != "register" or not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9]{32}", instance):
        raise ValueError("Invalid companion registration")
    path = runtime.socket(instance)
    with runtime.lock(instance):
        if path.exists():
            st = path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                raise BackendError(f"Unsafe Firefox socket: {path}")
            path.unlink()  # Exclusive instance lock proves this socket has no live host.
        listener = socket.socket(socket.AF_UNIX)
        stopped = threading.Event()
        pending = {}
        guard, writer = threading.Lock(), threading.Lock()
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(8)
        listener.settimeout(0.2)

        def client(conn):
            request_id = None
            registered = False
            try:
                with conn, conn.makefile("rb") as stream:
                    conn.settimeout(60)
                    line = stream.readline(MAX_MESSAGE + 1)
                    if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                        return
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        return
                    request_id = request.get("id")
                    if not isinstance(request_id, str) or not request_id:
                        return
                    response = queue.Queue(maxsize=1)
                    with guard:
                        if request_id in pending:
                            return
                        pending[request_id] = response
                        registered = True
                    if type(greeting.get("version")) is not int or greeting["version"] != FIREFOX_PROTOCOL_VERSION:
                        result = {"id": request_id, "version": greeting.get("version"), "ok": False,
                                  "code": "ProtocolMismatch", "message": "Companion protocol mismatch"}
                    else:
                        with writer:
                            write_native(outgoing, request)
                        result = response.get(timeout=60)
                    if result is not None:
                        conn.sendall(json.dumps(result).encode() + b"\n")
            except (OSError, ValueError, queue.Empty):
                pass
            finally:
                if registered:
                    with guard:
                        pending.pop(request_id, None)

        def accept():
            while not stopped.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=client, args=(conn,), daemon=True).start()

        threading.Thread(target=accept, daemon=True).start()
        try:
            while True:
                response = read_native(incoming)
                response_id = response.get("id")
                if not isinstance(response_id, str):
                    raise ValueError("Invalid native response ID")
                with guard:
                    target = pending.get(response_id)
                    if target and target.empty():
                        target.put_nowait(response)
        except EOFError:
            pass
        finally:
            stopped.set()
            listener.close()
            path.unlink(missing_ok=True)
            with guard:
                for target in pending.values():
                    if target.empty():
                        target.put_nowait(None)


def main():
    try:
        serve(sys.stdin.buffer, sys.stdout.buffer, firefox_runtime())
        return 0
    except (BackendError, OSError, ValueError, EOFError) as exc:
        print(f"Layouter Firefox host: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
