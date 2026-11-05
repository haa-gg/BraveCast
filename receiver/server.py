#!/usr/bin/env python3
"""
BraveCast Receiver & DIAL Server for Glen's MacBook (2)
Multi-threaded Python 3 HTTP Server with Dedicated Brave Kiosk Window, DIAL, and Remote Control
"""

import os
import sys
import json
import time
import queue
import socket
import struct
import threading
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

PORT = int(os.environ.get("BRAVECAST_PORT", 8099))
HOST = "0.0.0.0"
DIR_PATH = os.path.dirname(os.path.abspath(__file__))
DEVICE_UUID = "542696d5-e565-4000-8000-000000000000"
FRIENDLY_NAME = "Living Room TV (Mac)"
SMART_TV_UA = "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 Native TV /0.0.1 TV Safari/538.1"
KIOSK_DIR = os.path.expanduser("~/Library/Application Support/BraveSoftware/Brave-Kiosk")

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "192.168.1.19"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()

# Global State
class ReceiverState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {
            "state": "idle",
            "stream": None,
            "currentTime": 0,
            "duration": 0,
            "volume": 1.0,
            "muted": False,
            "lastUpdate": time.time(),
            "youtubeRunning": False
        }
        self.sse_queues = []
        self.history = []

    def update_status(self, data):
        with self.lock:
            for k in ("state", "currentTime", "duration", "volume", "muted", "youtubeRunning"):
                if k in data:
                    self.status[k] = data[k]
            if "stream" in data:
                self.status["stream"] = data["stream"]
            self.status["lastUpdate"] = time.time()

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def add_history(self, stream):
        with self.lock:
            if not self.history or self.history[0].get("url") != stream.get("url"):
                self.history.insert(0, stream)
                if len(self.history) > 20:
                    self.history.pop()

    def get_history(self):
        with self.lock:
            return list(self.history)

    def register_sse(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.sse_queues.append(q)
        return q

    def unregister_sse(self, q):
        with self.lock:
            if q in self.sse_queues:
                self.sse_queues.remove(q)

    def broadcast(self, event_type, payload):
        msg = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        with self.lock:
            dead_queues = []
            for q in self.sse_queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead_queues.append(q)
            for dq in dead_queues:
                if dq in self.sse_queues:
                    self.sse_queues.remove(dq)

state_mgr = ReceiverState()


def wake_tv_and_screen():
    try:
        subprocess.run(["caffeinate", "-u", "-t", "4"], check=False)
    except Exception as e:
        print(f"Error waking display: {e}")


def launch_brave_kiosk(url, is_youtube=True):
    """Launch Brave in dedicated, guaranteed fullscreen kiosk mode"""
    wake_tv_and_screen()
    try:
        brave_bin = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        cmd = [
            brave_bin,
            f"--user-data-dir={KIOSK_DIR}",
            "--start-fullscreen",
            f"--app={url}"
        ]
        if is_youtube:
            cmd.append(f"--user-agent={SMART_TV_UA}")

        subprocess.Popen(cmd)
    except Exception as e:
        print(f"Error launching Brave Kiosk: {e}")


def start_caffeinate():
    try:
        proc = subprocess.Popen(["caffeinate", "-d", "-i"])
        return proc
    except Exception as e:
        print(f"Could not start caffeinate: {e}")
        return None


# --- DIAL SSDP SERVER ---
class DialSsdpServer(threading.Thread):
    def __init__(self, local_ip, port):
        super().__init__(daemon=True)
        self.local_ip = local_ip
        self.port = port
        self.mcast_group = "239.255.255.250"
        self.mcast_port = 1900

    def run(self):
        print(f"📡 DIAL SSDP Responder listening on {self.mcast_group}:{self.mcast_port}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass

            sock.bind(("", self.mcast_port))
            mreq = struct.pack("4sl", socket.inet_aton(self.mcast_group), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception as e:
            print(f"⚠️ Could not bind SSDP port 1900: {e}")
            return

        while True:
            try:
                data, addr = sock.recvfrom(2048)
                msg = data.decode("utf-8", errors="ignore")
                if "M-SEARCH" in msg and ("urn:dial-multiscreen-org:service:dial:1" in msg or "ssdp:all" in msg):
                    location = f"http://{self.local_ip}:{self.port}/dd.xml"
                    response = (
                        "HTTP/1.1 200 OK\r\n"
                        f"LOCATION: {location}\r\n"
                        "CACHE-CONTROL: max-age=1800\r\n"
                        "EXT:\r\n"
                        "BOOTID.UPNP.ORG: 1\r\n"
                        "SERVER: macOS/13.7 UPnP/1.1 BraveCast/2.0\r\n"
                        "ST: urn:dial-multiscreen-org:service:dial:1\r\n"
                        f"USN: uuid:{DEVICE_UUID}::urn:dial-multiscreen-org:service:dial:1\r\n"
                        f"WAKEUP: MAC=54:26:96:d5:e5:65;Timeout=10\r\n"
                        "\r\n"
                    )
                    sock.sendto(response.encode("utf-8"), addr)
            except Exception as e:
                time.sleep(1)


# --- HTTP HANDLER ---
class BraveCastHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def read_body(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return ""
        return self.rfile.read(content_len).decode("utf-8", errors="ignore")

    def read_json_body(self):
        body = self.read_body()
        try:
            return json.loads(body)
        except Exception:
            return {}

    def send_json(self, data, status_code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_xml(self, xml_str, status_code=200, extra_headers=None):
        body = xml_str.encode("utf-8")
        self.send_response(status_code)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, content_type):
        if not os.path.exists(filepath):
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(b"File not found")
            return

        with open(filepath, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/dd.xml":
            app_url = f"http://{LOCAL_IP}:{PORT}/apps/"
            xml = f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:tvdevice:1</deviceType>
    <friendlyName>{FRIENDLY_NAME}</friendlyName>
    <manufacturer>Google</manufacturer>
    <modelName>BraveCast</modelName>
    <UDN>uuid:{DEVICE_UUID}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:dial-multiscreen-org:service:dial:1</serviceType>
        <serviceId>urn:dial-multiscreen-org:serviceId:dial</serviceId>
        <controlURL>/apps</controlURL>
        <eventSubURL></eventSubURL>
        <SCPDURL></SCPDURL>
      </service>
    </serviceList>
  </device>
</root>"""
            self.send_xml(xml, extra_headers={"Application-URL": app_url})
            return

        if path in ("/apps/YouTube", "/apps/YouTube/"):
            st = state_mgr.get_status()
            yt_running = st.get("youtubeRunning", False)
            state_val = "running" if yt_running else "stopped"
            link_tag = '<link rel="run" href="run"/>' if yt_running else ""

            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="2.1">
  <name>YouTube</name>
  <options allowStop="true"/>
  <state>{state_val}</state>
  {link_tag}
</service>"""
            self.send_xml(xml)
            return

        if path == "/api/ping":
            self.send_json({
                "status": "ok",
                "device": "Glens-MBP-2",
                "name": FRIENDLY_NAME,
                "version": "2.0",
                "localIp": LOCAL_IP,
                "dial": "active",
                "spotify": "active",
                "receiverUrl": f"http://localhost:{PORT}/receiver"
            })
            return

        if path == "/api/status":
            st = state_mgr.get_status()
            st["history"] = state_mgr.get_history()
            self.send_json(st)
            return

        if path == "/api/events":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = state_mgr.register_sse()
            init_msg = f"event: INIT\ndata: {json.dumps(state_mgr.get_status())}\n\n"
            try:
                self.wfile.write(init_msg.encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                state_mgr.unregister_sse(q)
            return

        if path in ("/", "/remote"):
            self.serve_file(os.path.join(DIR_PATH, "remote.html"), "text/html; charset=utf-8")
            return

        if path == "/receiver":
            self.serve_file(os.path.join(DIR_PATH, "receiver.html"), "text/html; charset=utf-8")
            return

        if path == "/hls.min.js":
            self.serve_file(os.path.join(DIR_PATH, "hls.min.js"), "application/javascript")
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/apps/YouTube", "/apps/YouTube/"):
            raw_body = self.read_body()
            yt_url = "https://www.youtube.com/tv"
            params = parse_qs(raw_body)
            if not params:
                params = parse_qs(parsed.query)

            query_parts = []
            if "pairingCode" in params:
                query_parts.append(f"pairingCode={params['pairingCode'][0]}")
            if "v" in params:
                query_parts.append(f"v={params['v'][0]}")
            if "t" in params:
                query_parts.append(f"t={params['t'][0]}")

            if query_parts:
                yt_url = f"https://www.youtube.com/tv?{'&'.join(query_parts)}"

            launch_brave_kiosk(yt_url, is_youtube=True)

            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": True,
                "stream": {"url": yt_url, "title": "YouTube on TV (Ad-Free)", "source": "dial"}
            })

            self.send_response(201)
            self.send_cors_headers()
            self.send_header("Location", f"http://{LOCAL_IP}:{PORT}/apps/YouTube/run")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/youtube":
            url = "https://www.youtube.com/tv"
            launch_brave_kiosk(url, is_youtube=True)
            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": True,
                "stream": {"url": url, "title": "YouTube on TV (Ad-Free)", "source": "youtube"}
            })
            self.send_json({"success": True, "message": "YouTube TV opened in Brave Kiosk"})
            return

        if path == "/api/cast":
            data = self.read_json_body()
            url = data.get("url")
            if not url:
                self.send_json({"error": "No url provided"}, 400)
                return

            title = data.get("title") or url.split("/")[-1].split("?")[0] or "Video Stream"
            stream_info = {
                "url": url,
                "title": title,
                "pageUrl": data.get("pageUrl", ""),
                "timestamp": int(time.time())
            }

            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": False,
                "stream": stream_info,
                "currentTime": 0
            })
            state_mgr.add_history(stream_info)

            # Wake TV and open receiver in fullscreen kiosk
            launch_brave_kiosk(f"http://localhost:{PORT}/receiver", is_youtube=False)
            state_mgr.broadcast("LOAD", stream_info)

            self.send_json({
                "success": True,
                "message": f"Casting '{title}' to Glen's MacBook (2)",
                "stream": stream_info
            })
            return

        if path == "/api/control":
            data = self.read_json_body()
            action = data.get("action")
            value = data.get("value")

            if not action:
                self.send_json({"error": "No action provided"}, 400)
                return

            if action in ("play", "toggle"):
                subprocess.run(["caffeinate", "-u", "-t", "2"], check=False)

            state_mgr.broadcast("CONTROL", {"action": action, "value": value})
            self.send_json({"success": True, "action": action, "value": value})
            return

        if path == "/api/receiver/status":
            data = self.read_json_body()
            state_mgr.update_status(data)
            self.send_json({"success": True})
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Endpoint not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/apps/YouTube/run", "/apps/YouTube/run/"):
            state_mgr.update_status({"youtubeRunning": False, "state": "idle"})
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {args[0]} {args[1]}\n")


def main():
    print(f"🚀 Starting BraveCast Receiver & DIAL Server on port {PORT}...")
    print(f"🏠 Local IP: {LOCAL_IP}")
    caffeinate_proc = start_caffeinate()

    ssdp_server = DialSsdpServer(LOCAL_IP, PORT)
    ssdp_server.start()

    server = ThreadingHTTPServer((HOST, PORT), BraveCastHandler)
    server.daemon_threads = True

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()
        if caffeinate_proc:
            caffeinate_proc.terminate()


if __name__ == "__main__":
    main()
