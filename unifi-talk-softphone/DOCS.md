# UniFi Talk Softphone – Setup-Anleitung

## Warum dieser Umweg nötig ist

Ubiquiti hat den offiziellen Softphone-Modus von UniFi Talk (Telefonieren über
PC/Handy-App statt physisches Telefon) beim Deutschland-Release deaktiviert.
Es gibt außerdem aktuell keine offizielle API oder Webhooks für Anruf-Ereignisse.
Der einzige Weg, trotzdem an Anruf-Informationen (Anrufer-Nummer, Zeitpunkt) zu
kommen, führt über eine reguläre **SIP-Registrierung** – genau so, wie es auch
physische UniFi-Handsets tun.

> ⚠️ **Wichtig:** Dieses Vorgehen ist ein von der Community entdeckter,
> **inoffizieller Workaround**. Er nutzt eine interne SSH-Funktion der
> UniFi-Console (FreeSWITCH, das SIP-Backend hinter UniFi Talk) und wird von
> Ubiquiti nicht offiziell unterstützt. Er kann mit einem Firmware-Update
> jederzeit brechen. Dieses Add-on registriert sich standardmäßig **nur als
> zusätzlicher, passiver Zuhörer** – es nimmt keine Anrufe an und blockiert eure
> bestehenden Telefone/Apps nicht.

## Voraussetzungen

- Eine UniFi-OS-Console mit UniFi Talk (UDM-Pro, UDM-SE, Cloud Gateway o. ä.)
- SSH-Zugriff auf die Console aktiviert (UniFi OS → Einstellungen → Konsole →
  **SSH aktivieren**, je nach Firmware auch unter „System" → „Advanced")
- Ein bereits eingerichteter Talk-Service (Rufnummer)

## Schritt 1: Neue „Extension" für dieses Add-on anlegen

1. UniFi Talk → **Devices** (bzw. **Phones**) → **Set up device** / **+ Add Device**
2. Gerätetyp **„Third-Party Device"** wählen (kein echtes UniFi-Handset nötig)
3. Besteht der Dialog auf einem bestimmten UniFi-Gerät: mehrmals (ca. 10×) auf
   **„Setup Device(s)"** klicken – laut Community-Berichten lässt sich der
   Schritt so überspringen, ohne Hardware zu besitzen
4. Die zugewiesene **Extension** notieren (z. B. `0007`) – im Account-/
   Geräte-Überblick sichtbar

## Schritt 2: Extension zur bestehenden Klingelgruppe hinzufügen (empfohlen)

