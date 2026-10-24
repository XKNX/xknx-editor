---
id: fb_room_thermostat
title: "Functional Block: Raumtemperaturregler & Heizungskanal (Room Thermostat Basic)"
category: hvac
standard_reference: "KNX Specifications Vol. 7/10, Kap. 3 & Kap. 4 (FB HVAC Room Controller)"
security_level: "Data Secure ready"
communication_objects:
  - id: temp_actual
    name: "Raumtemperatur Istwert"
    direction: write
    dpt: "9.001"
    flags: ["C", "W"]
    required: true
    cycle_time_recommended: "600s"
    description: "Gemessene Temperatur vom Raumfühler oder Glastaster in °C."
  - id: temp_setpoint_active
    name: "Aktueller Basis-Sollwert (Status)"
    direction: transmit
    dpt: "9.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Tatsächlich wirksamer Sollwert inklusive aller Offsets und Betriebsarten in °C."
  - id: setpoint_shift_cmd
    name: "Sollwertverschiebung (Befehl)"
    direction: write
    dpt: "9.002"
    flags: ["C", "W"]
    required: false
    description: "Relative Temperaturverschiebung in Kelvin (z. B. +1.5 K oder -1.0 K)."
  - id: setpoint_shift_state
    name: "Status Sollwertverschiebung"
    direction: transmit
    dpt: "9.002"
    flags: ["C", "R", "T"]
    required: false
    description: "Rückmeldung der aktuell aktiven Sollwertverschiebung in Kelvin."
  - id: hvac_mode_cmd
    name: "HVAC Betriebsmodus (Befehl)"
    direction: write
    dpt: "20.102"
    flags: ["C", "W"]
    required: true
    description: "Vorgabe der Betriebsart (1 = Comfort, 2 = Standby, 3 = Economy, 4 = Building Protection)."
  - id: hvac_mode_state
    name: "Status HVAC Betriebsmodus"
    direction: transmit
    dpt: "20.102"
    flags: ["C", "R", "T"]
    required: true
    description: "Rückmeldung der aktuell wirksamen Betriebsart."
  - id: control_value_heating
    name: "Stellgröße Heizen"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Berechneter Ausgangswert des PI-Reglers (0-100%) zur Ansteuerung des Ventils."
  - id: control_value_cooling
    name: "Stellgröße Kühlen"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: false
    description: "Stellgröße für Kühlbetrieb (0-100%)."
  - id: heat_cool_switch
    name: "Umschaltung Heizen/Kühlen"
    direction: write
    dpt: "1.100"
    flags: ["C", "W"]
    required: false
    description: "Umschaltung der Reglerlogik durch Wärmepumpe/Zentralsteuerung (1 = Heizen, 0 = Kühlen)."
  - id: window_status
    name: "Fensterkontakt (Frostschutz)"
    direction: write
    dpt: "1.019"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Schaltet den Regler sofort in Frostschutz (Building Protection) und stoppt Heizenergieverschwendung."
    description: "Fensterstatus (1 = Fenster geöffnet, 0 = geschlossen)."
  - id: dewpoint_alarm
    name: "Taupunktalarm"
    direction: write
    dpt: "1.006"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Schließt Kühlventile sofort zur Vermeidung von Bauschäden."
    description: "Signal von Feuchtefühler oder Taupunktberechnung (1 = Taupunkt unterschritten)."
recommended_ga_structure:
  - slot: 0
    function: "Raumtemperatur Istwert"
    dpt: "9.001"
  - slot: 1
    function: "Aktueller Sollwert (Status)"
    dpt: "9.001"
  - slot: 2
    function: "Sollwertverschiebung (Befehl)"
    dpt: "9.002"
  - slot: 3
    function: "Status Sollwertverschiebung"
    dpt: "9.002"
  - slot: 4
    function: "HVAC Modus (Befehl)"
    dpt: "20.102"
  - slot: 5
    function: "Status HVAC Modus"
    dpt: "20.102"
  - slot: 6
    function: "Stellgröße Heizen (0-100%)"
    dpt: "5.001"
  - slot: 7
    function: "Fensterstatus"
    dpt: "1.019"
  - slot: 8
    function: "Taupunktalarm"
    dpt: "1.006"
  - slot: 9
    function: "Umschaltung Heizen/Kühlen"
    dpt: "1.100"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. PI-Regelparameter für unterschiedliche Heizsysteme
Die Regelgüte hängt entscheidend von der thermischen Trägheit des Wärmeübertragers ab:
- **Fußbodenheizung (sehr träge):**
  - Proportionalbereich ($X_p$): `4,0 bis 5,0 K`
  - Nachstellzeit ($T_n$): `120 bis 240 Minuten`
  - Zykluszeit PWM: `15 bis 20 Minuten` (kürzere Zyklen schädigen thermoelektrische Antriebe ohne Regelgewinn).
- **Kompaktheizkörper / Radiatoren (schnell):**
  - Proportionalbereich ($X_p$): `1,5 bis 2,0 K`
  - Nachstellzeit ($T_n$): `30 bis 45 Minuten`
  - Zykluszeit PWM: `5 bis 10 Minuten` (oder stetiger Motorantrieb).

### 2. Fensterkontakt-Logik mit Öffnungsverzögerung
- Ein geöffnetes Fenster zwingt den Regler in den Modus `Building Protection` (`DPT 20.102 Wert 4`).
- **Verzögerungsparameter:** Um kurzes Lüften (Stoßlüften unter 1 Minute) oder Öffnen beim Hinaustreten nicht sofort als Abkühlung zu interpretieren, empfiehlt sich eine Ansprechverzögerung von **30–60 Sekunden** am Fensterkontakt-Eingang des Reglers.

### 3. Sensorüberwachung & Notbetrieb (Watchdog)
- Fällt der Raumfühler aus (z. B. Batterie leer bei Funk, Busunterbrechung), empfängt der Regler keinen Istwert mehr.
- **Watchdog-Zeit:** 30 bis 60 Minuten.
- **Notfall-Stellgröße:** Nach Ablauf der Zeit muss der Regler eine Not-Stellgröße (z. B. 25% bei Heizanforderung, 0% im Sommer) ausgeben, um Auskühlen oder Überhitzen zu verhindern.

### 4. Ventilschutzfunktion (Sommerpause)
- Thermoelektrische Ventile können nach monatelanger Inaktivität im Sommer verkalken oder festsitzen.
- **Ventilschutz-Zyklus:** Jeder Heizungskanal muss einmal pro Woche (z. B. jeden Mittwoch um 10:00 Uhr) das Ventil für 5 Minuten vollständig öffnen und wieder schließen.
