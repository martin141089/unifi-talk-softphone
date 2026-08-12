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
technisch über eine WebRTC-Brücke (das Add-on bringt dafür einen eigenen
`coturn`-TURN-Server mit).

**Nur im selben WLAN/LAN wie der Add-on-Host:** funktioniert ohne weitere
Einrichtung – einfach im Dashboard auf **„Annehmen"** klicken, sobald es
klingelt (Browser fragt nach Mikrofon-Zugriff).

**Auch von unterwegs (z. B. nach einer Push-Benachrichtigung):** zusätzlich
nötig:

1. `turn_public_host` in der Add-on-Konfiguration auf eure öffentliche
   IP-Adresse oder einen DynDNS-Hostnamen setzen (denselben, über den ihr auch
   sonst von außen auf Home Assistant zugreift)
2. Am Router (UniFi-Gateway → Einstellungen → Internet/Firewall →
   **Portweiterleitung**) folgende Ports **UDP** auf die IP dieses
   Add-on-Hosts weiterleiten:
   - `3478` (TURN-Signalisierung)
   - `49160-49200` (Relay-Range, Standard – einstellbar über
     `turn_relay_port_start`/`turn_relay_port_end`)
3. Add-on neu starten

Die TURN-Zugangsdaten (`turn_username`/`turn_password`) müssen nicht manuell
gesetzt werden – ohne eigene Eingabe generiert das Add-on beim Start ein
zufälliges Passwort und verwendet es automatisch sowohl für den TURN-Server
als auch für das Dashboard.

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

- **Nur eingehende Anrufe annehmen, kein aktives Wählen.** Das Add-on kann
  (noch) nicht selbst Nummern anrufen – nur klingelnde Anrufe entgegennehmen.
- **Immer nur ein Anruf gleichzeitig.** Ein zweiter eingehender Anruf während
  eines bereits laufenden wird wie gewohnt behandelt (klingelt, loggt), lässt
  sich aber erst annehmen, wenn der erste beendet ist.
- **Telefonie von unterwegs braucht eine öffentliche Adresse + Portfreigabe**
  (siehe Schritt 5) – ohne `turn_public_host` funktioniert das Annehmen nur im
  selben LAN wie der Add-on-Host.
- **Ausgehende externe Anrufe** funktionieren über so registrierte
  Drittanbieter-Geräte laut mehreren Community-Berichten teils nicht
  zuverlässig (Fehler „486 Busy"). Für den reinen Empfang/Annehmen über dieses
  Add-on ist das irrelevant.
- Der Workaround ist **inoffiziell** und kann jederzeit von Ubiquiti geändert
  oder entfernt werden.
