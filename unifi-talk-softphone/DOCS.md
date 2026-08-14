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
- Für Telefonie von außerhalb des LAN: Home Assistant muss über **HTTPS**
  erreichbar sein (z. B. über einen Cloudflare-Tunnel oder Nabu Casa) – dazu
  mehr unter „Bekannte Grenzen" unten.

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

## Schritt 5: Telefonie (Annehmen & Wählen mit Audio) – optional

Mit `enable_calling: true` (Standard) könnt ihr eingehende Anrufe direkt im
Dashboard **annehmen** und selbst **wählen** – per Mikrofon/Lautsprecher des
Browsers, technisch über eine WebRTC-Brücke. Das funktioniert auch in der
Home-Assistant-App, sofern Home Assistant über HTTPS aufgerufen wird (siehe
„Bekannte Grenzen").

**Wählen (ausgehende Anrufe):** Interne Extensions/andere Third-Party-Geräte
lassen sich zuverlässig anrufen. **Externe Rufnummern (normale Telefonanschlüsse)
lassen sich über diesen Workaround nicht anrufen** – UniFi Talk routet externe
Ziele nachweislich nur über eine interne Anwendungslogik, nicht über ein
regulär per SIP-INVITE erreichbares Dialplan-Ziel. Das „Anrufen"-Feld bleibt
trotzdem nützlich für interne Nummern.

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

- **Mikrofon-Zugriff braucht eine sichere Verbindung (HTTPS).** `getUserMedia()`
  ist eine Browser-Vorgabe für alle Browser (auch die eingebettete Ansicht der
  Home-Assistant-App, iOS und Android) und funktioniert nur über HTTPS oder
  `localhost` – nicht über eine lokale `http://`-IP-Adresse. Ruft Home
  Assistant über eine unverschlüsselte lokale Adresse auf, fehlt
  `navigator.mediaDevices` komplett, unabhängig davon, welche App/welcher
  Browser genutzt wird. Abhilfe: Home Assistant über die normale HTTPS-Adresse
  öffnen (Cloudflare-Tunnel, Nabu Casa, o. ä.) statt über eine lokale
  http://-IP. Die früher hier dokumentierte Aussage „funktioniert generell
  nicht in der Home-Assistant-App" war eine Fehldiagnose – moderne
  iOS-/Android-Companion-Apps unterstützen Mikrofonzugriff in eingebetteten
  Ingress-Panels grundsätzlich.
- **Externe Rufnummern lassen sich nicht anrufen** (siehe Schritt 5) – nur
  interne Extensions/Third-Party-Geräte. Architektonische Grenze des
  Workarounds, keine Konfigurationsfrage.
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
- Der Workaround ist **inoffiziell** und kann jederzeit von Ubiquiti geändert
  oder entfernt werden.
