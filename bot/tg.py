"""Minimal Telegram Bot API client (stdlib only) shared by the bot, the snapshot job and tv-levels alerts.

Messages are written WhatsApp-style (*bold*) so the same text works in every channel; to_html() converts
them for Telegram's HTML parse mode after escaping, so tickers/prices can never break the markup.
"""
import http.client, json, re, socket, threading, time, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"


def config():
    return json.loads(CONFIG.read_text())


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=1))


def target(name="group"):
    """Chat id for 'group' (falls back to the admin chat until the bot is added to a group) or 'admin'."""
    cfg = config()
    if name == "group" and cfg.get("group_chat_id"):
        return cfg["group_chat_id"]
    return cfg["admin_chat_id"]


def to_html(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"```\n?(.*?)```", r"<pre>\1</pre>", text, flags=re.S)  # monospace table
    return re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", text)


_local = threading.local()


class _IPv4Connection(http.client.HTTPSConnection):
    """This Mac resolves api.telegram.org to IPv6 first and that route hangs ~20s before falling back, which made
    every fresh connection (and so every bot reply) late. Connect over IPv4 only."""

    def connect(self):
        host = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        self.sock = socket.create_connection((host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _post(path, body, ctype, timeout):
    """POST over a kept-alive HTTPS connection (one per thread): ~0.3s a call instead of ~0.9s for a fresh TLS handshake."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _IPv4Connection("api.telegram.org", timeout=timeout)
    conn.timeout = timeout
    if conn.sock is not None:
        conn.sock.settimeout(timeout)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": ctype})
        r = conn.getresponse()
        return r.status, r.read()
    except Exception:
        conn.close()
        _local.conn = None
        raise


def _call(method, data=None, files=None, timeout=60, retries=3):
    path = f"/bot{config()['token']}/{method}"
    for attempt in range(retries):
        try:
            if files:
                body, ctype = _multipart(data or {}, files)
            else:
                body, ctype = json.dumps(data or {}).encode(), "application/json"
            try:
                code, raw = _post(path, body, ctype, timeout)
            except (http.client.RemoteDisconnected, http.client.BadStatusLine, BrokenPipeError, ConnectionResetError):
                code, raw = _post(path, body, ctype, timeout)   # idle keep-alive dropped by the server: reconnect once
            res = json.loads(raw or b"{}")
            if code == 200 and res.get("ok"):
                return res["result"]
            wait = (res.get("parameters") or {}).get("retry_after")
            if code == 429 and wait and attempt < retries - 1:
                time.sleep(wait + 1)
                continue
            if 400 <= code < 500:        # bad request / forbidden: retrying won't help
                raise RuntimeError(f"{method} {code}: {res.get('description')}")
            if attempt == retries - 1:
                raise RuntimeError(f"{method} {code}: {res.get('description')}")
        except (OSError, http.client.HTTPException):
            if attempt == retries - 1:
                raise
        time.sleep(2 * (attempt + 1))


def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, path in files.items():
        p = Path(path)
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\n'
                   f'Content-Type: {"image/png" if p.suffix == ".png" else "application/octet-stream"}\r\n\r\n'.encode() + p.read_bytes() + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def send_message(chat_id, text, **kw):
    return _call("sendMessage", {"chat_id": chat_id, "text": to_html(text), "parse_mode": "HTML",
                                 "disable_web_page_preview": True, **kw})


def edit_message(chat_id, message_id, text, **kw):
    return _call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": to_html(text),
                                     "parse_mode": "HTML", "disable_web_page_preview": True, **kw})


def answer_callback(callback_id, text=""):
    return _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text}, retries=1)


def send_photo(chat_id, path, caption=""):
    return _call("sendPhoto", {"chat_id": chat_id, "caption": to_html(caption), "parse_mode": "HTML"},
                 files={"photo": path}, timeout=120)


def send_document(chat_id, path, caption=""):
    return _call("sendDocument", {"chat_id": chat_id, "caption": to_html(caption), "parse_mode": "HTML"},
                 files={"document": path}, timeout=120)


def send_album(chat_id, items):
    """items: [(path, caption)], 2-10 photos sent as one grouped message."""
    media, files = [], {}
    for i, (path, cap) in enumerate(items):
        media.append({"type": "photo", "media": f"attach://p{i}", "caption": to_html(cap), "parse_mode": "HTML"})
        files[f"p{i}"] = path
    return _call("sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}, files=files, timeout=180)


def get_updates(offset=None, timeout=50):
    data = {"timeout": timeout, "allowed_updates": ["message", "my_chat_member", "callback_query"]}
    if offset is not None:
        data["offset"] = offset
    return _call("getUpdates", data, timeout=timeout + 15, retries=1)


def set_commands(commands):
    return _call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})
