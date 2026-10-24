---
id: fb_shutter_blind
title: "Functional Block: Sonnenschutzaktor (Sunblind Actuator Basic)"
category: shading
standard_reference: "KNX Specifications Vol. 7/50, Kap. 2 (FB Sunblind Actuator Basic)"
security_level: "Data Secure ready"
communication_objects:
  - id: up_down_cmd
    name: "Auf/Ab Langzeitbetrieb (Befehl)"
    direction: write
    dpt: "1.008"
    flags: ["C", "W"]
    required: true
    description: "Fahrbefehl für vollständiges Auf- (0) oder Abfahren (1) des Behangs."
  - id: step_stop_cmd
    name: "Lamelle Kurzzeit / Stopp (Befehl)"
    direction: write
    dpt: "1.007"
    flags: ["C", "W"]
    required: true
    description: "Stoppt eine laufende Fahrt oder verstellt die Jalousielamellen schrittweise (0 = Auf/Stopp, 1 = Ab/Stopp)."
  - id: pos_height_cmd
    name: "Absolute Behanghöhe (Befehl)"
    direction: write
    dpt: "5.001"
    flags: ["C", "W"]
    required: true
    description: "Gibt die Zielhöhe in Prozent vor (0% = ganz oben, 100% = ganz unten nach KNX-Standard)."
  - id: pos_height_state
    name: "Status Behanghöhe (Rückmeldung)"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: true
    description: "Sendet die tatsächliche Behanghöhe nach Beendigung der Fahrt oder während der Fahrtzyklusüberwachung."
  - id: pos_slat_cmd
    name: "Absolute Lamellenposition (Befehl)"
    direction: write
    dpt: "5.001"
    flags: ["C", "W"]
    required: false
    description: "Gibt den Lamellenwinkel in Prozent vor (0% = voll geöffnet / horizontal, 100% = voll geschlossen)."
  - id: pos_slat_state
    name: "Status Lamellenposition (Rückmeldung)"
    direction: transmit
    dpt: "5.001"
    flags: ["C", "R", "T"]
    required: false
    description: "Sendet den tatsächlichen Lamellenwinkel nach Beendigung der Fahrt."
  - id: operating_state
    name: "Status Fahrt aktiv"
    direction: transmit
    dpt: "1.002"
    flags: ["C", "R", "T"]
    required: false
    description: "Meldet, ob der Motor aktuell in Bewegung ist (1 = Fahrt läuft, 0 = Stillstand)."
  - id: wind_alarm
    name: "Windalarm (Sicherheit)"
    direction: write
    dpt: "1.006"
    flags: ["C", "W"]
    required: true
    cycle_time_recommended: "60s"
    behavior_on_lock: "Fährt sofort in obere Endlage und verriegelt alle manuellen und automatischen Fahrbefehle."
    description: "Sicherheitsobjekt mit zyklischer Überwachung durch den Aktor (1 = Windalarm aktiv, 0 = Normalbetrieb)."
  - id: rain_alarm
    name: "Regenalarm (Sicherheit)"
    direction: write
    dpt: "1.006"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Fährt in die parametrierte Sicherheitslage (z. B. Markise einfahren)."
    description: "Schutz vor Durchfeuchtung oder mechanischer Überlastung von Stoffbehängen."
  - id: lock_object
    name: "Sperrobjekt / Aussperrschutz"
    direction: write
    dpt: "1.003"
    flags: ["C", "W"]
    required: false
    behavior_on_lock: "Unterbindet Abwärtsfahrten bei geöffneter Terrassentür."
    description: "Wird typischerweise mit Fenster-/Türkontakten verknüpft (1 = Sperre aktiv)."
