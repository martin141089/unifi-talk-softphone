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
import struct
import time

log = logging.getLogger("unifi_talk_sip")

# G.711-Codecs (RTP-Standard-Payload-Types, RFC 3551) - die einzigen, die dieser
# Client anbietet/akzeptiert, da sie ohne zusaetzliche Bibliotheken ueber das
# Python-Stdlib-Modul audioop transkodiert werden koennen.
PCMU, PCMA = 0, 8
_CODEC_NAMES = {PCMU: "PCMU", PCMA: "PCMA"}

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


def _build_response(
    req: SipMessage, status_code, reason, local_ip, local_port, extension,
    to_tag=None, with_contact=True, body="", content_type=None,
):
    lines = [f"SIP/2.0 {status_code} {reason}", f"Via: {req.get('Via')}", f"From: {req.get('From')}"]
    to = req.get("To")
    if to_tag and "tag=" not in to:
        to = f"{to};tag={to_tag}"
    lines.append(f"To: {to}")
    lines.append(f"Call-ID: {req.get('Call-ID')}")
    lines.append(f"CSeq: {req.get('CSeq')}")
    if with_contact:
        lines.append(f"Contact: <sip:{extension}@{local_ip}:{local_port}>")
    body_bytes = body.encode("utf-8")
    if content_type:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(body_bytes)}")
    lines.append("")
    lines.append(body)
    return "\r\n".join(lines)


def _parse_sdp(body: str):
    """Extrahiert Remote-RTP-IP/Port und angebotene Audio-Codecs (Payload-Types)
    aus einem SDP-Body (INVITE-Angebot). Gibt None zurueck, wenn kein
    brauchbarer Audio-Media-Block gefunden wurde."""
    conn_ip = None
    audio_port = None
    payload_types = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("c=IN IP4 "):
            conn_ip = line.split(" ", 2)[2].strip()
        elif line.startswith("m=audio "):
            parts = line.split(" ")
            if len(parts) >= 4:
                audio_port = int(parts[1])
                payload_types = [int(p) for p in parts[3:] if p.isdigit()]
    if conn_ip is None or audio_port is None:
        return None
    return {"ip": conn_ip, "port": audio_port, "payload_types": payload_types}


def _build_sdp_answer(local_ip, rtp_port, payload_type):
    codec_name = _CODEC_NAMES[payload_type]
    session_id = int(time.time())
    return (
        f"v=0\r\n"
        f"o=unifitalksoftphone {session_id} {session_id} IN IP4 {local_ip}\r\n"
        f"s=unifi-talk-softphone\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        f"t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP {payload_type}\r\n"
        f"a=rtpmap:{payload_type} {codec_name}/8000\r\n"
        f"a=sendrecv\r\n"
    )


