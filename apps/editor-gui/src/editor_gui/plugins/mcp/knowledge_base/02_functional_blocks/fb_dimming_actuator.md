---
id: fb_dimming_actuator
title: "Functional Block: Dimmaktor (Dimming Actuator Basic)"
category: lighting
standard_reference: "KNX Specifications Vol. 7/20, Kap. 3 (FB Dimming Actuator Basic)"
security_level: "Data Secure ready"
communication_objects:
  - id: switch_cmd
    name: "Ein/Aus Schalten"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: true
    description: "Schaltet den Kanal ein oder aus (1 = Einschalten auf Memory-Wert oder vordefinierten Wert, 0 = Ausschalten)."
  - id: switch_state
    name: "Status Ein/Aus"
    direction: transmit
    dpt: "1.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Rückmeldung des tatsächlichen Schaltzustands (1 = Leuchte leuchtet mit > 0%, 0 = Ausgeschaltet)."
  - id: dim_relative
    name: "Relatives Dimmen (4-Bit)"
    direction: write
    dpt: "3.007"
    flags: ["C", "W"]
    required: false
    description: "4-Bit Dimmschritt für Tasterbedienung (Start Heller/Dunkler bei Tastendruck, Stopp bei Loslassen)."
  - id: dim_absolute
    name: "Absoluter Dimmwert (8-Bit)"
    direction: write
    dpt: "5.001"
    flags: ["C", "W"]
    required: true
    description: "Prozentwert (0-100%) für Szenen, Schieberegler in Visualisierungen oder Tageslichtnachführung."
  - id: value_state
    name: "Status Dimmwert"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Rückmeldung des aktuellen Helligkeitswertes in Prozent nach Erreichen des Zielwertes."
  - id: lock_object
    name: "Sperrobjekt"
    direction: write
    dpt: "1.003"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Zustand halten, Ausschalten oder festen Dimmwert anfahren"
    description: "Sperrt die Bedienung des Dimmkanals bei anstehendem Signal."
  - id: scene_control
    name: "Szenenaufruf / Speichern (8-Bit)"
    direction: write
    dpt: "18.001"
    flags: ["C", "W"]
    required: false
    description: "Ruft gespeicherte Helligkeitswerte für bis zu 64 Szenen ab oder speichert den aktuellen Wert."
recommended_ga_structure:
  - slot: 0
    function: "Schalten (Befehl)"
    dpt: "1.001"
  - slot: 1
    function: "Status Schalten (Rückmeldung)"
    dpt: "1.001"
  - slot: 2
    function: "Relatives Dimmen (4-Bit)"
    dpt: "3.007"
  - slot: 3
    function: "Absoluter Dimmwert (Befehl)"
    dpt: "5.001"
  - slot: 4
    function: "Status Dimmwert (Rückmeldung)"
    dpt: "5.001"
  - slot: 5
    function: "Sperren / Zwangsführung"
    dpt: "1.003"
  - slot: 6
    function: "Szene"
    dpt: "18.001"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. Trennung von Befehl und Status (Hörer-Sender-Prinzip)
- Ein Taster sendet seinen Tastendruck auf die **Befehls-GA** (`slot: 0` für Schalten, `slot: 2` für relatives Dimmen, `slot: 3` für absolute Helligkeitsvorgaben).
- Die LED-Rückmeldung des Tasters und Visualisierungs-Widgets lauschen auf die **Status-GAs** (`slot: 1` für Schalten, `slot: 4` für Prozentwert).
- **Verbot:** Befehl und Status dürfen niemals auf dieselbe Gruppenadresse gelegt werden (Gefahr von Telegrammschleifen und inkonsistenten Zuständen).

### 2. Dimmverhalten & Dimmkurven
- **Soft-Start & Dimmgeschwindigkeit:**
  - Plötzliche Sprünge belasten LED-Vorschaltgeräte und wirken unnatürlich.
  - Empfehlung: Dimmzeit für relative Dimmstufen auf 4–6 s (von 0 bis 100%), Anspringzeit bei Ein/Aus auf 1–2 s (Soft-Start/Soft-Off).
- **Logarithmische vs. lineare Dimmkurve:**
  - Das menschliche Auge empfindet Helligkeit logarithmisch (Weber-Fechner-Gesetz). Bei 20% elektrischer Leistung nimmt das Auge ca. 50% der maximalen Helligkeit wahr.
  - Phasen- und PWM-Aktoren müssen auf **logarithmische Dimmkurve** parametriert werden, um eine visuell gleichmäßige Helligkeitsänderung über den gesamten Stellbereich zu erzielen.
- **Minimaler Dimmwert:**
  - Bei Phasenabschnittsdimmern für Retrofit-LEDs muss die Mindesthelligkeit (meist zwischen 5% und 15%) so eingemessen werden, dass kein Flackern auftritt und die Leuchtmittel beim Einschalten sicher zünden.

### 3. Nachtmodus & tageszeitabhängiges Verhalten
- Bei aktivem Nachtobjekt (z. B. gesteuert über Systemuhr oder Dämmerungssensor) schaltet ein Tasterdruck den Dimmkanal nicht auf den Maximalwert, sondern auf einen blendfreien Nachtwert (z. B. 15% im Flur/Bad).

### 4. Fehlerszenarien & Systemverhalten
- **Überlast- und Kurzschlusserkennung:**
  - Moderne Universal-Dimmaktoren schalten bei thermischer Überlastung oder Kurzschluss die Halbleiterendstufe sofort ab und melden diesen Status über ein separates Diagnose-KO an den Bus.
- **Busspannungsausfall:**
  - Dimmaktor trennt den Lastkreis oder verharrt im aktuellen Zustand.
- **Busspannungswiederkehr:**
  - Der Aktor darf keine plötzlichen Einschaltströme erzeugen. Kanäle schalten entweder auf "Aus" oder auf den vor dem Ausfall gespeicherten Zustand.
  - Das Senden der Statusobjekte (`value_state`, `switch_state`) erfolgt zeitverzögert zur Schonung der Busbandbreite.