recommended_ga_structure:
  - slot: 0
    function: "Auf / Ab (Langzeitbefehl)"
    dpt: "1.008"
  - slot: 1
    function: "Schritt / Stopp (Kurzzeitbefehl)"
    dpt: "1.007"
  - slot: 2
    function: "Absolute Höhe (Befehl)"
    dpt: "5.001"
  - slot: 3
    function: "Status Höhe (Rückmeldung)"
    dpt: "5.001"
  - slot: 4
    function: "Absolute Lamelle (Befehl)"
    dpt: "5.001"
  - slot: 5
    function: "Status Lamelle (Rückmeldung)"
    dpt: "5.001"
  - slot: 6
    function: "Status Fahrt aktiv"
    dpt: "1.002"
  - slot: 7
    function: "Sperren / Aussperrschutz"
    dpt: "1.003"
  - slot: 8
    function: "Windalarm (Sicherheit)"
    dpt: "1.006"
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. Trennung von Langzeit- und Kurzzeitbetrieb (Taster-Interface)
- **Langzeit (Auf/Ab):**
  - Bei langem Tastendruck (> 400 ms) wird der Behang vollständig in die Endlage verfahren oder solange bewegt, bis ein Stopp-Telegramm empfangen wird.
- **Kurzzeit (Lamelle/Stopp):**
  - Ein kurzer Tastendruck stoppt eine aktive Fahrt sofort.
  - Befindet sich der Motor im Stillstand, verstellt der Kurzzeitbefehl die Lamellen um einen definierten Zeitschritt (z. B. 100–200 ms).

### 2. KNX-Positionskonvention (DPT 5.001)
> [!IMPORTANT]
> **KNX Standardkonvention für Beschattung:**
> - **Höhe:** `0%` = Behang vollständig oben (offen/eingefahren), `100%` = Behang vollständig unten (geschlossen/ausgefahren).
> - **Lamelle:** `0%` = Lamellen horizontal / maximaler Lichteinfall, `100%` = Lamellen senkrecht geschlossen / vollständige Abdunkelung.
>
> Jede Abweichung hiervon in Visualisierungen oder Fremdsystemen bricht das standardisierte KNX-Zusammenspiel.

### 3. Sicherheitslogik: Zyklusüberwachung (Heartbeat / Watchdog)
- **Problem:** Fällt die Wetterstation (z. B. durch Blitzschlag, Netzteildefekt oder Busleitungsbruch) aus, während Sturm aufzieht, könnte ein Aktor ohne Heartbeat-Prüfung ungeschützt bleiben.
- **Lösung:**
  - Die Wetterstation sendet das Telegramm `wind_alarm` zyklisch (z. B. alle 60 s).
  - Der Aktor überwacht die Ankunft. Bleibt das Telegramm für länger als das Überwachungszeitfenster (z. B. 3 Minuten) aus, schlägt der Watchdog an: Der Aktor wertet dies als Sensorfehler und fährt den Behang autark in die sichere obere Endlage.
- **Windberuhigungszeit:**
  - Sobald der Windalarm von `1` auf `0` zurückgeht, muss der Aktor eine Wartezeit (typisch 10–15 Minuten) einhalten, bevor Automatik- oder manuelle Befehle wieder freigegeben werden. Dies schützt Getriebe und Motoren vor Zyklenbelastung durch Windböen.

### 4. Aussperrschutz (Terrassentürlogik)
- Ein Türkontakt (Magnetkontakt oder Drehgriffsensor) wird mit `lock_object` des Aktorkanals verknüpft.
- Ist die Tür geöffnet, verhindert der Aktor hardwarenah das automatische Schließen des Rollladens (z. B. durch Dämmerungsautomatik, Zeitschaltuhr oder Urlaubsfunktion), wenn sich Bewohner auf der Terrasse befinden.

### 5. Fahrzeitmessung & Wendelaufzeit
- Vor Inbetriebnahme muss die Gesamtfahrzeit (Auf und Ab separat, da Motoren aufwärts geringfügig langsamer laufen) exakt eingemessen werden (z. B. per Stoppuhr oder automatischer Strom-Laufzeitmessung moderner Aktoren).
- Zur Fahrzeit wird eine Zuschlagzeit (z. B. 5–10%) addiert, um sicheres Abschalten über den internen Motor-Endlagenschalter zu gewährleisten.
- Bei Jalousien muss die Lamellenwendezeit (typisch 0,8 s bis 1,5 s) präzise hinterlegt werden, da Positionsansteuerungen sonst Lamellenwinkel drastisch verfehlen.
