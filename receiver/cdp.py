"""
Minimal Chrome DevTools Protocol client (stdlib only: no `websockets` dependency).

Talks to the kiosk Brave started with --remote-debugging-port so BraveCast can watch
the YouTube page and steer it (e.g. autoplay the next video).
"""

import base64
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse


class CDPError(Exception):
    pass


def list_targets(port, timeout=2.0):
    """HTTP endpoint listing open pages/tabs of the debuggable browser."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        return json.loads(r.read())


class CDPClient:
    def __init__(self, ws_url, timeout=10.0):
        u = urlparse(ws_url)
        self._sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        self._id = 0
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self._sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake closed")
            head += chunk
        status_line = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise CDPError("websocket handshake refused: " + repr(status_line))
        self._buf = head.split(b"\r\n\r\n", 1)[1]

    # --- websocket framing ---
    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CDPError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)  # client frames must be masked
        self._sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_message(self):
        message = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if opcode == 0x8:
                raise CDPError("websocket closed by browser")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return message

    # --- CDP ---
    def call(self, method, params=None, timeout=10.0):
        self._id += 1
        call_id = self._id
        self._sock.settimeout(timeout)
        self._send_frame(0x1, json.dumps({"id": call_id, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == call_id:  # anything else is an unrelated event
                if "error" in msg:
                    raise CDPError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result", {})

    def evaluate(self, expression, timeout=10.0):
        res = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout)
        if "exceptionDetails" in res:
            raise CDPError(res["exceptionDetails"].get("text", "evaluate failed"))
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
