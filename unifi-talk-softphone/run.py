import asyncio
import datetime
import html
import json
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

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


def _on_registered(registered, error):
    STATUS["registered"] = registered
    STATUS["last_error"] = error
    STATUS["last_change"] = datetime.datetime.now().isoformat(timespec="seconds")


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

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>UniFi Talk Softphone</title>
<style>{PAGE_STYLE}</style>
</head>
<body>
<h1>&#128222; UniFi Talk Softphone</h1>
<div class="sub">Anrufer-Übersicht &amp; SIP-Setup</div>

<div class="card">
<strong>Status:</strong> {status_html}
</div>

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
<li>Add-on speichern und <strong>neu starten</strong>.</li>
</ol>
</div>
</body>
</html>"""


async def dashboard_page(request):
    calls = await asyncio.get_running_loop().run_in_executor(None, _load_call_log)
    return web.Response(text=render_dashboard(STATUS, calls), content_type="text/html")


async def api_calls(request):
    calls = await asyncio.get_running_loop().run_in_executor(None, _load_call_log)
    return web.json_response({"status": STATUS, "calls": list(reversed(calls))})


def build_dashboard_app():
    app = web.Application()
    app.router.add_get("/", dashboard_page)
    app.router.add_get("/api/calls", api_calls)
    return app


async def main():
    global SIP_CLIENT

    if not (TALK_SIP_HOST and SIP_EXTENSION and SIP_PASSWORD):
        log.error(
            "talk_sip_host, sip_extension und sip_password muessen konfiguriert sein - "
            "siehe Dokumentations-Tab (DOCS.md) fuer die Einrichtung.",
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
        )

        if TALK_SIP_HOST and SIP_EXTENSION and SIP_PASSWORD:
            await SIP_CLIENT.start()

        dashboard_runner = web.AppRunner(build_dashboard_app())
        await dashboard_runner.setup()
        await web.TCPSite(dashboard_runner, "0.0.0.0", 8100).start()
        log.info("Dashboard (nur via Ingress) auf Port 8100 gestartet")

        try:
            await asyncio.Event().wait()
        finally:
            await SIP_CLIENT.stop()
            await dashboard_runner.cleanup()


async def _handle_incoming_call(http_session, caller):
    await record_call(caller)
    await notify_ha_call(http_session, caller)


if __name__ == "__main__":
    asyncio.run(main())