Damit eure bisherigen Telefone/die App weiterhin normal klingeln und Anrufe
annehmen können, fügt die neue Extension als **zusätzliches Mitglied** eurer
Service-Rufnummer/Ring-Group hinzu (Talk → Numbers → eure Nummer →
Gruppe/Mitglieder). Dieses Add-on nimmt Anrufe standardmäßig nicht an
(„nur loggen") – es klingelt nur „im Hintergrund" mit, ohne den anderen
Geräten den Anruf wegzunehmen.

## Schritt 3: SIP-Passwort auslesen (per SSH)

Mit der Console verbinden:

```
ssh root@<IP-eurer-Console>
```

Dann (`0007` durch eure Extension aus Schritt 1 ersetzen):

```
fs_cli -x "user_data 0007@talk.com param password"
```

Die Ausgabe ist das SIP-Passwort für diese Extension – notieren.

## Schritt 4: Werte im Add-on eintragen

| Add-on-Option   | Wert                                                  |
| --------------- | ------------------------------------------------------ |
| `talk_sip_host`  | Management-IP eurer Console (z. B. `192.168.1.1`)      |
| `talk_sip_port`  | `5060` (Standard, i. d. R. unverändert lassen)         |
| `sip_extension`  | Extension aus Schritt 1 (z. B. `0007`)                 |
| `sip_password`   | Passwort aus Schritt 3                                 |
| `sip_domain`     | `talk.com` (Standard, i. d. R. unverändert lassen)      |

Add-on **speichern und starten** – im Dashboard (Home-Assistant-Seitenleiste)
seht ihr danach den Registrierungsstatus sowie eintreffende Anrufe.

## Schritt 5: Telefonie (Annehmen mit Audio) – optional

Mit `enable_calling: true` (Standard) könnt ihr eingehende Anrufe direkt im
Dashboard **annehmen und per Mikrofon/Lautsprecher des Browsers führen** –
technisch über eine WebRTC-Brücke.

**Nur im selben WLAN/LAN wie der Add-on-Host:** funktioniert ohne weitere
Einrichtung – einfach im Dashboard auf **„Annehmen"** klicken, sobald es
klingelt (Browser fragt nach Mikrofon-Zugriff).

**Auch von unterwegs (z. B. nach einer Push-Benachrichtigung):** WebRTC
braucht dafür einen TURN-Relay-Server, der NAT/Firewalls überwindet. Statt
einen eigenen zu betreiben und Ports am Router freizugeben, nutzt dieses
Add-on **Cloudflares gehosteten TURN-Dienst** (Teil von „Cloudflare
Realtime", vormals „Calls") – ganz ohne Portfreigabe, da ihr ohnehin schon
Cloudflare (z. B. für den Tunnel zu Home Assistant) nutzt:

1. Im [Cloudflare Dashboard](https://dash.cloudflare.com/?to=/:account/calls)
   zu **Realtime** (bzw. „Calls") → **TURN** wechseln
2. Einen neuen **TURN Key** erstellen ("Create TURN Key")
3. Die beiden angezeigten Werte notieren: **Token ID** (bzw. „Key ID") und
   **API Token**
4. In der Add-on-Konfiguration eintragen: `cf_turn_key_id` = Token ID,
   `cf_turn_api_token` = API Token
5. Add-on neu starten

Ohne `cf_turn_key_id`/`cf_turn_api_token` funktioniert das Annehmen weiterhin,
dann aber nur im selben LAN wie der Add-on-Host.

> 💰 **Kosten:** Cloudflares TURN-Dienst ist kostenlos in Kombination mit
> Cloudflares Realtime-SFU, sonst mit nutzungsabhängiger Abrechnung
> (Stand der Cloudflare-Doku: 0,05 $ pro GB TURN-Traffic). Für gelegentliche
> Sprachanrufe (reines Audio, sehr geringe Bandbreite) fällt das kaum ins
> Gewicht.

## Anruf-Verhalten (`call_handling`)

Ein eingehender Anruf klingelt zunächst rund 25 Sekunden lang, in denen ihr
ihn im Dashboard annehmen könnt (siehe Schritt 5). Reagiert niemand, greift
danach:

- **`log_only`** (Standard): keine aktive Antwort außer „Ringing" – eure
  echten Telefone/Apps klingeln normal weiter und können den Anruf annehmen.
- **`decline`**: Anruf wird nach Ablauf der 25 Sekunden aktiv abgelehnt (486
  Busy). Sinnvoll, falls eure Ring-Group sonst auf alle Mitglieder wartet und
  sich dadurch verzögert.

## Bekannte Grenzen

- **„Annehmen"/„Anrufen" funktioniert nicht in der eingebetteten Ansicht der
  Home-Assistant-App.** Die App-WebView blockt auf iOS/Android den
  Mikrofon-Zugriff (`navigator.mediaDevices` fehlt) – das Dashboard dafür
  stattdessen direkt in Safari/Chrome öffnen (in Home Assistant einloggen,
  dann in der Seitenleiste auf „UniFi Talk" klicken).
- **Immer nur ein Anruf gleichzeitig** (angenommen oder gewählt). Ein zweiter
  eingehender Anruf während eines bereits laufenden wird wie gewohnt
  behandelt (klingelt, loggt), lässt sich aber erst annehmen, wenn der erste
  beendet ist.
- **Klingelt das Ziel beim Wählen länger, kann iOS Safari die Verbindung im
  Hintergrund/bei gesperrtem Bildschirm stillschweigend beenden** – der
  Anruf wird dann zwar auf SIP-Ebene angenommen, aber das Verbinden des
  Audios schlägt mit einer Fehlermeldung fehl. Bekannte iOS-Safari-
  Einschränkung bei WebRTC in Hintergrund-Tabs, kein Bug dieses Add-ons.
  Bildschirm/Seite während des Klingelns im Vordergrund lassen, dann klappt
  es normalerweise.
- **Telefonie von unterwegs braucht einen Cloudflare-TURN-Key** (siehe
  Schritt 5) – ohne `cf_turn_key_id`/`cf_turn_api_token` funktioniert
  Annehmen/Wählen nur im selben LAN wie der Add-on-Host.
- **Ausgehende Anrufe können laut mehreren Community-Berichten über so
  registrierte Drittanbieter-Geräte teils mit „486 Busy" fehlschlagen** -
  ein bekanntes Risiko dieses Workarounds, nicht etwas, das dieses Add-on
  softwareseitig umgehen kann. Tritt es auf, hilft meist nur ein Blick in
  die UniFi-Talk-Konfiguration der Extension (z. B. Klingelgruppen-
  Zuordnung) oder Geduld bei einem Firmware-Update.
- Der Workaround ist **inoffiziell** und kann jederzeit von Ubiquiti geändert
  oder entfernt werden.
