import asyncio
import datetime
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

import webrtc_bridge
from sip_client import SipClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("unifi_talk_softphone")

OPTIONS_PATH = Path("/data/options.json")

try:
    with OPTIONS_PATH.open("r", encoding="utf-8") as f:
        opts = json.load(f)
except (OSError, json.JSONDecodeError) as e:
    log.error("Konnte %s nicht laden: %s", OPTIONS_PATH, e)
    raise SystemExit(1)

TALK_SIP_HOST = opts["talk_sip_host"].strip()
TALK_SIP_PORT = int(opts.get("talk_sip_port") or 5060)
SIP_EXTENSION = opts["sip_extension"].strip()
SIP_PASSWORD = opts["sip_password"]
SIP_DOMAIN = (opts.get("sip_domain") or "talk.com").strip()
LOCAL_SIP_PORT = int(opts.get("local_sip_port") or 5070)
CALL_HANDLING = opts.get("call_handling") or "log_only"
NOTIFY_ON_CALL = bool(opts.get("notify_on_call", True))
REGISTER_EXPIRY = int(opts.get("register_expiry") or 300)

# Telefonie (WebRTC-Bruecke, siehe webrtc_bridge.py). Fuer die TURN-Relay-Seite
# (noetig, damit das Annehmen/Telefonieren auch von unterwegs funktioniert, nicht
# nur im LAN) wird Cloudflares gehosteter TURN-Dienst genutzt (turn.cloudflare.com,
# Teil von "Cloudflare Realtime") statt eines selbst betriebenen Servers - kein
# Port-Forwarding noetig. Ohne cf_turn_key_id/cf_turn_api_token funktioniert das
# Annehmen trotzdem, dann aber nur im selben LAN wie der Add-on-Host (reine
# Host-ICE-Kandidaten).
ENABLE_CALLING = bool(opts.get("enable_calling", True))
CF_TURN_KEY_ID = (opts.get("cf_turn_key_id") or "").strip()
CF_TURN_API_TOKEN = (opts.get("cf_turn_api_token") or "").strip()
CF_TURN_TTL = 3600  # Gueltigkeit der kurzlebigen TURN-Zugangsdaten in Sekunden

CALL_LOG_PATH = Path("/data/call_log.json")
CALL_LOG_LOCK = asyncio.Lock()
MAX_CALL_LOG_ENTRIES = 200

# Fuer Benachrichtigungen bei eingehenden Anrufen wird die Home-Assistant-Core-API
# ueber den Supervisor-Proxy angesprochen (erfordert "homeassistant_api: true" in
# config.yaml, dann steht SUPERVISOR_TOKEN automatisch als Env-Var zur Verfuegung).
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_API_BASE = "http://supervisor/core/api"
HA_NOTIFICATION_ID = "unifi_talk_incoming_call"

STATUS = {"registered": False, "last_error": None, "last_change": None}
SIP_CLIENT = None
ACTIVE_CALL_SESSION = None


def _on_registered(registered, error):
    STATUS["registered"] = registered
    STATUS["last_error"] = error
    STATUS["last_change"] = datetime.datetime.now().isoformat(timespec="seconds")


def _on_hangup():
    """Wird von sip_client aufgerufen, wenn die Gegenseite (Anrufer oder
    UniFi-Console) per BYE/CANCEL auflegt - schliesst eine ggf. noch offene
    WebRTC-Bruecke zum Browser mit."""
    asyncio.create_task(_close_active_call_session())


async def _close_active_call_session():
    global ACTIVE_CALL_SESSION
    if ACTIVE_CALL_SESSION:
        await ACTIVE_CALL_SESSION.close()
        ACTIVE_CALL_SESSION = None


def _load_call_log():
    if not CALL_LOG_PATH.exists():
        return []
    try:
        with CALL_LOG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Anruf-Historie konnte nicht geladen werden: %s", e)
        return []


def _save_call_log(entries):
    try:
        tmp_path = CALL_LOG_PATH.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CALL_LOG_PATH)
    except OSError as e:
        log.warning("Anruf-Historie konnte nicht gespeichert werden: %s", e)


async def record_call(caller):
    async with CALL_LOG_LOCK:
        entries = _load_call_log()
        entries.append({
            "ts": time.time(),
            "number": caller.get("number", ""),
            "name": caller.get("name", ""),
            "handling": CALL_HANDLING,
        })
        entries = entries[-MAX_CALL_LOG_ENTRIES:]
        _save_call_log(entries)


