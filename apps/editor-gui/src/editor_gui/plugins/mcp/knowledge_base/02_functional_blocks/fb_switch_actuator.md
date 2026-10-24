---
id: fb_switch_actuator
title: "Functional Block: Schaltaktor (Light Switching Actuator Basic - LSAB)"
category: lighting
standard_reference: "KNX Specifications Vol. 7/20, Kap. 2 (FB LSAB)"
security_level: "Data Secure ready"
communication_objects:
  - id: switch_cmd
    name: "Ein/Aus Schalten (Befehl)"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: true
    description: "Empfängt Schaltbefehle von Tastern, Präsenzmeldern oder Steuerungen (1 = Ein, 0 = Aus)."
  - id: switch_state
    name: "Status Ein/Aus (Rückmeldung)"
    direction: transmit
    dpt: "1.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Sendet den tatsächlichen Schaltzustand des Relais aktiv auf den Bus und antwortet auf Read-Requests."
  - id: timed_staircase
    name: "Treppenhausfunktion / Zeitgesteuertes Schalten"
    direction: write
    dpt: "1.010"
    flags: ["C", "W"]
    required: false
    description: "Startet oder stoppt den Treppenhaus-Timer (1 = Start/Nachlauftrigger, 0 = Vorzeitiger Abbruch)."
  - id: lock_object
    name: "Sperrobjekt"
    direction: write
    dpt: "1.003"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Zustand halten, Ausschalten, Einschalten oder keine Reaktion auf Schaltbefehle"
    description: "Sperrt die Bedienung des Kanals bei anstehendem Signal (1 = Sperre aktiv, 0 = Freigabe)."
  - id: forced_control
    name: "Zwangsführung (2-Bit)"
    direction: write
    dpt: "2.001"
    flags: ["C", "W"]
    required: false
    description: "Übersteuerndes Schalten mit Priorität (Bit 1 = Freigabe/Zwang, Bit 0 = Zustand Ein/Aus)."
  - id: scene_control
    name: "Szenenaufruf / Speichern (8-Bit)"
    direction: write
    dpt: "18.001"
    flags: ["C", "W"]
    required: false
    description: "Ruft gespeicherte Relaiszustände für bis zu 64 Szenen ab oder speichert sie."
recommended_ga_structure:
  - slot: 0
    function: "Schalten (Befehl)"
    dpt: "1.001"
  - slot: 1
    function: "Status Schalten (Rückmeldung)"
    dpt: "1.001"
  - slot: 2
    function: "Treppenhaus / Zeitschaltung"
    dpt: "1.010"
  - slot: 3
    function: "Sperren / Zwangsführung"
    dpt: "1.003"
  - slot: 4
    function: "Szene"
    dpt: "18.001"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. Trennung von Befehl und Status (Hörer-Sender-Prinzip)
- **Befehlsobjekt (`slot: 0` / KO `switch_cmd`):**
  - Empfängt Telegramme von allen Bedien- und Automationsstellen (Taster, Bewegungsmelder, Touchpanel, Home Assistant).
  - Flags: `[C, W]`. Der Aktor sendet niemals selbst auf dieser Adresse.
- **Statusobjekt (`slot: 1` / KO `switch_state`):**
  - Sendet aktiv (`T`) bei jeder Zustandsänderung (auch ausgelöst durch Handbedienung am Gerät oder Zeitschaltprogramme).
  - Antwortet auf Abfragen (`R`) von Visualisierungen oder Gateways nach einem Neustart.
  - Flags: `[C, R, T]`.
- **Verbot:** Befehl und Status dürfen niemals auf dieselbe Gruppenadresse gelegt werden. Bei Kopplung von Ein- und Ausgang auf eine GA entstehen Endlosschleifen und inkonsistente Rückmelde-LEDs.

### 2. Treppenhausfunktion (Staircase Function nach DIN 18015-2)
- Bei Ansteuerung über `timed_staircase` schaltet das Relais für die eingestellte Zeitspanne (z. B. 180 s) ein.
- **Nachschalten (Pumpen):** Jedes erneute Empfangen einer `1` verlängert die Einschaltdauer ab aktuellem Zeitpunkt.
- **Ausschaltvorwarnung:** Vor dem Abschalten warnt der Aktor durch kurzes Aus-/Eintasten (Flackern) oder Helligkeitsabsenkung nach DIN 18015-2, damit Personen im Treppenhaus nicht unvorbereitet im Dunkeln stehen.

### 3. Lastarten & Relaisspezifikation (C-Last)
- Moderne LED-Treiber und Schaltnetzteile erzeugen extrem hohe kapazitive Einschaltströme (Inrush Currents) von bis zu 800 A für wenige Mikrosekunden.
- Es müssen Schaltaktoren mit **C-Last-Eignung** (vorzugsweise mit Vorlaufkontakt aus Wolfram/AgSnO2 oder Nulldurchgangsschaltung) eingesetzt werden, um Kontaktschweißen dauerhaft zu verhindern.

### 4. Fehlerszenarien & Systemverhalten
- **Busspannungsausfall:**
  - Bistabile Relais verharren stromlos in ihrer aktuellen Lage (kein Energieverbrauch).
  - Sicherheitsrelevante Kreise (Notbeleuchtung) können per Parameter auf "Zwangs-EIN bei Busausfall" konfiguriert werden.
- **Busspannungswiederkehr:**
  - Standardverhalten: Zustand vor Busausfall wiederherstellen oder definierter Ausschaltzustand.
  - Das Senden des Status-Telegramms muss über eine **Einschaltverzögerung** (z. B. 2–5 s nach Buswiederkehr) abgefangen werden, um den KNX-Bus bei großen Liegenschaften nicht mit Status-Stürmen zu blockieren.
