# Changelog

Alle nennenswerten Änderungen an diesem Add-on werden hier dokumentiert.

## [0.3.1]

### Behoben
- **„Anrufen"-Button reagierte in Safari/iOS auf nichts.** `createLocalOffer()`
  rief `getUserMedia()` erst NACH einem `await fetch(...)` auf - Safari/iOS
  verlangt aber, dass der Mikrofon-Zugriff noch innerhalb der User-Activation
  des Klicks angefragt wird. Die Folge war kein Fehler, sondern ein lautlos
  haengendes Promise, das nie zur eigentlichen Anfrage an den Server kam
  (bestaetigt per Server-Log: keine einzige `/call/dial`-Anfrage trotz Klicks).
  `getUserMedia()` ist jetzt der allererste await in der Funktion. Betraf auch
  "Annehmen", nicht nur "Anrufen".

## [0.3.0]

### Hinzugefügt
- **Ausgehende Anrufe (Wählen).** Neues Eingabefeld + „Anrufen"-Button im
  Dashboard: `sip_client.py` kann jetzt selbst ein INVITE mit eigenem
  SDP-Angebot verschicken, wartet auf die finale Antwort (inkl.
  Digest-Auth-Retry, falls die Console das INVITE herausfordert) und baut bei
  Erfolg dieselbe WebRTC-Brücke wie beim Annehmen auf. Rückmeldungen wie
  „486 Busy" landen als verständliche Fehlermeldung im Dashboard.
- Neuer Endpoint `POST /call/dial` ({"number", "sdp", "type"} → SDP-Antwort
  oder Fehler).

### Geändert
- `hangup_active_call()` ist jetzt richtungsunabhängig (funktioniert für
  angenommene *und* selbst gewählte Anrufe) - dafür wurde das interne
  Dialog-Datenmodell auf `local_identity`/`remote_identity` vereinheitlicht.

Kompletter Wähl-Flow wurde end-to-end gegen einen simulierten SIP-Server +
simulierten Browser getestet: INVITE/ACK/200 OK, WebRTC-Aushandlung, Audio in
beide Richtungen, Auflegen - sowie separat Ablehnung (486) und
Digest-Auth-Retry.

## [0.2.2]

### Behoben
- **„Annehmen" schlug in der Home-Assistant-App mit einer kryptischen
  TypeError fehl.** Die App-WebView stellt auf iOS/Android kein
  `navigator.mediaDevices` bereit (kein Mikrofon-Zugriff möglich). Zeigt jetzt
  eine verständliche Fehlermeldung mit Hinweis, das Dashboard stattdessen
  direkt in Safari/Chrome zu öffnen - dort funktioniert es normal.

## [0.2.1]

### Geändert
- **Eigener TURN-Server (coturn) durch Cloudflare Realtime TURN ersetzt.**
  Telefonie von unterwegs braucht dadurch keine Portfreigabe am Router mehr - nur
  einen TURN-Key aus dem Cloudflare Dashboard (`cf_turn_key_id`/`cf_turn_api_token`
  statt der bisherigen `turn_username`/`turn_password`/`turn_public_host`/
  `turn_relay_port_start`/`turn_relay_port_end`). Ein normaler Cloudflare-Tunnel
  (wie er für den Home-Assistant-Zugriff selbst genutzt wird) kann TURN technisch
  nicht tragen (öffentliches UDP wird von Cloudflare Tunnel nicht unterstützt) -
  Cloudflares eigener, separat gehosteter TURN-Dienst schon.
- Dockerfile dadurch wieder deutlich schlanker (kein `coturn`-Systempaket mehr
  nötig).

## [0.2.0]

### Hinzugefügt
- **Anrufe im Dashboard annehmen und per Audio führen.** Neue WebRTC-Brücke
  (aiortc/PyAV, Audio-Transkodierung G.711 ↔ Opus) zwischen Browser
  (Mikrofon/Lautsprecher) und der Telefonie-Seite. Ein eingehender Anruf klingelt
  jetzt ca. 25 Sekunden lang (statt sofort dem `call_handling`-Standardverhalten zu
  folgen) - in diesem Fenster erscheint ein Live-Banner im Dashboard mit
  „Annehmen"/„Ablehnen"; nach dem Annehmen „Auflegen".
- Eingebauter TURN/STUN-Server (`coturn`) für die WebRTC-Verbindung - läuft als
  Subprozess im Add-on, Zugangsdaten werden automatisch generiert (`turn_username`/
  `turn_password`, Passwort optional manuell setzbar).
- Neue Option `turn_public_host` (öffentliche IP/DynDNS-Name) + Port-Range
  (`turn_relay_port_start`/`_end`, Standard `49160`-`49200`): mit Portfreigabe am
  Router funktioniert das Annehmen auch von unterwegs, nicht nur im LAN.
- Neue Option `enable_calling` (Standard `true`), um die Telefonie-Funktion bei
  Bedarf zu deaktivieren (dann nur noch reine Anruf-Erkennung wie in 0.1.x).
- SIP-Client (`sip_client.py`) versteht jetzt SDP-Angebote und kann eine eigene
  G.711-RTP-Session aufbauen (200 OK mit SDP-Antwort, BYE-Handling für Auflegen in
  beide Richtungen).

### Geändert
- `call_handling` wirkt jetzt erst nach Ablauf der Klingelzeit (~25s) als
  Standardverhalten, nicht mehr sofort beim Eingang des Anrufs - das gibt dem
  Dashboard ein echtes Zeitfenster zum Annehmen.

## [0.1.1]

### Behoben
- **Erfolgreiche SIP-Registrierung wurde nirgends geloggt.** Nur Fehlschläge landeten
  im Log, ein Erfolg blieb stumm - im Live-Test gegen eine echte UniFi-Talk-Console
  sah das nach einer hängenden Registrierung aus, obwohl sie sofort geklappt hatte.
  Jetzt loggt eine (erst-)erfolgreiche Registrierung explizit.
- Dashboard unterscheidet jetzt "nicht konfiguriert" von "Registrierung fehlgeschlagen"
  statt beides als "wird versucht ..." anzuzeigen.

## [0.1.0]

### Hinzugefügt
- Erste Version: SIP-Client registriert sich als zusätzliche, passive Extension bei
  UniFi Talk (über den Third-Party-Device-Workaround, siehe DOCS.md) und erkennt
  eingehende Anrufe (Anrufer-Nummer, Zeitpunkt).
- Ingress-Dashboard mit Registrierungsstatus, Anruf-Historie und eingebetteter
  Schritt-für-Schritt-Anleitung zur SIP-Einrichtung auf der UniFi-Console.
- Home-Assistant-Benachrichtigung bei eingehendem Anruf (`notify_on_call`, Standard
  `true`) - als `persistent_notification` sowie als Event `unifi_talk_incoming_call`
  (Felder `number`, `name`, `ts`) für eigene Automatisierungen.
- Konfigurierbares Anruf-Verhalten (`call_handling`): `log_only` (Standard - nimmt
  nichts an, andere Telefone/Apps klingeln normal weiter) oder `decline` (lehnt den
  Anruf nach dem Loggen aktiv ab).
- Reines Anruf-Erkennung, kein Audio: Das Add-on führt/beantwortet keine Gespräche -
  dafür wäre eine zusätzliche WebRTC-Bridge nötig (siehe README, geplanter nächster
  Schritt).
