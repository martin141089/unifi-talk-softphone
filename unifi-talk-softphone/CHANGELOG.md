# Changelog

Alle nennenswerten Änderungen an diesem Add-on werden hier dokumentiert.

## [0.3.8] – Diagnose-Build

### Geändert
- **Experimentell:** Für Ziele mit mehr als 5 Zeichen (also keine 4-stellige
  interne Extension) verwendet `dial()` jetzt die Console-IP (`talk_sip_host`)
  statt `sip_domain` als Ziel-Domain im Request-URI. Hintergrund: mehrere
  Nummernformate für externe Rufnummern scheiterten alle sofort mit
  `404 Not Found`, während interne Extensions (4-stellig) unter derselben
  Domain zuverlässig routen - das deutet auf eine falsche Ziel-Domain im
  Request-URI hin, nicht auf das Nummernformat. Dieser Build testet die
  Hypothese; wird sie nicht bestätigt, wird die Änderung zurückgenommen.

## [0.3.7]

### Behoben
- **Bestätigt: Der SIP-Teil des Wählens funktioniert jetzt zuverlässig** (der
  0.3.6-Fix hat sich im Live-Test bewährt - ein einzelner Klick, `INVITE`
  wurde nach genau einer Challenge-Runde durchgestellt, `200 OK` kam nach
  ca. 36 Sekunden echtem Klingeln). Danach schlug das Verbinden von Audio
  aber mit `InvalidStateError: The object is in an invalid state.` fehl -
  Ursache ist eine bekannte iOS-Safari-Einschränkung: WebRTC-Verbindungen in
  einem länger im Hintergrund/bei gesperrtem Bildschirm liegenden Tab können
  von iOS selbst beendet werden, während `call/dial` noch auf die Antwort
  wartet (bis zu ~40s pro Auth-Runde). Statt der kryptischen Browser-
  Fehlermeldung erscheint jetzt eine verständliche Erklärung mit Hinweis,
  während des Klingelns Bildschirm/Seite im Vordergrund zu lassen - eine
  echte Behebung ist von einer Webseite aus nicht möglich (kein natives
  CallKit-Wecken), siehe DOCS.md.

## [0.3.6]

### Behoben
- **„500 Overlapping Requests" auch bei einem einzelnen, sauberen Anrufversuch
  (kein Doppel-Tap).** Ursache war eine verspaetete UDP-Retransmission der
  ERSTEN 407-Challenge (die Console schickt sie erneut, falls unser ACK sie
  nicht rechtzeitig erreicht) - unser Code wartete pro INVITE-Versuch nur auf
  "irgendeine Antwort mit CSeq: ... INVITE" fuer die Call-ID, ohne zu pruefen,
  ob die CSeq-*Nummer* zur gerade gestellten Anfrage passt. Die verspaetete
  Retransmission (identischer Nonce, alte CSeq) wurde dadurch faelschlich als
  Antwort auf die bereits authentifizierte Folge-Anfrage gewertet, was einen
  dritten, ueberlappenden INVITE-Versuch ausloeste. `_wait_invite_final()`
  verwirft jetzt Antworten mit unpassender CSeq-Nummer und wartet weiter auf
  die tatsaechliche Antwort. Mit einem gezielten Test nachgestellt (verspaetete
  Retransmission der ersten Challenge nach dem Senden der zweiten Anfrage) und
  bestaetigt: vorher fuehrte das zuverlaessig zu einem dritten INVITE, jetzt
  bleibt es bei den erwarteten zwei.

## [0.3.5]

### Behoben
- **Doppel-Tap auf „Anrufen" loeste zwei parallele Anrufversuche aus.** Die
  Console beantwortete das mit „500 Overlapping Requests" (bestaetigt der
  0.3.4-Fix: die Digest-Challenge wird jetzt sauber in einer Runde geloest -
  das eigentliche INVITE erreichte also durch, scheiterte aber an der
  Doppelanfrage). Zusaetzlich konnte die zweite Anfrage die globale
  WebRTC-Verbindung der ersten ueberschreiben, was zu
  `TypeError: null is not an object (evaluating 'pc.close')` fuehrte, sobald
  die erste Anfrage antwortete. „Anrufen"/„Annehmen" sind waehrend einer
  laufenden Anfrage jetzt deaktiviert, und jeder Versuch haelt seine eigene
  WebRTC-Verbindung in einer lokalen Variable statt nur der geteilten
  globalen - ein zweiter Versuch kann die erste Verbindung dadurch nicht mehr
  unbeabsichtigt kappen.