async def notify_ha_call(session, caller):
    """Meldet einen eingehenden Anruf an Home Assistant - als
    persistent_notification (sofort sichtbar in der HA-Glocke) und als Event
    "unifi_talk_incoming_call" (fuer eigene Automatisierungen, z.B. TTS-Ansage
    oder Push-Benachrichtigung). Best-effort: ein Fehler hier blockiert nie das
    Loggen des Anrufs selbst."""
    if not NOTIFY_ON_CALL or not SUPERVISOR_TOKEN:
        return

    number = caller.get("number", "unbekannt")
    name = caller.get("name", "")
    display = f"{name} ({number})" if name else number

    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with session.post(
            f"{HA_API_BASE}/services/persistent_notification/create",
            headers=headers,
            json={
                "title": "Eingehender Anruf (UniFi Talk)",
                "message": display,
                "notification_id": f"{HA_NOTIFICATION_ID}_{int(time.time())}",
            },
        ) as r:
            r.raise_for_status()
    except aiohttp.ClientError as e:
        log.warning("HA-Benachrichtigung (persistent_notification) fehlgeschlagen: %s", e)

    try:
        async with session.post(
            f"{HA_API_BASE}/events/{HA_NOTIFICATION_ID}",
            headers=headers,
            json={"number": number, "name": name, "ts": time.time()},
        ) as r:
            r.raise_for_status()
    except aiohttp.ClientError as e:
        log.warning("HA-Event '%s' konnte nicht gefeuert werden: %s", HA_NOTIFICATION_ID, e)


def format_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")


# --- Cloudflare Realtime TURN ------------------------------------------------

CF_TURN_API = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"


async def fetch_cf_ice_servers(http_session):
    """Holt kurzlebige TURN-Zugangsdaten von Cloudflares gehostetem TURN-Dienst
    (Teil von "Cloudflare Realtime", vormals "Calls") - ersetzt einen selbst
    betriebenen TURN-Server, ganz ohne Port-Forwarding am Router. Ohne
    konfigurierte cf_turn_key_id/cf_turn_api_token (oder bei einem Fehler) wird
    eine leere Liste zurueckgegeben - WebRTC funktioniert dann nur ueber reine
    Host-Kandidaten, also nur im selben LAN wie der Add-on-Host."""
    if not (CF_TURN_KEY_ID and CF_TURN_API_TOKEN):
        return []
    url = CF_TURN_API.format(key_id=CF_TURN_KEY_ID)
    try:
        async with http_session.post(
            url,
            headers={"Authorization": f"Bearer {CF_TURN_API_TOKEN}", "Content-Type": "application/json"},
            json={"ttl": CF_TURN_TTL},
        ) as r:
            r.raise_for_status()
            data = await r.json()
    except aiohttp.ClientError as e:
        log.warning("Cloudflare-TURN-Zugangsdaten konnten nicht abgerufen werden: %s", e)
        return []

    servers = data.get("iceServers") or []
    if isinstance(servers, dict):
        servers = [servers]

    # Der alternative Port 53 wird von Browsern blockiert (siehe Cloudflare-Doku)
    # - ohne Trickle-ICE (wir warten auf vollstaendiges Gathering, siehe
    # webrtc_bridge.CallSession._wait_ice_complete) fuehrt das sonst nur zu
    # unnoetigen Timeout-Verzoegerungen beim Verbindungsaufbau. Wichtig: exakt
    # Port 53 matchen, nicht nur die Zeichenkette ":53" - sonst faellt z.B. Port
    # 5349 (TURNS) faelschlich mit raus.
    filtered = []
    for s in servers:
        urls = s.get("urls")
        if isinstance(urls, list):
            urls = [u for u in urls if not re.search(r":53(?:\?|$)", u)]
        filtered.append({**s, "urls": urls})
    return filtered


# --- Dashboard ---------------------------------------------------------------

PAGE_STYLE = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px;
       background: #f4f5f7; color: #1c1e21; }
