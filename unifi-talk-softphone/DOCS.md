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

## Anruf-Verhalten (`call_handling`)

Ein eingehender Anruf wird sofort erkannt und geloggt (Anrufer-Nummer, Name
falls übermittelt, Zeitpunkt) – dieses Add-on nimmt keine Anrufe an und führt
kein Audio, es beobachtet nur die SIP-Signalisierung:

- **`log_only`** (Standard): keine aktive Antwort auf das INVITE – eure
  echten Telefone/Apps klingeln normal weiter und können den Anruf annehmen.
- **`decline`**: Anruf wird sofort nach dem Loggen aktiv abgelehnt (486
  Busy). Sinnvoll, falls eure Ring-Group sonst auf alle Mitglieder wartet und
  sich dadurch verzögert.

## Bekannte Grenzen

- **Reine Anruferkennung, kein Audio.** Das Add-on führt/beantwortet keine
  Gespräche – weder eingehend noch ausgehend. Ein früherer Versuch, über
  eine WebRTC-Brücke im Dashboard Anrufe anzunehmen und selbst zu wählen,
  wurde wieder entfernt: Ausgehende Anrufe zu externen (PSTN-)Nummern lassen
  sich über diesen SIP-Extension-Workaround architektonisch nicht erreichen
  (UniFi Talk routet externe Ziele ausschließlich über eine interne
  Anwendungslogik, nicht über ein regulär per SIP-INVITE erreichbares
  Dialplan-Ziel), und mit dieser Grenze war der Aufwand einer eigenen
  Audio-Bridge nicht mehr gerechtfertigt.
- Der Workaround ist **inoffiziell** und kann jederzeit von Ubiquiti geändert
  oder entfernt werden.
