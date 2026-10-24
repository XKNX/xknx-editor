# Migrationsregeln: Sichere GA- und Parameter-Umbauten

## 1. Grundregeln für unterbrechungsfreie Umbauten
Bei Umbauten im laufenden KNX-Betrieb (insbesondere in bewohnten Gebäuden oder im gewerblichen Betrieb) können unbedachte Programmiervorgänge zu Ausfällen, Busüberlastung oder unkontrollierten Relaiszuständen führen.

### Grundregel 1: Erst hören, dann senden (Read-Flags vor Link-Tausch)
- Bevor eine Gruppenadresse an einem Kommunikationsobjekt getauscht wird, muss geprüft werden, ob andere Busteilnehmer (z. B. Home Assistant, Taster-LEDs) auf diesen Status angewiesen sind.
- Neue Statusadressen müssen **zuerst am Aktor angelegt und programmiert** werden, bevor Bediengeräte auf die neue Adresse umgestellt werden.

### Grundregel 2: Das Flag-Diktat (Keine unbeabsichtigten Read-Responses)
- Mehrere KO auf dieselbe Gruppenadresse:
  - Wenn mehrere KOs einer Statusadresse zugeordnet werden (z. B. Sammelstatus), darf **maximal ein einziges KO das R-Flag (Read)** gesetzt haben!
  - Haben zwei KOs auf derselben Adresse das R-Flag gesetzt, antworten beide gleichzeitig auf einen Bus-Read-Request. Auf Twisted Pair führt dies zu Telegrammkollisionen, Bitfehlern und Bus-Resends.

---

## 2. Vermeidung von Buslastspitzen (Telegrammfluten)

### 2.1 Sendeverzögerung nach Buswiederkehr
- **Gefahr:** Nach einem partiellen Stromausfall oder Neustart der Hauptlinie melden 50 bis 200 Aktorkanäle gleichzeitig ihren Status. Dies führt zu 100% Buslast, Paketverlusten und verzögerter Tasterreaktion.
- **Lösung:**
  - In allen Aktoren muss die **Sendeverzögerung für Status-Telegramme** nach Busspannungswiederkehr auf mindestens **2 bis 5 Sekunden** gestellt werden.
  - Geräte mit vielen Kanälen (z. B. 16-fach oder 24-fach Schaltaktoren) müssen die interne Telegrammrate begrenzen (z. B. maximal 3 bis 5 Telegramme pro Sekunde).

### 2.2 Zyklisches Senden begrenzen
- Sensoren (Temperatur, Feuchte, CO2, Helligkeit, Energie) sollten primär **bei Wertänderung** senden (z. B. Temperatur bei ±0,2 K Änderung, Leistung bei ±5% oder ±50 W).
- Ein zyklischer Heartbeat dient lediglich als Überwachung und darf nicht kürzer als 10–15 Minuten gewählt werden, sofern es sich nicht um sicherheitsrelevante Windwächter handelt.

---

## 3. ETS-Download-Sicherheit & Partielle Downloads
1. **Teil-Download statt Komplett-Download:**
   - Bei reinen GA-Verknüpfungsänderungen oder leichten Parameteranpassungen immer den **partiellen Download (Applikationsprogramm / Adressen & Parameter)** nutzen. Ein vollständiger Geräteneustart (mit Relais-Klackern) wird dadurch vermieden.
2. **Priorisierung von Linienkopplern:**
   - Vor dem Verschieben von Geräten in andere Linien oder Ändern von Hauptgruppen müssen die **Filtertabellen der Linienkoppler** aktualisiert werden. Andernfalls filtert der Koppler die Telegramme der neuen Adressen weg.
