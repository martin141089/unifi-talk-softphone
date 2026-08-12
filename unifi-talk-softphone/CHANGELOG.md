# Changelog

Alle nennenswerten Änderungen an diesem Add-on werden hier dokumentiert.

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
