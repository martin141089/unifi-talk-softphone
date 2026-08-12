"""Minimaler SIP-User-Agent für UniFi Talk.

Kein vollstaendiger RFC-3261-Stack und kein Media/RTP - dieses Modul kann nur
zwei Dinge: sich per REGISTER (mit Digest-Auth) bei der UniFi-Console anmelden
und eingehende INVITE-Requests erkennen, um Anrufer-Nummer + Zeitpunkt zu
loggen. Das reicht für eine Anrufer-Uebersicht, ohne dass echtes Telefonieren
(Audio) noetig waere - siehe DOCS.md fuer den Hintergrund.

UniFi Talk laeuft auf FreeSWITCH, daher normale RFC-2617-Digest-Auth (MD5,
qop=auth) gegen einen Standard-SIP-Registrar - kein UniFi-spezifisches
Protokoll.
"""

import asyncio
import hashlib
import logging
import random
import re
import socket
import string

log = logging.getLogger("unifi_talk_sip")

_CANON_HEADERS = {
    "v": "Via", "via": "Via",
    "f": "From", "from": "From",
    "t": "To", "to": "To",
    "i": "Call-ID", "call-id": "Call-ID",
    "cseq": "CSeq",
    "m": "Contact", "contact": "Contact",
    "c": "Content-Type", "content-type": "Content-Type",
    "l": "Content-Length", "content-length": "Content-Length",
    "www-authenticate": "WWW-Authenticate",
    "proxy-authenticate": "Proxy-Authenticate",
    "max-forwards": "Max-Forwards",
    "expires": "Expires",
    "p-asserted-identity": "P-Asserted-Identity",
    "user-agent": "User-Agent",
}


