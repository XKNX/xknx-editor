---
id: fb_presence_detector
title: "Functional Block: Präsenz- & Bewegungsmelder (Presence & Light Sensor Basic)"
category: sensors
standard_reference: "KNX Specifications Vol. 7/20, Kap. 1 & Vol. 7/01, Kap. 2"
security_level: "Data Secure ready"
communication_objects:
  - id: presence_output
    name: "Präsenz / Schaltausgang Licht"
    direction: transmit
    dpt: "1.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Sendet 1 bei erkannter Bewegung/Präsenz und 0 nach Ablauf der Nachlaufzeit."
  - id: brightness_value
    name: "Gemessener Helligkeitswert"
    direction: transmit
    dpt: "9.004"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "300s"
    description: "Aktuelle Umgebungshelligkeit in Lux (sendet zyklisch und bei prozentualer Helligkeitsänderung)."
  - id: slave_input
    name: "Slave-Eingang (Kaskadierung)"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: false
    description: "Empfängt Trigger-Impulse von Slave-Meldern zur Flächenerweiterung."
  - id: external_motion
    name: "Externer Bewegungsimpuls"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: false
    description: "Triggert die Nachlaufzeit durch externe Sensoren (z. B. Tasterdruck)."
  - id: day_night_switch
    name: "Tag / Nacht Umschaltung"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: false
    description: "Schaltet zwischen Tagbetrieb (z. B. 100% Licht, 300 Lux Schwelle) und Nachtbetrieb (z. B. Orientierungslicht 15%, 50 Lux Schwelle)."
  - id: lock_object
    name: "Sperrobjekt"
    direction: write
    dpt: "1.003"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Schaltausgang deaktivieren, Zustand halten oder Licht dauerhaft EIN/AUS"
    description: "Sperrt die Erfassung (z. B. bei manueller Lichtsperre oder TV-Modus)."
  - id: constant_light_dimvalue
    name: "Konstantlichtregelung Stellwert"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: false
    description: "Gibt den berechneten Dimmwert (0-100%) an den Dimmaktor aus, um konstante Helligkeit zu halten."
recommended_ga_structure:
  - slot: 0
    function: "Präsenz / Schalten Licht (Ausgang)"
    dpt: "1.001"
  - slot: 1
    function: "Helligkeitswert (Lux)"
    dpt: "9.004"
  - slot: 2
    function: "Slave-Trigger (Master/Slave)"
    dpt: "1.001"
  - slot: 3
    function: "Tag/Nacht Umschaltung"
    dpt: "1.001"
  - slot: 4
    function: "Sperren Präsenzmelder"
    dpt: "1.003"
  - slot: 5
    function: "Konstantlicht Dimmwert (0-100%)"
    dpt: "5.001"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. Präsenzmelder vs. Bewegungsmelder (Vollautomat vs. Halbautomat)
- **Vollautomat:**
  - Schaltet automatisch EIN bei Bewegung und unzureichendem Umgebungslicht (< Einschaltschwelle).
  - Schaltet automatisch AUS nach Verlassen des Raumes und Ablauf der Nachlaufzeit.
- **Halbautomat (Büro-Best-Practice):**
  - Einschalten erfolgt **ausschließlich manuell per Taster**.
  - Der Melder hält das Licht aktiv, solange gearbeitet wird, und schaltet nach Verlassen des Raumes automatisch ab.
  - Vorteil: Höchste Energieeffizienz und Vermeidung von Fehlschaltungen durch vorbeigehende Personen.

### 2. Master/Slave-Kaskadierung für große Räume
In L-förmigen Fluren, offenen Wohnbereichen oder Büros reicht ein Sensor nicht aus:
- **Slave-Melder:**
  - Reine Erfassung (Helligkeit meist deaktiviert).
  - Sendet bei Bewegung einen kurzen Impuls (`1`) im Zyklus (z. B. alle 3–5 Sekunden) auf die Master/Slave-GA.
- **Master-Melder:**
  - Empfängt die Impulse auf `slave_input`.
  - Führt die Helligkeitsmessung, Nachlaufzeitberechnung und die Aktor-Schaltbefehle zentral aus.
- **Wichtig:** Slaves dürfen niemals direkt auf den Schaltaktor wirken!

### 3. Tag- und Nachtmodus (Blendfreies Orientierungslicht)
- Über `day_night_switch` (`DPT 1.001`) wird zwischen zwei Regelsätzen umgeschaltet:
  - **Tag:** Volle Helligkeit (100%), Nachlaufzeit z. B. 3 Minuten.
  - **Nacht:** Gedimmtes Orientierungslicht (z. B. 10–15%), kürzere Nachlaufzeit (z. B. 60 Sekunden), reduzierte Helligkeitsschwelle.

### 4. Konstantlichtregelung (DIN EN 15232)
- Der Melder misst kontinuierlich das Mischlicht (Tageslicht + Kunstlicht) auf der Arbeitsfläche.
- Der interne Regler passt den Dimmwert des Dimmaktors dynamisch an, um den gewünschten Sollwert (z. B. 500 Lux nach ASR A3.4) exakt zu halten. Bei starker Sonneneinstrahlung schaltet der Regler die Kunstlichtquelle komplett ab.
