# Open-KNX-KB: Offene KNX-Automations-Wissensbasis

Eine herstellerneutrale, semantisch präzise Wissensbasis für Gebäudeautomation mit KNX nach dem Grundsatz **„Standard vor Willkür“**.

---

## 1. Architektur & Verzeichnisstruktur

```text
open-knx-kb/
├── manifest.json               # Zentraler Index & DPT-Katalog für MCP-Server
├── schema/                     # JSON-Schema-Validatoren & Konsistenz-Prüfung
│   ├── function_block.schema.json
│   ├── domain.schema.json
│   ├── device_profile.schema.json
│   └── validate_kb.py
│
├── 01_domains/                 # Gewerke-Einstiege, Systemgrenzen & Paradigmen
│   ├── lighting.md             # Licht: Schalten, Dimmen, DALI-Grenzen, LED-C-Last
│   ├── shading.md              # Beschattung: Fahrzeiten, Lamelle, Sicherheitsalarme
│   ├── hvac.md                 # HVAC: Messen -> Regeln -> Stellen, Betriebsarten, Taupunkt
│   ├── energy_hems.md          # Energie: PV, Speicher, Wallbox (ISO 15118), §14a EnWG
│   └── safety_diagnostics.md   # Systemdiagnose: Buslast, Prioritätenkaskade, Watchdog
│
├── 02_functional_blocks/       # Herstellerneutrale Bausteine (KNX Vol. 7 & Interop-Profile)
│   ├── fb_switch_actuator.md   # FB LSAB: Schalten, Status, Treppenhaus DIN 18015-2
│   ├── fb_dimming_actuator.md  # FB Dimming Actuator Basic: Rel./Abs. Dimmen, Soft-Start
│   ├── fb_shutter_blind.md     # FB Sunblind Actuator Basic: Auf/Ab, Lamelle, Watchdog
│   ├── fb_room_thermostat.md   # FB Room Thermostat: Soll-/Istwert, HVAC-Modus, PI-Stellgröße
│   ├── fb_presence_detector.md # FB Presence Sensor: Voll-/Halbautomat, Master/Slave, Konstantlicht
│   ├── fb_weather_station.md   # FB Weather Station: Wind, Regen, Frost, Sonnenazimut/Elevation
│   └── fb_energy_meter.md      # FB Energy Meter: Wirkleistung, Zählerstände, HEMS-Lastmanagement
│
├── 03_addressing_patterns/     # Best-Practice Adressierungsmuster
│   ├── 3_level_standard.md     # 3-stufiger Standard (Gewerke / Orte / Dekaden-Slots)
│   ├── address_allocation.md   # GA-Bedarfsmatrix je Verbrauchertyp
│   └── migration_rules.md      # Regeln für unterbrechungsfreie Umbauten
│
└── 04_device_archetypes/       # Typische Hardware-Muster & Parametrierpfade
    ├── push_button_panels.md   # Touch- & Glastaster (MDT, GVS, Zennio)
    ├── heating_actuators.md    # Heizungsaktoren (PWM für NC-Ventile, Ventilschutz, Notlauf)
    └── dali_gateways.md        # DALI-2 Gateways (Gruppen vs. Einzel-EVG, DT6 vs. DT8, HCL, Notlicht)
```

---

## 2. Bereitstellung über MCP (Model Context Protocol)

Die Wissensbasis ist speziell dafür ausgelegt, von **MCP-Servern** als strukturierte Wissensquelle ausgeliefert zu werden:
1. **Maschinenlesbarer Index (`manifest.json`):**
   - Direkte Suche nach Datapoints (DPT), KOs, Flags, Funktionsblöcken und Gewerken.
2. **Reines Markdown mit YAML-Frontmatter (`.md`):**
   - Plattform- und sprachenunabhängig konsumierbar (keine proprietäre Code-Bindung).
3. **Deterministische Qualität:**
   - Alle KOs trennen strikt zwischen Befehl (`C, W`) und Status (`C, R, T`).
   - Keine erfundenen Datapoint-Types (DPT-Prüfung gegen offizielle KNX-Norm Vol. 3/7/2).