def _md5(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def _gen_token(length=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _local_ip_towards(remote_host: str, remote_port: int) -> str:
    """Ermittelt die eigene LAN-IP, ueber die remote_host erreichbar waere -
    noetig fuer Via/Contact-Header, damit die Console eingehende Requests an
    die richtige Adresse zurueckschicken kann. connect() auf UDP-Sockets
    verschickt kein Paket, waehlt nur die Route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, remote_port))
        return s.getsockname()[0]
    finally:
        s.close()


class SipMessage:
    """Parst eine einzelne SIP-UDP-Nachricht (Request oder Response) in
    Start-Zeile + Header-Dict. Mehrfache gleichnamige Header (z.B. mehrere
    Via-Zeilen) werden gemaess RFC 3261 7.3.1 komma-separiert
    zusammengefasst - semantisch aequivalent zu getrennten Zeilen."""

    def __init__(self, raw: bytes):
        text = raw.decode("utf-8", errors="replace")
        head, _, body = text.partition("\r\n\r\n")
        lines = head.split("\r\n")
        self.start_line = lines[0] if lines else ""
        self.headers = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            name, _, value = line.partition(":")
            key = _CANON_HEADERS.get(name.strip().lower(), name.strip())
            value = value.strip()
            if key in self.headers:
                self.headers[key] = f"{self.headers[key]}, {value}"
            else:
                self.headers[key] = value
        self.body = body

        self.is_response = self.start_line.startswith("SIP/2.0")
        self.method = None
        self.status_code = None
        self.reason = ""
        if self.is_response:
            parts = self.start_line.split(" ", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                self.status_code = int(parts[1])
            if len(parts) >= 3:
                self.reason = parts[2]
        else:
            parts = self.start_line.split(" ")
            if parts:
                self.method = parts[0]

    def get(self, name, default=""):
        return self.headers.get(name, default)

    def cseq_number(self):
        raw = self.get("CSeq", "")
        try:
            return int(raw.split(" ", 1)[0])
        except ValueError:
            return None


def _parse_digest_challenge(header_value: str):
    if not header_value:
        return None
    params = {}
    for match in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', header_value):
        key = match.group(1)
        params[key] = match.group(2) if match.group(2) is not None else match.group(3)
    return params or None


def _build_authorization(username, password, method, uri, params, header_name="Authorization"):
    realm = params.get("realm", "")
    nonce = params.get("nonce", "")
    opaque = params.get("opaque")
    algorithm = params.get("algorithm", "MD5")
    qop_offered = (params.get("qop") or "").split(",")[0].strip()

    ha1 = _md5(f"{username}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")

    extra = ""
    if qop_offered:
        cnonce = _gen_token(8)
        nc = "00000001"
        response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_offered}:{ha2}")
        extra = f', qop={qop_offered}, nc={nc}, cnonce="{cnonce}"'
    else:
        response = _md5(f"{ha1}:{nonce}:{ha2}")

    opaque_part = f', opaque="{opaque}"' if opaque else ""
    return (
        f'{header_name}: Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm={algorithm}{extra}{opaque_part}'
    )


def _parse_caller(msg: SipMessage):
    """Extrahiert Anrufer-Nummer + Anzeigename aus P-Asserted-Identity (falls
    vorhanden, zuverlaessiger) oder sonst dem From-Header."""
    header = msg.get("P-Asserted-Identity") or msg.get("From")
    match = re.match(r'\s*"?([^"<]*)"?\s*<sip:([^@;>]+)', header)
    if match:
        return {"name": match.group(1).strip(), "number": match.group(2).strip()}
    uri_match = re.search(r"sip:([^@;>]+)", header)
    return {"name": "", "number": uri_match.group(1) if uri_match else header.strip()}


def _build_response(req: SipMessage, status_code, reason, local_ip, local_port, extension, to_tag=None, with_contact=True):
    lines = [f"SIP/2.0 {status_code} {reason}", f"Via: {req.get('Via')}", f"From: {req.get('From')}"]
    to = req.get("To")
    if to_tag and "tag=" not in to:
        to = f"{to};tag={to_tag}"
    lines.append(f"To: {to}")
    lines.append(f"Call-ID: {req.get('Call-ID')}")
    lines.append(f"CSeq: {req.get('CSeq')}")
    if with_contact:
        lines.append(f"Contact: <sip:{extension}@{local_ip}:{local_port}>")
    lines.append("Content-Length: 0")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client: "SipClient"):
        self._client = client

    def datagram_received(self, data, addr):
        try:
            msg = SipMessage(data)
        except Exception:
            log.debug("Konnte SIP-Paket von %s nicht parsen: %r", addr, data[:200])
            return
        log.debug("SIP <- %s:%s: %s", addr[0], addr[1], msg.start_line)
        if msg.is_response:
            self._client._handle_response(msg)
        else:
            self._client._handle_request(msg)

    def error_received(self, exc):
        log.warning("SIP-UDP-Socket-Fehler: %s", exc)


class SipClient:
    """Registriert eine Extension bei UniFi Talk und ruft on_call(caller) fuer
    jeden erkannten eingehenden Anruf auf. caller ist {"number": str, "name": str}."""

    def __init__(self, host, port, local_port, extension, password, domain, expiry, call_handling, on_call, on_registered=None):
        self.host = host
        self.port = port
        self.local_port = local_port
        self.extension = extension
        self.password = password
        self.domain = domain or host
        self.expiry = expiry
        self.call_handling = call_handling
        self.on_call = on_call
        self.on_registered = on_registered

        self.local_ip = None
        self.transport = None
        self.registered = False
        self.last_error = None

        self._from_tag = _gen_token(10)
        self._cseq = 1
        self._pending = {}
        self._stop = False

    async def start(self):
        self.local_ip = _local_ip_towards(self.host, self.port)
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=("0.0.0.0", self.local_port),
        )
        log.info(
            "SIP-Client gestartet: %s:%s -> lokal %s:%s (Extension %s)",
            self.host, self.port, self.local_ip, self.local_port, self.extension,
        )
        asyncio.create_task(self._register_loop())

    async def stop(self):
        self._stop = True
        if self.transport:
            self.transport.close()

    def _send(self, message: str):
        self.transport.sendto(message.encode("utf-8"), (self.host, self.port))

    def _next_cseq(self):
        value = self._cseq
        self._cseq += 1
        return value

    async def _register_loop(self):
        backoff = 5
        while not self._stop:
            try:
                ok = await self._register_once()
            except Exception as e:  # noqa: BLE001 - Registrierung darf den Loop nie beenden
                log.exception("Unerwarteter Fehler bei SIP-Registrierung: %s", e)
                ok = False
                self.last_error = str(e)

            if ok:
                was_registered = self.registered
                self.registered = True
                self.last_error = None
                if self.on_registered:
                    self.on_registered(True, None)
                if not was_registered:
                    log.info("SIP-Registrierung erfolgreich (Extension %s)", self.extension)
                backoff = 5
                await asyncio.sleep(max(30, self.expiry - 30))
            else:
                if self.registered and self.on_registered:
                    self.on_registered(False, self.last_error)
                self.registered = False
                log.warning("SIP-Registrierung fehlgeschlagen, neuer Versuch in %ds", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def _register_once(self):
        call_id = _gen_token(16) + "@unifi-talk-softphone"
        uri = f"sip:{self.domain}"
        to_from_uri = f"sip:{self.extension}@{self.domain}"

        cseq1 = self._next_cseq()
        req1 = self._build_register(uri, to_from_uri, call_id, cseq1, auth_header=None)
        resp = await self._send_and_wait(req1, call_id, cseq1)
        if resp is None:
            self.last_error = "Keine Antwort auf REGISTER (Host/Port erreichbar?)"
            return False
        if resp.status_code == 200:
            return True
        if resp.status_code not in (401, 407):
            self.last_error = f"Unerwartete Antwort auf REGISTER: {resp.status_code} {resp.reason}"
            log.warning(self.last_error)
            return False

        challenge_header = "WWW-Authenticate" if resp.status_code == 401 else "Proxy-Authenticate"
        params = _parse_digest_challenge(resp.get(challenge_header))
        if not params:
            self.last_error = "Digest-Challenge konnte nicht gelesen werden"
            log.error("%s: %r", self.last_error, resp.get(challenge_header))
            return False

        auth_header_name = "Authorization" if resp.status_code == 401 else "Proxy-Authorization"
        auth_header = _build_authorization(
            self.extension, self.password, "REGISTER", uri, params, header_name=auth_header_name,
        )

        cseq2 = self._next_cseq()
        req2 = self._build_register(uri, to_from_uri, call_id, cseq2, auth_header=auth_header)
        resp2 = await self._send_and_wait(req2, call_id, cseq2)
        if resp2 is None:
            self.last_error = "Keine Antwort auf authentifiziertes REGISTER"
            return False
        if resp2.status_code == 200:
            return True
        self.last_error = f"Authentifizierung fehlgeschlagen: {resp2.status_code} {resp2.reason} (Extension/Passwort pruefen)"
        log.warning(self.last_error)
        return False

    def _build_register(self, request_uri, to_from_uri, call_id, cseq, auth_header):
        branch = "z9hG4bK" + _gen_token(16)
        lines = [
            f"REGISTER {request_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <{to_from_uri}>;tag={self._from_tag}",
            f"To: <{to_from_uri}>",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} REGISTER",
            f"Contact: <sip:{self.extension}@{self.local_ip}:{self.local_port}>;expires={self.expiry}",
            f"Expires: {self.expiry}",
            "User-Agent: unifi-talk-softphone/0.1",
        ]
        if auth_header:
            lines.append(auth_header)
        lines.append("Content-Length: 0")
        lines.append("")
        lines.append("")
        return "\r\n".join(lines)

    async def _send_and_wait(self, raw_message, call_id, cseq, timeout=5):
        key = (call_id, cseq)
        fut = asyncio.get_running_loop().create_future()
        self._pending[key] = fut
        try:
            self._send(raw_message)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(key, None)

    def _handle_response(self, msg: SipMessage):
        cseq_num = msg.cseq_number()
        call_id = msg.get("Call-ID")
        if cseq_num is None or not call_id:
            return
        fut = self._pending.get((call_id, cseq_num))
        if fut and not fut.done():
            fut.set_result(msg)

    def _handle_request(self, msg: SipMessage):
        if msg.method == "OPTIONS":
            self._send(_build_response(msg, 200, "OK", self.local_ip, self.local_port, self.extension))
        elif msg.method == "INVITE":
            self._handle_invite(msg)
        elif msg.method in ("BYE", "CANCEL"):
            self._send(_build_response(msg, 200, "OK", self.local_ip, self.local_port, self.extension, with_contact=False))
        elif msg.method == "ACK":
            pass
        else:
            log.debug("Unbehandelte SIP-Methode: %s", msg.method)

    def _handle_invite(self, msg: SipMessage):
        caller = _parse_caller(msg)
        log.info("Eingehender Anruf erkannt: %s <%s>", caller["name"] or "(unbekannt)", caller["number"])

        to_tag = _gen_token(10)
        self._send(_build_response(msg, 180, "Ringing", self.local_ip, self.local_port, self.extension, to_tag=to_tag))

        if self.on_call:
            try:
                self.on_call(caller)
            except Exception:
                log.exception("Fehler im Anruf-Callback")

        if self.call_handling == "decline":
            self._send(_build_response(msg, 486, "Busy Here", self.local_ip, self.local_port, self.extension, to_tag=to_tag))
