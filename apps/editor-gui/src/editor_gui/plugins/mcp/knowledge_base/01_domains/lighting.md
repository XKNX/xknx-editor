---
id: domain_lighting
title: "Gewerk: Beleuchtungssteuerung & DALI-Integration"
scope: "Ganzheitliche Beleuchtungsarchitektur in KNX: Schalten, Phasenan-/-abschnittdimmen, LED-Treiber (CC/CV), DALI/DALI-2 Gateways und HCL (Human Centric Lighting)."
primary_functional_blocks:
  - fb_switch_actuator
  - fb_dimming_actuator
subsystems_interfaces:
  - name: "DALI / DALI-2 (IEC 62386)"
    protocol: "DALI Broadcast, Group & Single Device Adressierung"
    boundary_description: "DALI-Gateway agiert als Protokollübersetzer. KNX sendet Befehle/Werte an Gruppenobjekte; das Gateway steuert das EVG und spiegelt DALI-Zustands- und Fehlertelegramme (Lampen-/EVG-Ausfall) auf KNX-Statusobjekte zurück."
  - name: "DMX512-A / Art-Net"
    protocol: "DMX / RS-485"
    boundary_description: "KNX-DMX Gateway für Effekt- und Bühnenbeleuchtung (Architekturlicht). Direkte 1-Byte Farbkanäle oder RGB/RGBW DPTs werden in High-Speed DMX-Universen gemappt."
---

# Beleuchtungssteuerung (KNX Lighting Domain)

## 1. Systemgrenzen & Architektur
In modernen KNX-Installationen teilt sich die Beleuchtung in drei Hauptkategorien auf:
1. **Reine KNX-Aktoren (Schaltaktoren, Universaldimmaktoren, KNX-LED-Controller):**
   - Direkte Ansteuerung von Lasten über Relais oder integrierte PWM-Endstufen (12-48 V DC CC/CV).
   - Vorteil: Keine Gateway-Latenzen, volle Transparenz auf dem KNX-Bus, native Unterstützung von KNX Data Secure.
2. **DALI-2 Gateways (KNX zu IEC 62386):**
   - Steuerung von DALI-Betriebsgeräten (DT6 für Standarddimmen, DT8 für Tunable White und RGBW).
   - Systemgrenze: Das DALI-Gateway verwaltet die DALI-Kurzadresse und DALI-Gruppen. KNX steuert primär über **DALI-Gruppen** (Broadcast oder Gruppen 0–15), um Telegrammüberlastung auf dem 1200-Baud-DALI-Bus zu verhindern. Einzel-EVG-Steuerung ist Diagnose- und Notfunktionen vorbehalten.
3. **Sensorik (Präsenz- & Bewegungsmelder, Taster):**
   - Taster senden ausschließlich Steuerimpulse (Schalten, Relatives Dimmen, Szenenaufruf).
   - Präsenzmelder regeln autark (Konstantlichtregelung) oder schalten präsenz- und helligkeitsabhängig.

---

## 2. Kommunikationsparadigma: Trennung von Befehl und Status
> [!IMPORTANT]
> **Das Hörer-Sender-Prinzip (Command-Status Separation):**
> Befehlsobjekte (Input) und Statusobjekte (Output) dürfen **niemals** auf dieselbe Gruppenadresse verknüpft werden.

- **Befehls-GA (Write):** Alle Bediengeräte (Taster, Visu, PM, Logik) senden ihr Steuersignal auf die Befehls-GA. Der Aktor empfängt auf dieser Adresse (`Flags: C, W`).
- **Status-GA (Transmit):** Ausschließlich der Aktor sendet bei Zustandsänderung oder nach interner Abarbeitung auf der Status-GA (`Flags: C, R, T`).
- **Hörer:** Status-LEDs an Tastern und Visu-Elemente hören passiv auf die Status-GA. Dies verhindert Schleifen, Fehltrigger und inkonsistente Anzeigen.

---

## 3. Typische Steuerungsarten & DPTs
| Funktion | Befehl (DPT) | Flags (Aktor) | Status (DPT) | Flags (Aktor) | Bemerkung |
|---|---|---|---|---|---|
| Schalten | `1.001` (DPT_Switch) | C, W | `1.001` (DPT_Switch) | C, R, T | Ein (1) / Aus (0) |
| Relatives Dimmen | `3.007` (DPT_Control_Dimming) | C, W | - | - | 4-Bit Schrittdimmen (Start/Stop) |
| Absolutwert Dimmen | `5.001` (DPT_Scaling) | C, W | `5.001` (DPT_Scaling) | C, R, T | 0-100% Helligkeit |
| Farbtemperatur (TW) | `7.600` (DPT_Absolute_Colour_Temperature) | C, W | `7.600` | C, R, T | Kelvin (z. B. 2700K - 6500K) |
| RGBW Farbe | `251.600` (DPT_Colour_RGBW) | C, W | `251.600` | C, R, T | 6-Byte kombiniertes Farbobjekt |

---

## 4. Dimmverhalten & LED-Schonung
- **Soft-Start / Soft-Aus:** Dimmkurven sollten logarithmisch (entsprechend der menschlichen Augenempfindlichkeit nach Weber-Fechner) mit Dimmzeiten zwischen 0,8 s und 2,0 s parametriert werden.
- **Nachtbetrieb (Nachtlichtfunktion):** Bei aktiviertem Nachtobjekt schaltet der Aktor nicht mit 100%, sondern mit einem reduzierten Einschaltwert (z. B. 15-20%) ein, um Blendung zu vermeiden.
- **Treppenlichtfunktion:** Zeitgesteuertes Ausschalten mit Vorwarnung (kurzes Absenken auf 50% vor dem Abschalten nach DIN 18015-2).

---

## 5. Fehlerszenarien & Busspannungsausfall
- **Busspannungsausfall:** Relais behalten ihren Zustand (bistabile Relais) oder schalten in einen definierten Sicherheitszustand (z. B. Fluchtwege EIN).
- **Busspannungswiederkehr:** Statusobjekte senden nicht alle gleichzeitig auf den Bus, sondern werden mit einer konfigurierbaren Sendeverzögerung (z. B. 2–10 s) gestaffelt versendet, um Buslastspitzen zu vermeiden.
- **DALI-Busausfall (System Failure Level):** EVGs schalten bei Verlust der DALI-Busspannung automatisch auf 100% (Notbetrieb).
