FROM python:3.12-slim

WORKDIR /app

# aiortc/av bringen fuer amd64 UND aarch64 fertige Wheels inkl. gebuendeltem
# FFmpeg/Opus mit - kein Compile-Aufwand, keine System-Pakete noetig. Der
# TURN-Relay fuer WebRTC laeuft extern bei Cloudflare (siehe webrtc_bridge.py /
# fetch_cf_ice_servers in run.py), deshalb ist auch kein eigener TURN-Server
# (frueher: coturn) mehr Teil dieses Images.
RUN pip install --no-cache-dir aiohttp==3.10.11 "aiortc>=1.9,<2"

COPY sip_client.py /app/sip_client.py
COPY webrtc_bridge.py /app/webrtc_bridge.py
COPY run.py /app/run.py

CMD ["python3", "/app/run.py"]