class RtpSession(asyncio.DatagramProtocol):
    """Ein einzelner RTP-Audio-Strom (G.711) fuer die Telefonie-Seite eines
    aktiven Anrufs. Empfangene Payload-Bytes landen in recv_queue (fuer die
    WebRTC-Bridge); send_payload() verschickt Payload-Bytes als RTP-Paket an
    das ausgehandelte Remote-Ende."""

    def __init__(self, remote_ip, remote_port, payload_type):
        self.remote_addr = (remote_ip, remote_port)
        self.payload_type = payload_type
        self.transport = None
        self.recv_queue = asyncio.Queue(maxsize=50)
        self._seq = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)
        self._ssrc = random.randint(0, 0xFFFFFFFF)
        # Reine Zaehler fuer die Diagnose "kein Ton trotz erfolgreicher
        # Signalisierung" - ohne die ist von aussen nicht unterscheidbar, ob
        # ueberhaupt RTP-Pakete rausgehen/ankommen oder ob das Problem erst
        # in der WebRTC-Bruecke/Browser-Wiedergabe liegt.
        self._sent_count = 0
        self._recv_count = 0

    def connection_made(self, transport):
        self.transport = transport
        local = transport.get_extra_info("sockname")
        log.info("RTP-Session lokal auf %s gebunden, Ziel %s", local, self.remote_addr)

    def datagram_received(self, data, addr):
        if len(data) < 12:
            return
        payload = data[12:]
        self._recv_count += 1
        if self._recv_count == 1:
            log.info("RTP: erstes Paket empfangen von %s (%d Bytes Payload)", addr, len(payload))
        elif self._recv_count % 250 == 0:
            log.info("RTP: %d Pakete von %s empfangen bisher", self._recv_count, addr)
        if self.recv_queue.full():
            try:
                self.recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.recv_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    def send_payload(self, payload: bytes, samples=160):
        """samples = Anzahl PCM-Samples, die dieses Paket repraesentiert
        (G.711 @ 8kHz: 160 Samples = 20ms, der SIP-Standard-Paketrhythmus)."""
        if not self.transport:
            return
        header = struct.pack(
            "!BBHII",
            0x80, self.payload_type & 0x7F,
            self._seq & 0xFFFF, self._timestamp & 0xFFFFFFFF, self._ssrc,
        )
        self.transport.sendto(header + payload, self.remote_addr)
        self._sent_count += 1
        if self._sent_count == 1:
            log.info("RTP: erstes Paket gesendet an %s", self.remote_addr)
        elif self._sent_count % 250 == 0:
            log.info("RTP: %d Pakete an %s gesendet bisher", self._sent_count, self.remote_addr)
        self._seq = (self._seq + 1) & 0xFFFF
        self._timestamp = (self._timestamp + samples) & 0xFFFFFFFF

    def close(self):
        log.info(
            "RTP-Session beendet (Ziel %s): %d Pakete gesendet, %d empfangen",
            self.remote_addr, self._sent_count, self._recv_count,
        )
        if self.transport:
            self.transport.close()


