# Changelog

Alle nennenswerten Änderungen an diesem Add-on werden hier dokumentiert.

## [0.5.1]

### Behoben
- **Externes Wählen live bestätigt, aber ohne Audio in beide Richtungen** -
  trotz erfolgreicher SIP-Signalisierung und erfolgreichem ICE (Browser ↔
  Add-on über TURN-Relay) blieb die Verbindung stumm. Ursache: Die Console
  meldet in ihrer SDP-Antwort für das RTP-Ziel teils eine andere IP (z. B.
  ihre öffentliche WAN-IP) als `talk_sip_host`, obwohl die Extension
  ausschließlich im LAN erreichbar ist - ohne NAT-Hairpinning am Router kam
  dadurch kein RTP an, obwohl die Signalisierung völlig normal lief (exakt
  das Muster aus dem Support-File-Fund zur nativen Rufweiterleitung).
  `answer_ringing_call()` und `dial()` verwenden für das RTP-Ziel jetzt
  immer `self.host` (`talk_sip_host`, nachweislich erreichbar - dort läuft
  bereits die SIP-Signalisierung) statt der von der Console gemeldeten
  SDP-IP; nur der Port kommt weiterhin aus der SDP. Mit gezielten Tests
  gegen einen simulierten SIP-Server nachgestellt (SDP meldet bewusst eine
  abweichende IP) und für beide Richtungen (Annehmen und Wählen) bestätigt.

### Bestätigt
- **Externe (PSTN-)Rufnummern lassen sich über den SIP-Extension-Workaround
  doch anrufen** - die frühere Diagnose "architektonisch blockiert" (0.3.9,
  0.4.0) war unvollständig: Es liegt nicht an SIP-INVITE-Routing generell,
  sondern an einer **Extension-spezifischen Berechtigung**. Mit Extension
  `0003` scheiterten alle Formate/Domains weiterhin mit `404 Not Found`,
  während ein Live-Test mit dem tatsächlichen INVITE eines funktionierenden
  Referenzclients (Groundwire, Extension `0007`, Format/Domain 1:1
  nachgebaut) sofort durchging (`200 OK`). Direkter Beweis per Umkonfiguration
  auf Extension `0007`: derselbe Anruf, der mit `0003` durchgehend `404`
  ergab, ging mit `0007` sofort durch (nur noch am Test-SDP gescheitert,
  nicht mehr an der Console). Ursache der unterschiedlichen Berechtigung
  zwischen Extensions bleibt außerhalb dieses Add-ons (UniFi-Talk-seitige
  Konfiguration) und ungeklärt.

## [0.5.0]

### Hinzugefügt
- **Telefonie-Feature (Annehmen & Wählen) wiederhergestellt** (in 0.4.0
  entfernt). Hintergrund: mit TalkAnchor (separates Projekt, hält die von
  UniFi Talk intern gespeicherte öffentliche IP bei dynamischer IP aktuell)
  ist die wahrscheinliche Ursache der zuvor beobachteten Audio-Aussetzer
  behoben - eine falsche öffentliche IP in der SDP erklärt das Muster aus
  dem Support-File (Anruf signalisiert "answered"/"bridged", aber kein Ton).
  `webrtc_bridge.py`, RTP/SDP-Handling, `answer_ringing_call()`, `dial()`,
  `hangup_active_call()` in `sip_client.py` sowie die WebRTC-Endpunkte und
  Cloudflare-TURN-Integration in `run.py` sind identisch zum Stand vor 0.4.0
  wiederhergestellt. **Wichtig:** Das ändert nichts an der separat
  bestätigten architektonischen Grenze für externe (PSTN-)Rufnummern (siehe
  0.3.9) - die bleibt bestehen, UniFi Talk routet externe Ziele weiterhin nur
  über eine interne Anwendungslogik, nicht per SIP-INVITE. Wählen
  funktioniert daher nur zu internen Extensions/Third-Party-Geräten.

### Behoben
- **Mikrofon-Zugriff in der Home-Assistant-App fälschlich als pauschal
  nicht unterstützt gemeldet.** Die bisherige Meldung ("die App-WebView
  stellt kein navigator.mediaDevices bereit") war eine Fehldiagnose: sowohl
  die iOS- als auch die Android-Companion-App unterstützen Mikrofonzugriff
  in Ingress-WebViews seit App-Version 2020.8 - Voraussetzung ist lediglich
  ein sicherer Kontext (`window.isSecureContext`, also HTTPS-Zugriff auf
  Home Assistant). Die Prüfung wurde entsprechend korrigiert.
- **Anruftabelle nicht an schmale Bildschirme angepasst.** Responsive
  CSS/Viewport-Meta-Fix, damit die Anruf-Historie auf mobilen Bildschirmen
  (insb. in der HA-App) nicht mehr über den Rand hinausläuft.
