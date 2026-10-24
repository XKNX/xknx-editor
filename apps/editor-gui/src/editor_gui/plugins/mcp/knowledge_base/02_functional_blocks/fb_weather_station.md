---
id: fb_weather_station
title: "Functional Block: Wetterstation & Umwelt-Sensorik (Weather Station Basic)"
category: sensors
standard_reference: "KNX Specifications Vol. 7/01/06 Weather Data & Vol. 7/50/01 Shutters Sensors"
security_level: "Data Secure ready"
communication_objects:
  - id: wind_speed
    name: "Windgeschwindigkeit"
    direction: transmit
    dpt: "9.005"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "10-30 s"
    description: "Gemessene Windgeschwindigkeit in m/s (oder km/h nach DPT 9.028)."
  - id: wind_alarm
    name: "Windalarm (Sicherheit)"
    direction: transmit
    dpt: "1.006"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "60s"
    description: "Zyklisches Sicherheitsobjekt bei Überschreitung des Windschwellwerts (1 = Alarm / Sturm, 0 = Normalbetrieb)."
  - id: rain_alarm
    name: "Regenalarm (Sicherheit)"
    direction: transmit
    dpt: "1.006"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "60s"
    description: "Signal des beheizten Regensensors (1 = Regen erkannt, 0 = trocken)."
  - id: frost_alarm
    name: "Frostalarm (Eisschutz)"
    direction: transmit
    dpt: "1.006"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "300s"
    description: "Schutz vor Festfrieren von Behanglamellen bei Temperatur < 3 °C und Feuchte (1 = Frostgefahr)."
  - id: temp_outdoor
    name: "Außentemperatur"
    direction: transmit
    dpt: "9.001"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "300s"
    description: "Außentemperaturmessung in °C für Heizkurvenanpassung und Kühlfreigabe."
  - id: brightness_east
    name: "Helligkeit Fassade Ost"
    direction: transmit
    dpt: "9.004"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60s"
    description: "Helligkeitswert in Lux für sonnenstandsabhängige Beschattungsautomatik."
  - id: brightness_south
    name: "Helligkeit Fassade Süd"
    direction: transmit
    dpt: "9.004"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60s"
    description: "Helligkeitswert in Lux für Südfassade."
  - id: brightness_west
    name: "Helligkeit Fassade West"
    direction: transmit
    dpt: "9.004"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60s"
    description: "Helligkeitswert in Lux für Westfassade."
  - id: twilight_value
    name: "Dämmerungswert"
    direction: transmit
    dpt: "9.004"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60s"
    description: "Feiner Messwert im Bereich 0-1000 Lux für Kunstlicht- und Rollladendämmerungssteuerung."
  - id: sun_azimuth
    name: "Sonnenazimut (Horizontalwinkel)"
    direction: transmit
    dpt: "14.007"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "180s"
    description: "Berechneter Sonnenwinkel in Grad (0° = Nord, 90° = Ost, 180° = Süd, 270° = West)."
  - id: sun_elevation
    name: "Sonnenelevation (Höhenwinkel)"
    direction: transmit
    dpt: "14.007"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "180s"
    description: "Berechneter Höhenwinkel der Sonne über dem Horizont in Grad (-90° bis +90°)."
  - id: day_night_state
    name: "Tag / Nacht Status"
    direction: transmit
    dpt: "1.001"
    flags: ["C", "R", "T"]
    required: false
    description: "Dämmerungs- und zeitgeführtes Master-Objekt (1 = Tag, 0 = Nacht)."
recommended_ga_structure:
  - slot: 0
    function: "Windalarm (zyklisch 60s)"
    dpt: "1.006"
  - slot: 1
    function: "Regenalarm (zyklisch 60s)"
    dpt: "1.006"
  - slot: 2
    function: "Frostalarm"
    dpt: "1.006"
  - slot: 3
    function: "Außentemperatur"
    dpt: "9.001"
  - slot: 4
    function: "Dämmerungswert (Lux)"
    dpt: "9.004"
  - slot: 5
    function: "Tag / Nacht Status"
    dpt: "1.001"
  - slot: 6
    function: "Helligkeit Fassade Ost"
    dpt: "9.004"
  - slot: 7
    function: "Helligkeit Fassade Süd"
    dpt: "9.004"
  - slot: 8
    function: "Helligkeit Fassade West"
    dpt: "9.004"
  - slot: 9
    function: "Sonnenazimut"
    dpt: "14.007"
  - slot: 10
    function: "Sonnenelevation"
    dpt: "14.007"
  - slot: 11
    function: "Windgeschwindigkeit"
    dpt: "9.005"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. Zyklisches Senden von Sicherheitsalarmen
> [!CAUTION]
> Wind- und Regenalarme müssen zwingend **zyklisch gesendet** werden (z. B. alle 60 Sekunden), niemals nur bei Statusänderung!
> Die empfangenden Jalousieaktoren besitzen Watchdog-Filter: Bleibt das Signal länger als das projektierte Überwachungsfenster aus, müssen sie aus Eigensicherheitsgründen in die obere Endlage fahren.

### 2. Beheizte Regensensorik
- Regensensoren arbeiten resistiv oder optisch. Um Taubildung am frühen Morgen nicht als Regen fehlzuinterpretieren und nach Regenende schnell abzutrocknen, muss die Sensorfläche **temperaturgeregelt beheizt** sein.

### 3. Fassadenbezogene Beschattung & Lamellennachführung
- Statt alle Fenster gleichzeitig herabzufahren, nutzt die Fassadenautomatik getrennte Lux-Sensoren oder die Sonnenstandsberechnung (Azimut/Elevation):
  - **Ostfassade:** Aktiv am Vormittag (z. B. Azimut 60° bis 160°).
  - **Südfassade:** Aktiv um die Mittagszeit (z. B. Azimut 120° bis 240°).
  - **Westfassade:** Aktiv am Nachmittag/Abend (z. B. Azimut 200° bis 300°).
- **Hysteresen:** Um ständiges Auf- und Abfahren an wolkigen Tagen zu verhindern, müssen Einschaltverzögerungen (z. B. 3–5 Minuten kontinuierlich über Schwellwert) und Ausschaltverzögerungen (z. B. 15–20 Minuten unter Schwellwert) parametriert werden.
