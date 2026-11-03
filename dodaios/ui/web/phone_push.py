"""Web Push for the phone companion: "Jarvis has finished" while the app is closed.

No payload is sent. A push without a body needs only the VAPID signature (ES256, from
``cryptography``, already a dependency); the service worker then asks ``/api/phone/push/last``
(same-origin, paired cookie) what to show. The VAPID private key is generated on this
computer on first use and never leaves it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

log = logging.getLogger(__name__)

#: Push services a browser can hand out. Anything else is refused (the server would otherwise
#: POST to an address chosen by whoever sends the subscription).
_PUSH_HOSTS = (
    "web.push.apple.com", ".push.apple.com", "fcm.googleapis.com",
    "updates.push.services.mozilla.com", ".push.services.mozilla.com", ".notify.windows.com",
)
#: Events worth a notification, with the text shown.
_NOTIFY = {
    "turn_finished": ("Jarvis ha finito", "Il lavoro è pronto."),
    "approval_required": ("Jarvis aspetta un tuo OK", "C'è un'azione da approvare."),
    "question_required": ("Jarvis ha una domanda", "Apri la chat per rispondere."),
}
_WATCH_TTL_S = 6 * 3600.0  # a chat the phone wrote to stays "watched" this long
_MIN_GAP_S = 3.0
#: A device that called the API this recently has the app open in front: no banner for it.
_PRESENT_S = 15.0


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return any(
        host == h.lstrip(".") or (h.startswith(".") and host.endswith(h)) for h in _PUSH_HOSTS
    )


class PushStore:
    """VAPID key, per-device subscriptions, and the "watched chats" the phone wrote to."""

    def __init__(self, root: Path) -> None:
        self._dir = Path(root)
        self._lock = threading.Lock()
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._subs: dict[str, dict[str, Any]] | None = None
        self._watched: dict[str, float] = {}
        self._last: dict[str, dict[str, Any]] = {}
        self._gap: dict[tuple[str, str], float] = {}
        self._seen: dict[str, float] = {}

    # ---- keys
    def _private(self) -> ec.EllipticCurvePrivateKey:
        with self._lock:
            if self._key is None:
                path = self._dir / "push-vapid.pem"
                if path.exists():
                    loaded = serialization.load_pem_private_key(path.read_bytes(), None)
                    if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                        raise ValueError("push key is not an EC key")
                    self._key = loaded
                else:
                    self._dir.mkdir(parents=True, exist_ok=True)
                    self._key = ec.generate_private_key(ec.SECP256R1())
                    pem = self._key.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(pem)
            return self._key

    def public_key(self) -> str:
        raw = self._private().public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return _b64u(raw)

    def vapid_headers(self, endpoint: str, subject: str) -> dict[str, str]:
        parts = urlsplit(endpoint)
        head = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(time.time()) + 12 * 3600, "sub": subject,
        }
        body = _b64u(json.dumps(claims, separators=(",", ":")).encode())
        signing = f"{head}.{body}".encode()
        r, s = decode_dss_signature(self._private().sign(signing, ec.ECDSA(hashes.SHA256())))
        sig = _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return {
            "Authorization": f"vapid t={head}.{body}.{sig}, k={self.public_key()}",
            "TTL": "3600", "Urgency": "normal", "Content-Length": "0",
        }

    # ---- subscriptions
    def _load(self) -> dict[str, dict[str, Any]]:
        if self._subs is None:
            try:
                data = json.loads((self._dir / "push-subs.json").read_text("utf-8"))
                self._subs = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._subs = {}
        return self._subs

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._dir / "push-subs.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._subs, fh)
        os.replace(tmp, self._dir / "push-subs.json")

    def subscribe(self, device_id: str, endpoint: str, subject: str) -> None:
        with self._lock:
            self._load()[device_id] = {"endpoint": endpoint, "subject": subject, "ts": time.time()}
            self._save()

    def unsubscribe(self, device_id: str) -> None:
        with self._lock:
            if self._load().pop(device_id, None) is not None:
                self._save()

    def subscription(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load().get(device_id)

    def device_ids(self) -> list[str]:
        with self._lock:
            return list(self._load())

    # ---- what to notify about
    def watch(self, session_id: str) -> None:
        with self._lock:
            self._watched[session_id] = time.monotonic() + _WATCH_TTL_S

    def note_event(self, session_id: str, kind: str) -> dict[str, Any] | None:
        """The notice for this event, or ``None`` when it is not for the phone."""
        if kind not in _NOTIFY:
            return None
        now = time.monotonic()
        with self._lock:
            if self._watched.get(session_id, 0.0) < now or not self._load():
                return None
            if now - self._gap.get((session_id, kind), -1e9) < _MIN_GAP_S:
                return None
            self._gap[(session_id, kind)] = now
            title, body = _NOTIFY[kind]
            notice = {
                "title": title, "body": body, "session": session_id,
                "kind": kind, "ts": time.time(),
            }
            for dev in self._load():
                self._last[dev] = notice
            return notice

    def seen(self, device_id: str) -> None:
        """The app just talked to us (it polls every few seconds while it is open in front)."""
        self._seen[device_id] = time.monotonic()

    def present(self, device_id: str) -> bool:
        return time.monotonic() - self._seen.get(device_id, -1e9) < _PRESENT_S

    def last(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._last.get(device_id)

    def forget_subscription_if_gone(self, device_id: str, status: int) -> None:
        if status in (404, 410):
            self.unsubscribe(device_id)


def _deliver(store: PushStore, device_id: str, is_active: Any) -> None:
    sub = store.subscription(device_id)
    if sub is None:
        return
    if not is_active(device_id):
        store.unsubscribe(device_id)
        return
    if store.present(device_id):
        return
    if not allowed_endpoint(sub["endpoint"]):
        store.unsubscribe(device_id)
        return
    import httpx

    try:
        res = httpx.post(
            sub["endpoint"], content=b"", timeout=10.0,
            headers=store.vapid_headers(sub["endpoint"], sub["subject"]),
        )
        store.forget_subscription_if_gone(device_id, res.status_code)
        if res.status_code >= 400:
            log.warning("phone push: service answered %s", res.status_code)
    except Exception as exc:  # noqa: BLE001 — a failed notification never breaks the chat
        log.warning("phone push: could not deliver (%s)", type(exc).__name__)


def attach(service: Any, hub: Any) -> None:
    """Listen to the chat service; the listener only records and hands off to a thread."""
    if not hasattr(service, "add_event_listener"):
        return

    def on_event(session_id: str, stored: dict[str, Any]) -> None:
        notice = hub.push.note_event(session_id, str((stored or {}).get("kind") or ""))
        if notice is None:
            return
        for device_id in hub.push.device_ids():
            threading.Thread(
                target=_deliver, args=(hub.push, device_id, hub.registry.is_active),
                name="phone-push", daemon=True,
            ).start()

    service.add_event_listener(on_event)