h1 { font-size: 1.4rem; margin-bottom: 4px; }
.sub { color: #666; margin-bottom: 20px; }
.card { background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.status-ok { color: #1b8a3d; font-weight: 600; }
.status-bad { color: #c0392b; font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; font-size: .92rem; }
th { color: #666; font-weight: 600; }
.empty { color: #888; font-style: italic; padding: 8px 0; }
ol { padding-left: 20px; }
ol li { margin-bottom: 10px; }
code { background: #eef0f2; padding: 1px 5px; border-radius: 4px; }
.warn { background: #fff8e6; border: 1px solid #f0dca0; border-radius: 8px; padding: 10px 14px;
        font-size: .9rem; margin-bottom: 16px; }
.call-banner { background: #e8f4ea; border: 1px solid #9fd6ac; display: none; }
.call-banner.active { background: #e9f0ff; border: 1px solid #a9c3f5; }
.call-banner button { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer;
                       font-size: .95rem; margin-right: 8px; margin-top: 10px; }
.btn-answer { background: #1b8a3d; color: #fff; }
.btn-decline, .btn-hangup { background: #c0392b; color: #fff; }
"""


def render_dashboard(status, calls):
    configured = bool(TALK_SIP_HOST and SIP_EXTENSION and SIP_PASSWORD)
    if status["registered"]:
        status_html = '<span class="status-ok">&#9679; Registriert</span> bei UniFi Talk als Extension ' \
                      f'<code>{html.escape(SIP_EXTENSION)}</code>'
    elif not configured:
        status_html = '<span class="status-bad">&#9679; Nicht konfiguriert</span> - ' \
                       'talk_sip_host, sip_extension und sip_password fehlen noch (siehe Setup-Anleitung unten)'
    else:
        reason = html.escape(status.get("last_error") or "wird versucht ...")
        status_html = f'<span class="status-bad">&#9679; Nicht registriert</span> ({reason})'

    if calls:
        rows = "".join(
            f"<tr><td>{html.escape(format_ts(c['ts']))}</td>"
            f"<td>{html.escape(c.get('name') or '-')}</td>"
            f"<td>{html.escape(c.get('number') or '-')}</td>"
            f"<td>{'abgelehnt' if c.get('handling') == 'decline' else 'nur geloggt'}</td></tr>"
            for c in reversed(calls)
        )
        table = f"<table><thead><tr><th>Zeitpunkt</th><th>Name</th><th>Nummer</th><th>Verhalten</th></tr></thead>" \
                f"<tbody>{rows}</tbody></table>"
    else:
        table = '<div class="empty">Noch keine Anrufe erkannt.</div>'

    calling_card = ""
    calling_script = ""
    if ENABLE_CALLING:
        turn_hint = "" if (CF_TURN_KEY_ID and CF_TURN_API_TOKEN) else (
            '<div class="warn">Kein Cloudflare-TURN-Key konfiguriert (<code>cf_turn_key_id</code>/'
            "<code>cf_turn_api_token</code>) - Annehmen/Telefonieren funktioniert dadurch nur im selben "
            "WLAN/LAN wie dieser Add-on-Host, nicht von unterwegs.</div>"
        )
        calling_card = f"""
<div class="card">
<h2 style="margin-top:0;font-size:1.1rem;">Telefonie</h2>
{turn_hint}
<div id="ringing-banner" class="card call-banner">
  <div id="ringing-text"></div>
  <button id="btn-answer" class="btn-answer">Annehmen</button>
  <button id="btn-decline" class="btn-decline">Ablehnen</button>
</div>
<div id="active-banner" class="card call-banner active">
  <div id="active-text">Verbunden</div>
  <audio id="remote-audio" autoplay></audio>
  <button id="btn-hangup" class="btn-hangup">Auflegen</button>
</div>
<div id="idle-text" class="empty">Aktuell klingelt kein Anruf.</div>
<div id="dial-box" style="margin-top:14px;">
  <input type="tel" id="dial-number" placeholder="Rufnummer, z. B. +49170..."
         style="padding:8px 10px;border:1px solid #ccc;border-radius:6px;font-size:.95rem;width:200px;">
  <button id="btn-dial" class="btn-answer">Anrufen</button>
</div>
</div>
"""
        calling_script = """
<script>
let pc = null;

function showRinging(caller) {
  document.getElementById("ringing-text").textContent =
    "Eingehender Anruf: " + (caller.name || "(unbekannt)") + " <" + caller.number + ">";
  document.getElementById("ringing-banner").style.display = "block";
  document.getElementById("active-banner").style.display = "none";
  document.getElementById("idle-text").style.display = "none";
}

function showActive() {
  document.getElementById("ringing-banner").style.display = "none";
  document.getElementById("active-banner").style.display = "block";
  document.getElementById("idle-text").style.display = "none";
}

function showIdle() {
  document.getElementById("ringing-banner").style.display = "none";
  document.getElementById("active-banner").style.display = "none";
  document.getElementById("idle-text").style.display = "block";
}

async function poll() {
  try {
    const r = await fetch("api/ringing");
    const data = await r.json();
    if (pc) return;  // waehrend eines laufenden/verbundenen Anrufs nicht ueberschreiben
    if (data.ringing) {
      showRinging(data.ringing.caller);
    } else if (data.active) {
      showActive();
    } else {
      showIdle();
    }
  } catch (e) { /* naechster Versuch in 1.5s */ }
}

function checkMicSupport() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert(
      "Mikrofon-Zugriff ist hier nicht verfügbar - das passiert typischerweise in der " +
      "eingebetteten Ansicht der Home-Assistant-App. Bitte diese Seite stattdessen direkt " +
      "in Safari/Chrome öffnen (in Home Assistant einloggen, dann in der Seitenleiste auf " +
      "„UniFi Talk” klicken).",
    );
    return false;
  }
  return true;
}

// Gemeinsamer Aufbau fuer beide Richtungen: eigenes Mikrofon einbinden, Offer
// erzeugen, auf vollstaendiges ICE-Gathering warten (kein Trickle-ICE - der
// Server erwartet eine bereits vollstaendige Antwort in einem Roundtrip).
async function createLocalOffer() {
  // Wichtig: getUserMedia() muss der ALLERERSTE await hier sein. Safari/iOS
  // verlangt, dass der Mikrofon-Zugriff noch innerhalb der "user activation"
  // des Klicks angefragt wird - ein vorheriger await (z.B. ein fetch()) reicht
  // oft schon aus, damit die Aktivierung verfaellt. Die Folge ist nicht ein
  // Fehler, sondern ein lautlos haengendes Promise ("Knopf ohne Funktion").
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  const iceResp = await fetch("api/ice-servers");
  const iceServers = await iceResp.json();
  const newPc = new RTCPeerConnection({ iceServers });
  newPc.ontrack = (event) => {
    document.getElementById("remote-audio").srcObject = event.streams[0];
  };

  stream.getTracks().forEach((t) => newPc.addTrack(t, stream));

  const offer = await newPc.createOffer();
  await newPc.setLocalDescription(offer);
  await new Promise((resolve) => {
    if (newPc.iceGatheringState === "complete") return resolve();
    newPc.addEventListener("icegatheringstatechange", () => {
      if (newPc.iceGatheringState === "complete") resolve();
    });
  });
  return newPc;
}

async function answerCall() {
  if (!checkMicSupport()) { showIdle(); return; }
  try {
    pc = await createLocalOffer();
    const resp = await fetch("webrtc/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert("Anruf konnte nicht angenommen werden: " + (err.error || resp.status));
      pc.close();
      pc = null;
      showIdle();
      return;
    }
    const answer = await resp.json();
    await pc.setRemoteDescription(answer);
    showActive();
  } catch (e) {
    alert("Annehmen fehlgeschlagen: " + e);
    if (pc) { pc.close(); pc = null; }
    showIdle();
  }
}

async function declineCall() {
  showIdle();
  await fetch("call/decline", { method: "POST" });
}

async function hangupCall() {
  await fetch("call/hangup", { method: "POST" });
  if (pc) { pc.close(); pc = null; }
  showIdle();
}

async function dialCall() {
  const number = document.getElementById("dial-number").value.trim();
  if (!number) { alert("Bitte eine Rufnummer eingeben."); return; }
  if (!checkMicSupport()) return;
  try {
    pc = await createLocalOffer();

    document.getElementById("active-text").textContent = "Rufe an: " + number + " ...";
    document.getElementById("ringing-banner").style.display = "none";
    document.getElementById("active-banner").style.display = "block";
    document.getElementById("idle-text").style.display = "none";

    const resp = await fetch("call/dial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ number: number, sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert("Anruf fehlgeschlagen: " + (err.error || resp.status));
      pc.close();
      pc = null;
      showIdle();
      return;
    }
    const answer = await resp.json();
    await pc.setRemoteDescription(answer);
    document.getElementById("active-text").textContent = "Verbunden mit " + number;
  } catch (e) {
    alert("Anrufen fehlgeschlagen: " + e);
    if (pc) { pc.close(); pc = null; }
    showIdle();
  }
}

document.getElementById("btn-answer").addEventListener("click", answerCall);
document.getElementById("btn-decline").addEventListener("click", declineCall);
document.getElementById("btn-hangup").addEventListener("click", hangupCall);
document.getElementById("btn-dial").addEventListener("click", dialCall);
setInterval(poll, 1500);
poll();
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>UniFi Talk Softphone</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<h1>&#128222; UniFi Talk Softphone</h1>
<div class="sub">Anrufer-Übersicht, Telefonie &amp; SIP-Setup</div>

<div class="card">
<strong>Status:</strong> {status_html}
</div>

{calling_card}

<div class="card">
<h2 style="margin-top:0;font-size:1.1rem;">Anruf-Historie</h2>
{table}
</div>

<div class="card">
<h2 style="margin-top:0;font-size:1.1rem;">Setup-Anleitung</h2>
<div class="warn">
Inoffizieller Workaround (SIP-Extension-Trick), da UniFi Talk in Deutschland keine
offiziellen Softphones/keine Anruf-API anbietet. Details siehe Dokumentations-Tab
dieses Add-ons (DOCS.md).
</div>
<ol>
<li><strong>Extension anlegen:</strong> UniFi Talk &rarr; Devices &rarr; „Set up device" &rarr;
„Third-Party Device" wählen, Extension notieren (z.&nbsp;B. <code>0007</code>).</li>
<li><strong>Zur Klingelgruppe hinzufügen</strong> (empfohlen), damit eure bestehenden Telefone
weiter normal klingeln.</li>
<li><strong>SIP-Passwort auslesen</strong> per SSH auf der Console:<br>
<code>fs_cli -x "user_data 0007@talk.com param password"</code></li>
<li><strong>In der Add-on-Konfiguration eintragen:</strong> <code>talk_sip_host</code>
(Management-IP der Console), <code>sip_extension</code>, <code>sip_password</code>.</li>
<li>Für Telefonie von unterwegs zusätzlich einen TURN-Key im
<a href="https://dash.cloudflare.com/?to=/:account/calls" target="_blank" rel="noopener">
Cloudflare Dashboard (Realtime/Calls → TURN)</a> anlegen und Token-ID/API-Token als
<code>cf_turn_key_id</code>/<code>cf_turn_api_token</code> eintragen - kein Router
nötig.</li>
<li>Add-on speichern und <strong>neu starten</strong>.</li>
</ol>
</div>

{calling_script}
</body>
</html>"""


async def dashboard_page(request):
    calls = await asyncio.get_running_loop().run_in_executor(None, _load_call_log)
    return web.Response(text=render_dashboard(STATUS, calls), content_type="text/html")


async def api_calls(request):
    calls = await asyncio.get_running_loop().run_in_executor(None, _load_call_log)
    return web.json_response({"status": STATUS, "calls": list(reversed(calls))})


async def api_ringing(request):
    ringing = SIP_CLIENT.get_ringing_call() if SIP_CLIENT else None
    active = bool(SIP_CLIENT and SIP_CLIENT.active_call)
    return web.json_response({"ringing": ringing, "active": active})


async def api_ice_servers(request):
    servers = await fetch_cf_ice_servers(request.app["http"])
    return web.json_response(servers)


async def webrtc_offer(request):
    global ACTIVE_CALL_SESSION
    if not ENABLE_CALLING or not SIP_CLIENT:
        return web.json_response({"error": "Telefonie-Funktion ist deaktiviert"}, status=404)
    if not SIP_CLIENT.get_ringing_call():
        return web.json_response({"error": "Es klingelt gerade kein Anruf"}, status=409)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Ungueltiges JSON"}, status=400)

    try:
        rtp = await SIP_CLIENT.answer_ringing_call()
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=409)

    payload_type = SIP_CLIENT.active_call["payload_type"]
    cf_servers = await fetch_cf_ice_servers(request.app["http"])
    ice_servers = webrtc_bridge.ice_servers_from_cloudflare(cf_servers)
    call = webrtc_bridge.CallSession(rtp, payload_type, ice_servers)
    try:
        answer_sdp, answer_type = await call.accept_offer(data["sdp"], data["type"])
    except Exception as e:
        log.exception("WebRTC-Verhandlung fuer eingehenden Anruf fehlgeschlagen")
        await call.close()
        return web.json_response({"error": f"WebRTC-Verhandlung fehlgeschlagen: {e}"}, status=500)

    ACTIVE_CALL_SESSION = call
    return web.json_response({"sdp": answer_sdp, "type": answer_type})


async def call_dial(request):
    """Startet einen ausgehenden Anruf: SIP_CLIENT.dial() haelt die Anfrage
    offen, bis die Gegenseite abnimmt/ablehnt oder ein Timeout eintritt (bis
    zu 40s) - das Frontend zeigt waehrenddessen "Rufe an ..." (siehe
    dialCall() im Dashboard-Skript)."""
    global ACTIVE_CALL_SESSION
    if not ENABLE_CALLING or not SIP_CLIENT:
        return web.json_response({"error": "Telefonie-Funktion ist deaktiviert"}, status=404)

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Ungueltiges JSON"}, status=400)

    number = (data.get("number") or "").strip()
    if not number:
        return web.json_response({"error": "Keine Rufnummer angegeben"}, status=400)

    try:
        rtp = await SIP_CLIENT.dial(number)
    except RuntimeError as e:
        return web.json_response({"error": str(e)}, status=502)

    payload_type = SIP_CLIENT.active_call["payload_type"]
    cf_servers = await fetch_cf_ice_servers(request.app["http"])
    ice_servers = webrtc_bridge.ice_servers_from_cloudflare(cf_servers)
    call = webrtc_bridge.CallSession(rtp, payload_type, ice_servers)
    try:
        answer_sdp, answer_type = await call.accept_offer(data["sdp"], data["type"])
    except Exception as e:
        log.exception("WebRTC-Verhandlung fuer ausgehenden Anruf fehlgeschlagen")
        await call.close()
        await SIP_CLIENT.hangup_active_call()
        return web.json_response({"error": f"WebRTC-Verhandlung fehlgeschlagen: {e}"}, status=500)

    ACTIVE_CALL_SESSION = call
    return web.json_response({"sdp": answer_sdp, "type": answer_type})


async def call_decline(request):
    if SIP_CLIENT:
        SIP_CLIENT.decline_ringing_call()
    return web.json_response({"ok": True})


async def call_hangup(request):
    if SIP_CLIENT:
        await SIP_CLIENT.hangup_active_call()
    await _close_active_call_session()
    return web.json_response({"ok": True})


def build_dashboard_app(http_session):
    app = web.Application()
    app["http"] = http_session
    app.router.add_get("/", dashboard_page)
    app.router.add_get("/api/calls", api_calls)
    app.router.add_get("/api/ringing", api_ringing)
    app.router.add_get("/api/ice-servers", api_ice_servers)
    app.router.add_post("/webrtc/offer", webrtc_offer)
    app.router.add_post("/call/dial", call_dial)
    app.router.add_post("/call/decline", call_decline)
    app.router.add_post("/call/hangup", call_hangup)
    return app


async def main():
    global SIP_CLIENT

    if not (TALK_SIP_HOST and SIP_EXTENSION and SIP_PASSWORD):
        log.error(
            "talk_sip_host, sip_extension und sip_password muessen konfiguriert sein - "
            "siehe Dokumentations-Tab (DOCS.md) fuer die Einrichtung.",
        )
    if ENABLE_CALLING and not (CF_TURN_KEY_ID and CF_TURN_API_TOKEN):
        log.warning(
            "cf_turn_key_id/cf_turn_api_token nicht konfiguriert - Annehmen/Telefonieren "
            "funktioniert dadurch nur im selben LAN wie dieser Add-on-Host.",
        )

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as http_session:

        def on_call(caller):
            asyncio.create_task(_handle_incoming_call(http_session, caller))

        SIP_CLIENT = SipClient(
            host=TALK_SIP_HOST,
            port=TALK_SIP_PORT,
            local_port=LOCAL_SIP_PORT,
            extension=SIP_EXTENSION,
            password=SIP_PASSWORD,
            domain=SIP_DOMAIN,
            expiry=REGISTER_EXPIRY,
            call_handling=CALL_HANDLING,
            on_call=on_call,
            on_registered=_on_registered,
            on_hangup=_on_hangup,
        )

        if TALK_SIP_HOST and SIP_EXTENSION and SIP_PASSWORD:
            await SIP_CLIENT.start()

        dashboard_runner = web.AppRunner(build_dashboard_app(http_session))
        await dashboard_runner.setup()
        await web.TCPSite(dashboard_runner, "0.0.0.0", 8100).start()
        log.info("Dashboard (nur via Ingress) auf Port 8100 gestartet")

        try:
            await asyncio.Event().wait()
        finally:
            await _close_active_call_session()
            await SIP_CLIENT.stop()
            await dashboard_runner.cleanup()


async def _handle_incoming_call(http_session, caller):
    await record_call(caller)
    await notify_ha_call(http_session, caller)


if __name__ == "__main__":
    asyncio.run(main())
