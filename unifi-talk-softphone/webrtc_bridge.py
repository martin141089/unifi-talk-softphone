"""WebRTC-Bruecke zwischen dem Browser (Mikrofon/Lautsprecher im Dashboard) und
der RTP-Session zur Telefonie-Seite (sip_client.RtpSession, G.711).

Nutzt aiortc/PyAV fuer die eigentliche WebRTC-Verbindung (ICE, DTLS-SRTP) und
das Audio-Transcoding G.711 <-> Opus - sip_client.py selbst bleibt dadurch frei
von Media-Bibliotheken und kennt nur rohe RTP-Payload-Bytes.
"""

import asyncio
import audioop
import logging
from fractions import Fraction

import av
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

log = logging.getLogger("unifi_talk_webrtc")

SAMPLE_RATE = 8000
SAMPLES_PER_FRAME = 160  # 20ms bei 8kHz - Standardpaketierung fuer G.711
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 16-bit PCM

_DECODERS = {0: audioop.ulaw2lin, 8: audioop.alaw2lin}
_ENCODERS = {0: audioop.lin2ulaw, 8: audioop.lin2alaw}


class RtpToBrowserTrack(MediaStreamTrack):
    """Liest G.711-Payloads aus einer sip_client.RtpSession und liefert sie als
    PCM-Frames an aiortc - das kodiert sie Richtung Browser als Opus (inkl.
    Resampling 8kHz -> 48kHz, uebernimmt aiortc/PyAV automatisch)."""

    kind = "audio"

    def __init__(self, rtp_session, payload_type):
        super().__init__()
        self._rtp = rtp_session
        self._decode = _DECODERS[payload_type]
        self._timestamp = 0

    async def recv(self):
        try:
            payload = await asyncio.wait_for(self._rtp.recv_queue.get(), timeout=1.0)
            pcm = self._decode(payload, 2)
        except asyncio.TimeoutError:
            # Noch keine Audiodaten von der Telefonie-Seite (z.B. kurz nach dem
            # Annehmen) - Stille senden, damit der WebRTC-Stream nicht stockt.
            pcm = b"\x00\x00" * SAMPLES_PER_FRAME

        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.sample_rate = SAMPLE_RATE
        frame.planes[0].update(pcm)
        frame.pts = self._timestamp
        frame.time_base = Fraction(1, SAMPLE_RATE)
        self._timestamp += SAMPLES_PER_FRAME
        return frame


class BrowserToRtpBridge:
    """Liest dekodierte PCM-Frames vom Browser-Mikrofon-Track (aiortc liefert
    sie unabhaengig vom Leitungscodec bereits dekodiert), resampelt auf 8kHz
    mono und schickt sie als G.711-RTP-Pakete an die Telefonie-Seite.

    Der PyAV-Resampler liefert nicht garantiert exakt 160-Sample-Haeppchen pro
    Aufruf (interner Filter-Vorlauf, siehe Tests) - deshalb wird ueber einen
    Byte-Puffer auf feste 20ms-Pakete aufgeteilt, bevor sie verschickt werden.
    """

    def __init__(self, rtp_session, payload_type, track):
        self._rtp = rtp_session
        self._encode = _ENCODERS[payload_type]
        self._track = track
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self._buffer = bytearray()
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        try:
            while True:
                frame = await self._track.recv()
                for out_frame in self._resampler.resample(frame):
                    self._buffer += bytes(out_frame.planes[0])
                    while len(self._buffer) >= FRAME_BYTES:
                        chunk = bytes(self._buffer[:FRAME_BYTES])
                        del self._buffer[:FRAME_BYTES]
                        payload = self._encode(chunk, 2)
                        self._rtp.send_payload(payload, samples=SAMPLES_PER_FRAME)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Fehler beim Weiterleiten von Browser-Audio zur Telefonie-Seite")


def ice_servers_from_cloudflare(servers):
    """Wandelt die von der Cloudflare-Realtime-TURN-API zurueckgegebene Liste
    ({"urls": [...], "username": ..., "credential": ...}, siehe
    fetch_cf_ice_servers() in run.py) in aiortc-RTCIceServer-Objekte um. Ohne
    konfigurierte Cloudflare-Zugangsdaten kommt eine leere Liste an - WebRTC
    funktioniert dann nur ueber reine Host-Kandidaten, also nur im selben LAN
    wie der Add-on-Host."""
    result = []
    for s in servers or []:
        urls = s.get("urls")
        if not urls:
            continue
        result.append(RTCIceServer(urls=urls, username=s.get("username"), credential=s.get("credential")))
    return result


class CallSession:
    """Buendelt RTCPeerConnection + beide Bridge-Richtungen fuer einen
    einzelnen aktiven Anruf. Es ist immer nur ein Anruf gleichzeitig
    angenommen (siehe sip_client.SipClient.active_call)."""

    def __init__(self, rtp_session, payload_type, ice_servers):
        config = RTCConfiguration(iceServers=ice_servers or [])
        self.pc = RTCPeerConnection(configuration=config)
        self.pc.addTrack(RtpToBrowserTrack(rtp_session, payload_type))
        self._bridge = None

        @self.pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                self._bridge = BrowserToRtpBridge(rtp_session, payload_type, track)
                self._bridge.start()

    async def accept_offer(self, sdp, sdp_type):
        """Nimmt das SDP-Angebot des Browsers entgegen, erzeugt die eigene
        Antwort und wartet auf vollstaendiges ICE-Gathering (kein Trickle-ICE -
        die Antwort enthaelt dadurch bereits alle Kandidaten, kein zweiter
        Signaling-Roundtrip noetig). Gibt (sdp, type) der lokalen Antwort
        zurueck."""
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        await self._wait_ice_complete()
        return self.pc.localDescription.sdp, self.pc.localDescription.type

    async def _wait_ice_complete(self, timeout=10):
        if self.pc.iceGatheringState == "complete":
            return
        done = asyncio.get_running_loop().create_future()

        @self.pc.on("icegatheringstatechange")
        def on_change():
            if self.pc.iceGatheringState == "complete" and not done.done():
                done.set_result(None)

        try:
            await asyncio.wait_for(done, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("ICE-Gathering nicht innerhalb %ss abgeschlossen - sende SDP trotzdem", timeout)

    async def close(self):
        if self._bridge:
            self._bridge.stop()
        await self.pc.close()