- Zusaetzliche Absicherung serverseitig: `dial()` blockiert jetzt auch einen
  zweiten, *gleichzeitig* gestarteten Aufruf (vorher schuetzten
  `active_call`/`ringing_call` nur vor einem zweiten Anruf, nachdem der erste
  bereits durch war - waehrend beide noch auf die INVITE-Antwort warteten,
  war das Fenster offen).

## [0.3.4]

### Behoben
- **Ausgehende Anrufe scheiterten an einer verschachtelten Digest-Challenge.**
  `dial()` beantwortete nur eine einzelne 401/407-Challenge und behandelte
  jede weitere Challenge als endgültige Ablehnung. UniFi Talk (FreeSWITCH)
  kann ein INVITE aber über mehrere interne Hops führen (z. B. Registrar-Realm
  und separat einen Trunk/Gateway-Realm), die jeweils eigenständig
  herausfordern - und vergisst dabei zusätzlich den bereits erfüllten
  Auth-Header des vorherigen Hops, wenn nur der neueste mitgeschickt wird.
  `dial()` sammelt jetzt alle bisher berechneten Auth-Header (nach
  Header-Name) und schickt sie gemeinsam bei jedem Retry mit, über bis zu
  3 Runden.
- Jede INVITE-Challenge wird jetzt mit vollem Header-Inhalt geloggt
  (Realm/Nonce/qop) - erleichtert die Diagnose, falls eine Challenge-Schleife
  trotzdem nicht aufgelöst werden kann (dann vermutlich ein echtes
  Berechtigungsproblem auf Console-Seite statt eines Auth-Bugs).

## [0.3.3]

### Behoben
- **„Anrufen" konnte über Mobilfunk (LTE) komplett wirkungslos bleiben, ohne
  jede Fehlermeldung.** `createLocalOffer()` wartete beim ICE-Gathering ohne
  Zeitlimit auf den Zustand "complete" - blockiert das UDP zum TURN-Server
  (z. B. durch manche Mobilfunknetze), erreicht dieser Zustand nie, und das
  Promise hing für immer (identisches Symptom wie der Bug aus 0.3.1: "Knopf
  ohne Funktion", nur mit anderer Ursache). Jetzt wird nach 3 Sekunden mit den
  bis dahin gesammelten ICE-Kandidaten weitergemacht statt endlos zu warten.

### Geändert
- Fehlschläge beim Annehmen/Anrufen (`RuntimeError` aus `sip_client.py`,
  z. B. "486 Busy" oder Zeitüberschreitung) werden jetzt zusätzlich als
  Server-Log-Zeile ausgegeben, nicht mehr nur als HTTP-Antwort an den Browser
  - erleichtert die Diagnose über die Add-on-Logs bei zukünftigen Problemen.

## [0.3.2]

### Behoben
- **Echter JavaScript-Syntaxfehler hat das komplette Dashboard-Skript
  unbrauchbar gemacht - dadurch reagierte KEIN Button mehr, nicht nur
  "Anrufen".** In der Mikrofon-Fehlermeldung (`checkMicSupport()`, seit
  0.2.2) stand ein falsch escaptes Anführungszeichen
  (`\"UniFi Talk\"` - das `\"` wurde von Python selbst als literales `"`
  ausgewertet statt als escaptes Zeichen fürs JavaScript weitergereicht),
  wodurch der JS-String vorzeitig endete und der Parser beim Laden der Seite
  abbrach. Mit `node --check` gegen die tatsächlich ausgelieferte Seite
  bestätigt und behoben (curly-quote-Zeichen `„…”` statt escaptem `"`
  verwendet - vermeidet die Escaping-Falle komplett).

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
