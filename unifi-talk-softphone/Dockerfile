FROM python:3.12-slim

WORKDIR /app

# Reine SIP-Signalisierung (REGISTER + INVITE-Erkennung), kein Media/RTP -
# aiohttp fuer den HTTP-Server (Ingress-Dashboard) und die HA-Notification-API.
RUN pip install --no-cache-dir aiohttp==3.10.11

COPY sip_client.py /app/sip_client.py
COPY run.py /app/run.py

CMD ["python3", "/app/run.py"]
