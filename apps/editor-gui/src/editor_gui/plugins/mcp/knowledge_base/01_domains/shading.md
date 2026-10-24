---
id: domain_shading
title: "Gewerk: Beschattungs- & Sonnenschutzsteuerung"
scope: "Ganzheitliche Sonnenschutzarchitektur in KNX: Jalousien (Raffstore mit Lamellenverstellung), Rollläden, Senkrechtmarkisen, automatische Fassadensteuerung und Sicherheits-Wetteralarme."
primary_functional_blocks:
  - fb_shutter_blind
subsystems_interfaces:
  - name: "SMI (Standard Motor Interface)"
    protocol: "SMI / SMI-LoVo (digitales Motor-Interface)"
    boundary_description: "KNX-SMI Aktor steuert SMI-Motoren digital an. Rückmeldung von exakter Ist-Position, Motorstörung und Hinderniserkennung auf KNX-Objekte."
  - name: "Wetterstation / Umwelt-Sensorik"
    protocol: "KNX nativ"
    boundary_description: "Wetterstation liefert zyklische Wind-, Regen- und Helligkeitswerte sowie Azimut und Elevation (Sonnenstandsberechnung) für dynamische Lamellennachführung."
---

# Beschattungs- & Sonnenschutzsteuerung (KNX Shading Domain)

## 1. Systemgrenzen & Komponenten
Die Sonnenschutzautomation unterteilt sich in drei operative Schichten:
1. **Sicherheitsebene (Höchste Priorität):**
   - Windalarm, Regenalarm, Frost-/Eisalarm, Brandschutz (Rauch-/Brandmelder).
   - Verriegelt Aktorkanäle gegen manuelle oder automatische Bedienung; fährt Behänge in die definierte Sicherheitslage (z. B. Jalousien ganz nach oben gegen Windbruch).
2. **Automatik- & Energieebene (Mittlere Priorität):**
   - Thermischer Sonnenschutz (sommerlicher Wärmeschutz nach DIN 4108-2) zur Vermeidung von Überhitzung und Reduktion von Kühllasten.
   - Solare Wärmegewinne im Winter (Behänge öffnen bei Sonnenschein, solange Heizanforderung besteht).
   - Blendschutz mit solarer Lamellennachführung nach Sonnenazimut und -elevation.
3. **Manuelle Bedienebene (Niedrigste Priorität):**
   - Taster, Visu und Raumbediengeräte.
   - Jede manuelle Bedienung kann die Automatik für eine definierte Zeitdauer sperren (Automatik-Reset nach Zeitablauf oder am nächsten Tag).

---

## 2. Kommunikationsparadigma: Kurz- / Langzeitbetrieb und absolute Position
- **Langzeit-Fahrbefehl (Auf/Ab):**
  - DPT `1.008` (`0` = Auf, `1` = Ab).
  - Taster sendet bei langem Tastendruck (z. B. > 400 ms).
- **Kurzzeit-Fahrbefehl (Lamellen-Schritt / Stopp):**
  - DPT `1.007` (`0` = Schritt Auf / Stopp, `1` = Schritt Ab / Stopp).
  - Taster sendet bei kurzem Tastendruck. Dient während der Fahrt als **Stopp-Befehl**, im Stillstand zur feinen Winkelverstellung.
- **Absolute Positionierung (Höhe & Lamelle):**
  - DPT `5.001` (Prozent: `0%` = ganz oben / Lamelle offen, `100%` = ganz unten / Lamelle geschlossen nach KNX-Konvention).
  - Für Szenen, Helligkeitsautomatik und Schieberegler in Visualisierungen.

---

## 3. Sicherheitsarchitektur & Zyklusüberwachung (Watchdog)
> [!CAUTION]
> **Sensorausfall ist ein Sicherheitsrisiko:**
> Wetterstationen müssen Wind- und Regenalarme zwingend **zyklisch senden** (z. B. alle 60–120 s).

- **Aktor-Watchdog:** Der Jalousieaktor überwacht das Eintreffen des Alarmtelegramms innerhalb eines Fensters (z. B. Überwachungszeit = 3x Sendezyklus, z. B. 6 Minuten).
- **Fehlerszenario (Busunterbrechung zur Wetterstation / Sensor defekt):** Bleibt das Telegramm aus, löst der Aktor autark den Alarm aus und fährt den Behang in die obere Sicherheitslage.
- **Windalarm-Verriegelung:** Solange ein Windalarm ansteht (Telegrammwert `1`), sind manuelle Fahrbefehle zwingend gesperrt. Nach Abklingen des Alarms (Telegrammwert `0`) greift eine Alarmverzögerung (z. B. 10–20 Minuten Windberuhigungszeit), um Flattern bei böigem Wind zu verhindern.

---

## 4. Fensterkontakt-Kopplung (Lüftungsfunktion & Aussperrschutz)
1. **Lüftungsfunktion (Kippkontakt):**
   - Beim Kippen des Fensters fährt der Rollladen nicht komplett hoch, sondern fährt auf eine definierte Lüftungsposition (z. B. 80% mit offenen Lichtschlitzen).
2. **Aussperrschutz (Drehkontakt bei Terrassen-/Balkontür):**
   - Ist die Terrassentür geöffnet (Kontakt offen), wird das Abwärtsfahren der Türbeschattung (insbesondere durch Zentralbefehle oder Dämmerungsautomatik) hardwarenah im Aktor gesperrt.

---

## 5. Busspannungsausfall & Busspannungswiederkehr
- **Busspannungsausfall:** Motoren stoppen sofort (Relais fallen ab).
- **Busspannungswiederkehr:**
  - Zur Vermeidung unzulässiger Einschaltströme und Lastspitzen fahren nicht alle Behänge synchron, sondern mit zeitlicher Staffelung.
  - Optionale Referenzfahrt: Bei Aktoren mit Laufzeitmessung wird zur exakten Positionsbestimmung eine Referenzfahrt ganz nach oben (0%) durchgeführt.
