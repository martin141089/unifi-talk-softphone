FROM python:3.12-slim

WORKDIR /app

# coturn: TURN/STUN-Relay fuer WebRTC (noetig, damit die Browser-Audio-Verbindung
# auch von ausserhalb des LAN durch NATs kommt). aiortc/av bringen fuer amd64 UND
# aarch64 fertige Wheels inkl. gebuendeltem FFmpeg/Opus mit - kein Compile-Aufwand,
# keine zusaetzlichen -dev-Pakete noetig.
RUN apt-get update && apt-get install -y --no-install-recommends \
    coturn \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir aiohttp==3.10.11 "aiortc>=1.9,<2"

COPY sip_client.py /app/sip_client.py
COPY webrtc_bridge.py /app/webrtc_bridge.py
COPY run.py /app/run.py

CMD ["python3", "/app/run.py"]
