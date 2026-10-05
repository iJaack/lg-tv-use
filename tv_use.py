"""Bounded SSAP client. No service-menu, arbitrary URI or purchase tools."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
from pathlib import Path
import socket
import ssl
import tempfile
import time
from urllib.parse import urlsplit, quote
import xml.etree.ElementTree as ET

import websocket

PERMISSIONS = ["LAUNCH", "READ_INSTALLED_APPS", "READ_RUNNING_APPS", "CONTROL_INPUT_TEXT",
               "CONTROL_MOUSE_AND_KEYBOARD", "CONTROL_DISPLAY", "CONTROL_AUDIO",
               "CONTROL_POWER", "READ_NETWORK_STATE"]
BUTTONS = {"HOME", "BACK", "UP", "DOWN", "LEFT", "RIGHT", "ENTER"}
MAX_IMAGE = 8 * 1024 * 1024


class TVError(RuntimeError):
    pass


class ConnectionLost(TVError):
    """A command may have been delivered. Never automatically replay it."""
    pass


def validate_host(host: str) -> str:
    address = ipaddress.ip_address(host)
    if address.version != 4 or not any(address in ipaddress.ip_network(net) for net in
                                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise TVError("Only an RFC1918 LAN IPv4 TV address is allowed")
    return str(address)


def discover(timeout: float = 3) -> list[dict]:
    """Discover LG's second-screen advertisements; never sweep the subnet."""
    message = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
               'MAN: "ssdp:discover"\r\nMX: 2\r\n'
               'ST: urn:lge-com:service:webos-second-screen:1\r\n\r\n')
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(.4)
        sock.sendto(message.encode(), ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (host, _) = sock.recvfrom(8192)
                headers = {key.lower(): value for line in data.decode(errors="replace").splitlines()
                           if ":" in line for key, value in [line.split(":", 1)]}
                location = headers.get("location", "").strip()
                if urlsplit(location).hostname != host:
                    continue
                tv = TV(host)
                raw, _ = tv.download(location, limit=128 * 1024, require_pair=False)
                root = ET.fromstring(raw)
                ns = {"u": "urn:schemas-upnp-org:device-1-0"}
                device = root.find("u:device", ns)
                if device is not None:
                    found[host] = {"host": host, "name": device.findtext("u:friendlyName", namespaces=ns),
                                   "model": device.findtext("u:modelNumber", namespaces=ns)}
            except socket.timeout:
                pass
            except (ValueError, OSError, TVError, ET.ParseError):
                continue
    return list(found.values())


class TV:
    def __init__(self, host: str | None = None, state_dir: Path | None = None, timeout: float = 8):
        host = host or os.environ.get("TV_HOST")
        if not host:
            raise TVError("Set TV_HOST or pass --host with your TV's LAN IPv4 address")
        self.host = validate_host(host)
        self.state_dir = Path(state_dir or os.environ.get("TV_STATE_DIR") or
                              Path.home() / "Library/Application Support/lg-tv-use")
        self.key_path = self.state_dir / (self.host + ".json")
        self.timeout = timeout
        self.ws = None
        self.pointer = None
        self.pointer_checked_at = 0
        self.counter = 0
        self.pin = None
        self.metrics = {"connections": 0, "requests": 0, "screenshots": 0}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        for ws in (self.pointer, self.ws):
            if ws is not None:
                ws.close()
        self.pointer = self.ws = None
        self.pointer_checked_at = 0

    def _open_socket(self, url: str):
        parts = urlsplit(url)
        if parts.scheme != "wss" or parts.hostname != self.host or parts.port != 3001:
            raise TVError("WebSocket must use this TV's pinned TLS endpoint")
        ws = websocket.create_connection(url, timeout=self.timeout,
                                         sslopt={"cert_reqs": ssl.CERT_NONE},
                                         suppress_origin=True,
                                         http_no_proxy=[self.host])
        try:
            fingerprint = hashlib.sha256(ws.sock.getpeercert(binary_form=True)).hexdigest()
            if self.pin and fingerprint != self.pin:
                raise TVError("TV certificate changed; refusing to send credentials. Re-pair explicitly after verifying TV identity.")
            self.pin = fingerprint
            return ws
        except BaseException:
            ws.close()
            raise

    def _save_key(self, key):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        fd, temporary = tempfile.mkstemp(dir=self.state_dir)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump({"host": self.host, "client_key": key, "certificate_sha256": self.pin}, file)
            os.replace(temporary, self.key_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def connect(self, pair: bool = False, pairing_timeout: float = 45, expected_model: str | None = None):
        self.close()
        credential = json.loads(self.key_path.read_text()) if self.key_path.exists() else {}
        if credential.get("host", self.host) != self.host:
            raise TVError("Credential is bound to a different TV")
        if not credential.get("client_key") and not pair:
            raise TVError("Not paired. Run: lg-tv-use pair; accept LG TV Use on your TV.")
        if credential.get("client_key") and not credential.get("certificate_sha256"):
            raise TVError("Saved credential has no certificate pin; refusing to use it")
        self.pin = credential.get("certificate_sha256")
        self.ws = self._open_socket(f"wss://{self.host}:3001")
        payload = {"pairingType": "PROMPT", "forcePairing": bool(pair and credential.get("client_key")),
                   "manifest": {"manifestVersion": 1, "appId": "io.github.ijaack.lg-tv-use",
                                "localizedAppNames": {"": "LG TV Use"}, "permissions": PERMISSIONS}}
        if credential.get("client_key"):
            payload["client-key"] = credential["client_key"]
        deadline = time.monotonic() + (pairing_timeout if pair else self.timeout)
        try:
            self.ws.send(json.dumps({"type": "register", "id": "register", "payload": payload}))
            while time.monotonic() < deadline:
                self.ws.settimeout(max(.1, deadline - time.monotonic()))
                raw = self.ws.recv()
                if not raw:
                    raise TVError("TV closed the connection before registration")
                message = json.loads(raw)
                if message.get("type") == "error":
                    raise TVError(message.get("error", "Pairing rejected"))
                if message.get("type") == "registered":
                    key = message.get("payload", {}).get("client-key") or credential.get("client_key")
                    if not key:
                        raise TVError("Registered response omitted the client key")
                    if expected_model:
                        self.ws.settimeout(self.timeout)
                        model=self.request('ssap://system/getSystemInfo').get('modelName')
                        if model != expected_model:
                            raise TVError('Pairing reached a different model; credentials were not saved')
                    if pair or key != credential.get("client_key"):
                        self._save_key(key)
                    self.metrics["connections"] += 1
                    self.ws.settimeout(self.timeout)
                    return {"paired": True, "host": self.host, "certificate_pinned": True}
                if message.get("payload", {}).get("pairingType") and not pair:
                    raise TVError("TV requires pairing again. Run pair explicitly.")
            raise TVError("Pairing timed out; no successful connection recorded")
        except BaseException:
            self.close()
            raise

    def request(self, uri: str, payload: dict | None = None) -> dict:
        if not self.ws:
            raise TVError("Not connected")
        self.counter += 1
        request_id = str(self.counter)
        deadline = time.monotonic() + self.timeout
        try:
            self.metrics["requests"] += 1
            self.ws.send(json.dumps({"type": "request", "id": request_id, "uri": uri, "payload": payload or {}}))
            while time.monotonic() < deadline:
                self.ws.settimeout(max(.1, deadline - time.monotonic()))
                raw = self.ws.recv()
                if not raw:
                    raise ConnectionLost("TV closed the connection; command outcome unknown")
                message = json.loads(raw)
                if message.get("id") != request_id:
                    continue
                if message.get("type") == "error":
                    raise TVError(message.get("error", "TV rejected request"))
                result = message.get("payload", {})
                if result.get("returnValue") is False:
                    raise TVError(result.get("errorText", "TV rejected request"))
                return result
            raise TVError("TV response deadline exceeded; command outcome unknown")
        except (ConnectionLost, websocket.WebSocketException, OSError) as error:
            self.close()
            raise ConnectionLost(f"Connection lost; do not replay the command automatically: {error}") from error
        finally:
            if self.ws is not None:
                self.ws.settimeout(self.timeout)

    def download(self, url: str, limit: int = MAX_IMAGE, require_pair: bool = True):
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname != self.host or parts.username or parts.password:
            raise TVError("TV download URL must point to the same LAN TV")
        if require_pair and not self.pin:
            raise TVError("Pair before downloading captures")
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection(self.host, parts.port or 443, timeout=self.timeout,
                context=ssl._create_unverified_context())
            connection.connect()
            fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not self.pin or fingerprint != self.pin:
                connection.close()
                raise TVError("Capture TLS certificate differs from paired TV")
        else:
            connection = http.client.HTTPConnection(self.host, parts.port or 80, timeout=self.timeout)
        try:
            connection.request("GET", (parts.path or "/") + ("?" + parts.query if parts.query else ""))
            response = connection.getresponse()
            if response.status != 200:
                raise TVError(f"TV download returned HTTP {response.status}; redirects are refused")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise TVError("TV response exceeds size limit")
            return data, response.getheader("Content-Type", "")
        finally:
            connection.close()

    def screenshot(self):
        self.metrics["screenshots"] += 1
        result = self.request("ssap://tv/executeOneShot")
        url = result.get("imageUri")
        if not url:
            raise TVError("TV did not provide a screenshot URL")
        data, _ = self.download(url)
        if data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            raise TVError("Capture was not a supported image")
        return data, mime

    def observe(self, capture=True):
        result = {"host": self.host, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "state": {}, "limitations": []}
        for name, uri in [("foreground_app", "com.webos.applicationManager/getForegroundAppInfo"),
                          ("audio", "audio/getVolume")]:
            try:
                result["state"][name] = self.request("ssap://" + uri)
            except (TVError, websocket.WebSocketException, OSError) as error:
                result["limitations"].append(f"{name}: {error}")
        image = None
        if capture:
            try:
                image = self.screenshot()
                result["screenshot"] = "captured; protected video may be blank"
            except (TVError, websocket.WebSocketException, OSError) as error:
                result["screenshot"] = "unavailable"
                result["limitations"].append(f"screenshot: {error}")
        return result, image

    def apps(self):
        response = {"apps": self.app_inventory()}
        # Keep context small and do not expose hidden system services as launch targets.
        apps = [{"id": app.get("id"), "title": app.get("title")} for app in response.get("apps", [])
                if app.get("visible", True) and app.get("id")]
        return {"apps": apps}

    def app_inventory(self):
        response = self.request("ssap://com.webos.applicationManager/listApps")
        fields = ("id", "title", "version", "vendor", "type", "inAppSearchParams", "deeplinkingParams")
        return [{key: app.get(key) for key in fields} for app in response.get("apps", [])
                if app.get("visible", True) and app.get("id")]

    def foreground(self):
        return self.request("ssap://com.webos.applicationManager/getForegroundAppInfo").get("appId")

    def native_app_action(self, app_id: str, operation: str, value: str = ""):
        metadata = next((app for app in self.app_inventory() if app["id"] == app_id), None)
        if not metadata:
            raise TVError("App is not installed or visible")
        if metadata.get("type") == "stub":
            raise TVError("App is a store stub; installation is required before exploration")
        if operation == "launch":
            return self.request("ssap://system.launcher/launch", {"id": app_id})
        if not isinstance(value, str) or not value.strip() or len(value) > 2048 or any(ord(c) < 32 for c in value):
            raise TVError("Native action requires a bounded nonempty string without control characters")
        if operation == "open_url":
            if app_id != "com.webos.app.browser":
                raise TVError("URL opening is restricted to the LG browser")
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise TVError("Only HTTP(S) URLs without embedded credentials are supported")
            return self.request("ssap://system.launcher/open", {"target": value})
        if operation == "youtube_video":
            if app_id != "youtube.leanback.v4" or not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
                raise TVError("YouTube video requires an 11-character video ID")
            return self.request("ssap://system.launcher/launch", {"id": app_id,
                "params": {"contentTarget": "https://www.youtube.com/tv?v=" + value}})
        if operation != "search":
            raise TVError("Supported native operations: launch, search, open_url, youtube_video")
        try:
            template = json.loads(metadata.get("inAppSearchParams") or "null")
        except (TypeError, ValueError) as error:
            raise TVError("App search metadata is malformed") from error
        if not isinstance(template, dict) or not template or set(template) - {"contentTarget", "target", "handledBy"}:
            raise TVError("App does not advertise a supported native search route; discover visually")
        if not any(isinstance(v, str) and "$SEARCH_KEYWORD" in v for v in template.values()):
            raise TVError("App search metadata has no keyword placeholder")
        if any(not isinstance(v, str) or len(v) > 2048 for v in template.values()):
            raise TVError("App search template contains unsupported values")
        params = {key: text.replace("$SEARCH_KEYWORD", quote(value, safe="")) for key, text in template.items()}
        return self.request("ssap://system.launcher/launch", {"id": app_id, "params": params})

    def launch(self, app_id):
        return self.native_app_action(app_id, "launch")

    @staticmethod
    def validate_ui_steps(steps, value=''):
        if not isinstance(steps,list) or not 1 <= len(steps) <= 20:
            raise TVError('UI route requires 1-20 bounded steps')
        total_wait=0
        for step in steps:
            if not isinstance(step,dict) or len(step)!=1:
                raise TVError('Each UI step must contain exactly one operation')
            if 'press' in step:
                if step['press'] not in BUTTONS: raise TVError('Unsupported UI button')
            elif 'wait' in step:
                if type(step['wait']) not in {int,float} or not 0 <= step['wait'] <= 5: raise TVError('UI wait out of bounds')
                total_wait+=step['wait']
            elif 'text' in step or 'replace_text' in step:
                template=step.get('text',step.get('replace_text'))
                text=value if template=='$VALUE' else template
                if not isinstance(text,str) or not 1 <= len(text) <= 1000 or any(ord(c)<32 for c in text):
                    raise TVError('UI text requires 1-1000 characters without controls')
            elif 'submit' in step:
                if step['submit'] is not True: raise TVError('Submit must be true')
            else: raise TVError('Only press, wait, text, replace_text and submit steps are permitted')
        if total_wait > 10: raise TVError('UI route wait exceeds 10 seconds')

    def run_ui_steps(self, app_id, steps, value=''):
        self.validate_ui_steps(steps,value)
        deadline=time.monotonic()+30
        for step in steps:
            if self.foreground()!=app_id:
                raise TVError('UI route stopped: foreground changed')
            if time.monotonic()>deadline:
                raise TVError('UI route exceeded its 30-second execution window')
            if 'press' in step: self.press(step['press'])
            elif 'text' in step: self.text(value if step['text']=='$VALUE' else step['text'])
            elif 'replace_text' in step:
                self.request('ssap://com.webos.service.ime/insertText',
                             {'text':value if step['replace_text']=='$VALUE' else step['replace_text'],'replace':True})
            elif 'submit' in step: self.request('ssap://com.webos.service.ime/sendEnterKey')
            else: time.sleep(step['wait'])
        return {'sent':True,'acknowledged':False,'steps':len(steps)}

    def pointer_command(self, kind: str, **values):
        # No reader runs on this socket while idle. Drain protocol pings and prove
        # liveness before sending input; a local .connected flag is insufficient.
        if self.pointer and (not self.pointer.connected or time.monotonic()-self.pointer_checked_at > 5):
            try:
                nonce=os.urandom(8)
                self.pointer.settimeout(1)
                self.pointer.ping(nonce)
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    opcode,frame=self.pointer.recv_data_frame(control_frame=True)
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        raise ConnectionLost('Pointer closed before input')
                    if opcode == websocket.ABNF.OPCODE_PONG and frame.data==nonce:
                        self.pointer_checked_at=time.monotonic()
                        break
                else:
                    raise ConnectionLost('Pointer did not confirm liveness')
            except (websocket.WebSocketException,OSError,ConnectionLost):
                self.pointer.close();self.pointer=None
            finally:
                if self.pointer: self.pointer.settimeout(self.timeout)
        if not self.pointer:
            url = self.request("ssap://com.webos.service.networkinput/getPointerInputSocket").get("socketPath", "")
            parts = urlsplit(url)
            if parts.hostname != self.host or parts.scheme not in {"ws", "wss"} or parts.port not in {3000, 3001}:
                raise TVError("TV returned an unexpected pointer endpoint")
            # Same service on the encrypted listener, never send input over ws.
            url = f"wss://{self.host}:3001{parts.path}" + ("?" + parts.query if parts.query else "")
            self.pointer = self._open_socket(url)
            self.pointer_checked_at=time.monotonic()
        try:
            self.pointer.send("type:" + kind + "\n" + "".join(f"{key}:{value}\n" for key, value in values.items()) + "\n")
        except (websocket.WebSocketException, OSError) as error:
            self.close()
            raise ConnectionLost("Pointer delivery unknown; command will not be replayed") from error
        return {"sent": True, "acknowledged": False, "verification": "Inspect the fresh observation or TV"}

    def press(self, button):
        if button not in BUTTONS:
            raise TVError("Allowed keys: " + ", ".join(sorted(BUTTONS)))
        return self.pointer_command("button", name=button)

    def move(self, dx: int, dy: int):
        if type(dx) is not int or type(dy) is not int or abs(dx) > 2000 or abs(dy) > 2000:
            raise TVError("Relative motion requires integer deltas from -2000 to 2000")
        return self.pointer_command("move", dx=dx, dy=dy, down=0)

    def click(self):
        return self.pointer_command("click")

    def scroll(self, dy: int):
        if type(dy) is not int or abs(dy) > 100:
            raise TVError("Scroll requires an integer from -100 to 100")
        return self.pointer_command("scroll", dx=0, dy=dy)

    def text(self, text: str):
        if not text or len(text) > 1000:
            raise TVError("Text must contain 1–1000 characters")
        return self.request("ssap://com.webos.service.ime/insertText", {"text": text, "replace": False})


def main():
    from tv_cli import main as cli_main
    cli_main()

if __name__ == "__main__":
    main()