async def _create_rtp_session(remote_ip, remote_port, payload_type):
    loop = asyncio.get_running_loop()
    protocol = RtpSession(remote_ip, remote_port, payload_type)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=("0.0.0.0", 0),
    )
    return protocol


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

    def __init__(
        self, host, port, local_port, extension, password, domain, expiry, call_handling,
        on_call, on_registered=None, on_hangup=None, ring_timeout=25,
    ):
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
        self.on_hangup = on_hangup
        self.ring_timeout = ring_timeout

        self.local_ip = None
        self.transport = None
        self.registered = False
        self.last_error = None
        self.ringing_call = None
        self.active_call = None

        self._from_tag = _gen_token(10)
        self._cseq = 1
        self._pending = {}
        self._invite_pending = {}
        self._stop = False
        self._dialing = False

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
            return
        # Fuer ausgehende INVITEs (dial()) kann es mehrere Antworten auf
        # dieselbe (Call-ID, CSeq) geben (z.B. 180 Ringing, dann 200 OK) - die
        # laufen ueber eine Queue statt eines einzelnen Future, siehe
        # _wait_invite_final().
        queue = self._invite_pending.get(call_id)
        if queue is not None and "INVITE" in msg.get("CSeq", ""):
            queue.put_nowait(msg)

    async def _wait_invite_final(self, queue, timeout, expected_cseq=None):
        """Wartet auf die finale Antwort (Statuscode >= 200) einer INVITE-
        Transaktion, ignoriert vorlaeufige Zwischenantworten (100 Trying, 180
        Ringing, ...). Gibt None bei Zeitueberschreitung zurueck.

        expected_cseq filtert zusaetzlich auf die CSeq-Nummer der gerade
        gestellten Anfrage: die Warteschlange ist nur nach Call-ID sortiert,
        nicht nach Transaktion, daher kann eine verspaetet eintreffende
        UDP-Retransmission einer FRUEHEREN Challenge (z.B. der Server sendet
        den ersten 407 nochmal, weil unser ACK ihn nicht rechtzeitig erreicht
        hat) sonst faelschlich als Antwort auf eine SPAETERE, bereits
        authentifizierte Anfrage durchgehen - das fuehrte zu einer
        vermeintlich zweiten Challenge mit identischem Nonce und in der Folge
        zu einem ueberlappenden dritten INVITE-Versuch ("500 Overlapping
        Requests")."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                msg = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                return None
            if expected_cseq is not None and msg.cseq_number() != expected_cseq:
                continue
            if msg.status_code is not None and msg.status_code < 200:
                continue
            return msg

    def _handle_request(self, msg: SipMessage):
        if msg.method == "OPTIONS":
            self._send(_build_response(msg, 200, "OK", self.local_ip, self.local_port, self.extension))
        elif msg.method == "INVITE":
            self._handle_invite(msg)
        elif msg.method in ("BYE", "CANCEL"):
            self._send(_build_response(msg, 200, "OK", self.local_ip, self.local_port, self.extension, with_contact=False))
            self._handle_remote_hangup(msg)
        elif msg.method == "ACK":
            pass
        else:
            log.debug("Unbehandelte SIP-Methode: %s", msg.method)

    def _handle_invite(self, msg: SipMessage):
        caller = _parse_caller(msg)
        call_id = msg.get("Call-ID")
        log.info("Eingehender Anruf erkannt: %s <%s>", caller["name"] or "(unbekannt)", caller["number"])

        to_tag = _gen_token(10)
        self._send(_build_response(msg, 180, "Ringing", self.local_ip, self.local_port, self.extension, to_tag=to_tag))

        # Der Anruf klingelt jetzt erstmal nur (kein automatisches Annehmen/Ablehnen) -
        # das gibt dem Dashboard ein Zeitfenster (ring_timeout), in dem die Nutzerin
        # ueber "Annehmen" den Anruf per WebRTC uebernehmen kann. Reagiert niemand,
        # greift nach Ablauf das konfigurierte call_handling (decline/log_only).
        self.ringing_call = {
            "call_id": call_id, "to_tag": to_tag, "invite_msg": msg,
            "caller": caller, "received_at": time.time(),
        }
        asyncio.create_task(self._ring_timeout(call_id))

        if self.on_call:
            try:
                self.on_call(caller)
            except Exception:
                log.exception("Fehler im Anruf-Callback")

    async def _ring_timeout(self, call_id):
        await asyncio.sleep(self.ring_timeout)
        call = self.ringing_call
        if not call or call["call_id"] != call_id:
            return  # laengst angenommen, abgelehnt oder vom Anrufer aufgelegt
        if self.call_handling == "decline":
            self._send(_build_response(
                call["invite_msg"], 486, "Busy Here", self.local_ip, self.local_port,
                self.extension, to_tag=call["to_tag"],
            ))
        self.ringing_call = None

    def _handle_remote_hangup(self, msg: SipMessage):
        """BYE/CANCEL vom Anrufer (oder von der UniFi-Console) fuer einen
        klingelnden oder aktiven Anruf - raeumt lokalen Zustand auf, egal in
        welcher Phase sich der Anruf gerade befand."""
        call_id = msg.get("Call-ID")
        ended = False
        if self.ringing_call and self.ringing_call["call_id"] == call_id:
            self.ringing_call = None
            ended = True
        if self.active_call and self.active_call["call_id"] == call_id:
            if self.active_call.get("rtp"):
                self.active_call["rtp"].close()
            self.active_call = None
            ended = True
        if ended and self.on_hangup:
            try:
                self.on_hangup()
            except Exception:
                log.exception("Fehler im Auflege-Callback")

    def get_ringing_call(self):
        call = self.ringing_call
        if not call:
            return None
        return {"call_id": call["call_id"], "caller": call["caller"], "received_at": call["received_at"]}

    def decline_ringing_call(self):
        call = self.ringing_call
        if not call:
            return
        self._send(_build_response(
            call["invite_msg"], 486, "Busy Here", self.local_ip, self.local_port,
            self.extension, to_tag=call["to_tag"],
        ))
        self.ringing_call = None

    async def answer_ringing_call(self):
        """Nimmt den aktuell klingelnden Anruf an: parst das SDP-Angebot aus dem
        INVITE, baut eine eigene RTP-Session (G.711) auf und beantwortet mit
        200 OK + eigener SDP-Antwort. Gibt die RtpSession zurueck (Rohdaten-
        Schnittstelle fuer die WebRTC-Bridge). Wirft RuntimeError, wenn gerade
        kein Anruf klingelt oder kein passender Codec (PCMU/PCMA) angeboten
        wurde."""
        call = self.ringing_call
        if not call:
            raise RuntimeError("Kein Anruf klingelt gerade")

        sdp = _parse_sdp(call["invite_msg"].body)
        if not sdp:
            raise RuntimeError("SDP im INVITE konnte nicht gelesen werden")

        payload_type = next((pt for pt in (PCMU, PCMA) if pt in sdp["payload_types"]), None)
        if payload_type is None:
            raise RuntimeError("Keine unterstuetzte Codec (PCMU/PCMA) angeboten")

        # Die Console meldet in ihrer SDP manchmal eine andere IP (z.B. ihre
        # oeffentliche WAN-IP) als self.host, obwohl sie fuer diese Extension
        # ausschliesslich im LAN erreichbar ist - ohne NAT-Hairpinning am
        # Router kommt dann kein RTP an (stumme Verbindung trotz erfolgreicher
        # Signalisierung). self.host ist bereits nachweislich erreichbar (dort
        # laeuft die SIP-Signalisierung drueber), daher wird er statt der
        # SDP-IP fuer das RTP-Ziel verwendet - nur der Port kommt aus der SDP.
        if sdp["ip"] != self.host:
            log.info("SDP meldet RTP-IP %s, verwende stattdessen %s (talk_sip_host)", sdp["ip"], self.host)
        rtp = await _create_rtp_session(self.host, sdp["port"], payload_type)
        local_rtp_port = rtp.transport.get_extra_info("sockname")[1]

        answer_body = _build_sdp_answer(self.local_ip, local_rtp_port, payload_type)
        self._send(_build_response(
            call["invite_msg"], 200, "OK", self.local_ip, self.local_port, self.extension,
            to_tag=call["to_tag"], body=answer_body, content_type="application/sdp",
        ))

        our_uri = f"sip:{self.extension}@{self.domain}"
        self.active_call = {
            **call, "rtp": rtp, "payload_type": payload_type,
            # Dialog-Identitaeten fuer ein spaeteres BYE (siehe hangup_active_call) -
            # wir waren hier die Angerufenen (UAS): unsere lokale Identitaet ist
            # unsere Extension + der von uns vergebene to_tag, die Gegenseite ist
            # unveraendert der From-Header des eingehenden INVITE.
            "local_identity": f"<{our_uri}>;tag={call['to_tag']}",
            "remote_identity": call["invite_msg"].get("From"),
        }
        self.ringing_call = None
        return rtp

    async def dial(self, number):
        """Startet einen ausgehenden Anruf zu number: baut eine eigene
        RTP-Session auf, schickt ein INVITE mit eigenem SDP-Angebot (PCMU) an
        die UniFi-Console und wartet auf die finale Antwort (inkl. optionalem
        Digest-Auth-Retry, falls die Console das INVITE selbst nochmal
        herausfordert). Gibt bei Erfolg die RtpSession zurueck (Call wird als
        aktiv vermerkt); wirft RuntimeError mit einer verstaendlichen
        Fehlermeldung bei Ablehnung/Zeitueberschreitung."""
        # active_call/ringing_call schuetzen nicht vor zwei *gleichzeitig*
        # gestarteten dial()-Aufrufen (z.B. Doppel-Tap auf "Anrufen") - keiner
        # von beiden hat active_call schon gesetzt, solange das erste INVITE
        # noch auf Antwort wartet. self._dialing schliesst dieses Fenster.
        if self.active_call or self.ringing_call or self._dialing:
            raise RuntimeError("Es läuft bereits ein Anruf")
        self._dialing = True

        rtp = await _create_rtp_session(self.host, self.port, PCMU)
        try:
            local_rtp_port = rtp.transport.get_extra_info("sockname")[1]
            offer_body = _build_sdp_answer(self.local_ip, local_rtp_port, PCMU)

            call_id = _gen_token(16) + "@unifi-talk-softphone"
            from_tag = _gen_token(10)
            target_uri = f"sip:{number}@{self.domain}"
            our_uri = f"sip:{self.extension}@{self.domain}"

            queue = asyncio.Queue()
            self._invite_pending[call_id] = queue
            try:
                cseq = self._next_cseq()
                self._send(self._build_invite(target_uri, our_uri, from_tag, call_id, cseq, offer_body))
                resp = await self._wait_invite_final(queue, timeout=40, expected_cseq=cseq)

                # Manche FreeSWITCH-Setups (wie bei UniFi Talk) haengen ein INVITE
                # ueber mehrere interne Hops (Registrar-Realm, dann ggf. ein
                # separater Trunk/Gateway-Realm) - jeder Hop kann eigenstaendig
                # per 401/407 herausfordern. Alle bisher gesammelten Auth-Header
                # (nach Header-Name dedupliziert) werden bei jedem Retry erneut
                # mitgeschickt, sonst "vergisst" der naechste Versuch die bereits
                # erfuellte Challenge eines vorherigen Hops. Bis zu 3 Runden, um
                # verschachtelte Challenges (Registrar + Trunk) abzudecken, ohne
                # bei einer echten Ablehnung endlos weiterzuversuchen.
                auth_headers = {}
                rounds = 0
                while resp is not None and resp.status_code in (401, 407) and rounds < 3:
                    rounds += 1
                    # Jede finale Antwort auf ein INVITE muss per ACK bestaetigt
                    # werden, auch eine Challenge - erst danach darf mit
                    # Zugangsdaten in einer NEUEN Transaktion (neue CSeq, gleiche
                    # Call-ID/gleicher From-Tag) erneut versucht werden.
                    self._send(self._build_ack(target_uri, our_uri, from_tag, resp.get("To"), call_id, cseq))
                    challenge_header = "WWW-Authenticate" if resp.status_code == 401 else "Proxy-Authenticate"
                    params = _parse_digest_challenge(resp.get(challenge_header))
                    if not params:
                        raise RuntimeError("Digest-Challenge konnte nicht gelesen werden")
                    auth_header_name = "Authorization" if resp.status_code == 401 else "Proxy-Authorization"
                    log.info(
                        "INVITE-Challenge #%d fuer %s: %s: %s",
                        rounds, number, challenge_header, resp.get(challenge_header),
                    )
                    auth_headers[auth_header_name] = _build_authorization(
                        self.extension, self.password, "INVITE", target_uri, params, header_name=auth_header_name,
                    )
                    cseq = self._next_cseq()
                    self._send(self._build_invite(
                        target_uri, our_uri, from_tag, call_id, cseq, offer_body,
                        auth_headers=list(auth_headers.values()),
                    ))
                    resp = await self._wait_invite_final(queue, timeout=40, expected_cseq=cseq)

                if resp is not None and resp.status_code in (401, 407):
                    log.warning(
                        "INVITE fuer %s haengt nach %d Auth-Runden weiter in einer Challenge-Schleife "
                        "(zuletzt %s) - vermutlich kein Digest-, sondern ein Berechtigungsproblem "
                        "auf der Console-Seite.", number, rounds, resp.status_code,
                    )

                if resp is None:
                    raise RuntimeError("Keine Antwort (Zeitüberschreitung)")
                if resp.status_code != 200:
                    self._send(self._build_ack(target_uri, our_uri, from_tag, resp.get("To"), call_id, cseq))
                    raise RuntimeError(f"Anruf nicht angenommen: {resp.status_code} {resp.reason}")

                sdp = _parse_sdp(resp.body)
                if not sdp:
                    raise RuntimeError("Keine gültige SDP-Antwort erhalten")
                # Siehe Kommentar in answer_ringing_call() - self.host statt der
                # von der Console gemeldeten SDP-IP verwenden, falls abweichend.
                if sdp["ip"] != self.host:
                    log.info("SDP meldet RTP-IP %s, verwende stattdessen %s (talk_sip_host)", sdp["ip"], self.host)
                rtp.remote_addr = (self.host, sdp["port"])

                to_header = resp.get("To")
                self._send(self._build_ack(target_uri, our_uri, from_tag, to_header, call_id, cseq))

                self.active_call = {
                    "call_id": call_id,
                    "local_identity": f"<{our_uri}>;tag={from_tag}",
                    "remote_identity": to_header,
                    "caller": {"number": number, "name": ""},
                    "received_at": time.time(),
                    "rtp": rtp,
                    "payload_type": PCMU,
                }
                return rtp
            finally:
                self._invite_pending.pop(call_id, None)
        except Exception:
            rtp.close()
            raise
        finally:
            self._dialing = False

    def _build_invite(self, request_uri, from_uri, from_tag, call_id, cseq, sdp_body, auth_headers=None):
        branch = "z9hG4bK" + _gen_token(16)
        lines = [
            f"INVITE {request_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <{from_uri}>;tag={from_tag}",
            f"To: <{request_uri}>",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} INVITE",
            f"Contact: <sip:{self.extension}@{self.local_ip}:{self.local_port}>",
            "Content-Type: application/sdp",
            "User-Agent: unifi-talk-softphone/0.1",
        ]
        for auth_header in (auth_headers or []):
            lines.append(auth_header)
        body_bytes = sdp_body.encode("utf-8")
        lines.append(f"Content-Length: {len(body_bytes)}")
        lines.append("")
        lines.append(sdp_body)
        return "\r\n".join(lines)

    def _build_ack(self, request_uri, from_uri, from_tag, to_header, call_id, cseq):
        branch = "z9hG4bK" + _gen_token(16)
        lines = [
            f"ACK {request_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: <{from_uri}>;tag={from_tag}",
            f"To: {to_header}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} ACK",
            "Content-Length: 0",
            "",
            "",
        ]
        return "\r\n".join(lines)

    async def hangup_active_call(self):
        """Legt einen aktiven Anruf (angenommen oder selbst gewaehlt) von
        unserer Seite auf, indem ein BYE verschickt wird - immer an die
        UniFi-Console (self.host/self.port), wie auch REGISTER: die Console
        agiert als B2BUA, jede Signalisierung laeuft ueber sie. local_identity/
        remote_identity wurden beim Annehmen (answer_ringing_call) bzw. Waehlen
        (dial) passend zur jeweiligen Dialog-Rolle hinterlegt."""
        call = self.active_call
        if not call:
            return

        cseq = self._next_cseq()
        branch = "z9hG4bK" + _gen_token(16)
        lines = [
            f"BYE sip:{self.domain} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport",
            "Max-Forwards: 70",
            f"From: {call['local_identity']}",
            f"To: {call['remote_identity']}",
            f"Call-ID: {call['call_id']}",
            f"CSeq: {cseq} BYE",
            "Content-Length: 0",
            "",
            "",
        ]
        self._send("\r\n".join(lines))

        if call.get("rtp"):
            call["rtp"].close()
        self.active_call = None
