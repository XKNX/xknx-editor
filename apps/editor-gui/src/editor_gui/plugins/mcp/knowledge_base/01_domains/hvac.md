---
id: domain_hvac
title: "Gewerk: Heizung, Lüftung, Kühlung & Raumklima (HVAC)"
scope: "Systemarchitektur für thermische Behaglichkeit und Raumautomation nach KNX Vol. 7/10 sowie DIN EN ISO 52120: Kette Messen -> Regeln -> Stellen, Betriebsartenumschaltung, sommerlicher Kühlbetrieb, Taupunktschutz und Notbetrieb."
primary_functional_blocks:
  - fb_room_thermostat
subsystems_interfaces:
  - name: "Wärmepumpen- & Kältemanager (SG Ready / Modbus)"
    protocol: "Modbus RTU / TCP oder potentialfreie Kontakte (SG Ready)"
    boundary_description: "KNX liefert Wärmeanforderung (höchste Stellgröße der Räume) an den Wärmeerzeuger. Die Wärmepumpe signalisiert Heiz-/Kühlbetrieb (Umschaltung DPT 1.100) und EVU-Sperren."
  - name: "Kontrollierte Wohnraumlüftung (KWL)"
    protocol: "KNX nativ oder Modbus RTU"
    boundary_description: "Lüftungsstufensteuerung (DPT 5.010 oder 5.001) gekoppelt an Raumluftqualität (CO2 / VOC DPT 9.008) und Feuchteschutz nach DIN 1946-6."
---

# HVAC & Raumtemperaturregelung (KNX HVAC Domain)

## 1. Die funktionale Kette: Messen -> Regeln -> Stellen

In der Gebäudeautomation mit KNX muss die Kette der Temperaturregelung strikt in drei logische Stufen unterteilt werden:

```text
[ 1. MESSEN ]               [ 2. REGELN ]                   [ 3. STELLEN ]
Temperatursensor ---------> Raumtemperaturregler (RTR) ----> Heizungsaktor
(Taster / Fühler)   Istwert        (PI-Algorithmus)     Stell-   (PWM-Relais /
                   (DPT 9.001)                         größe     Motorischer Stellantrieb)
                                                      (DPT 5.001
                                                       oder 1.001)
```

### 1.1 Wo sitzt der Regler?
In modernen Installationen gibt es zwei gängige Topologien:
1. **Regler im Raumbediengerät (Taster / Thermostat):**
   - Der Taster erfasst die Temperatur, führt den PI-Regler intern aus und sendet die Stellgröße (0–100%) an den Aktor im Heizkreisverteiler.
2. **Regler im Heizungsaktor (z. B. MDT AKH):**
   - Der Taster sendet lediglich die unmanipulierte Ist-Temperatur (`DPT 9.001`).
   - Der PI-Regler sitzt zentral im Aktor im Verteiler.
   - **Vorteil:** Hohe Zuverlässigkeit, da auch bei Ausfall eines Raumbediengerätes der Aktor über Sensorüberwachung (Watchdog) in ein geregeltes Notprogramm schalten kann.

---

## 2. Standardisierte KNX-Betriebsarten (DPT 20.102)

Nach KNX Spezifikation Vol. 7/10 erfolgt die Vorgabe des Betriebsmodus über das 1-Byte-Objekt `DPT 20.102` (`HVACMode`):

| Wert | Modus | Bedeutung & Verhalten |
|:---:|---|---|
| `1` | **Comfort** | Reguläre Solltemperatur bei Anwesenheit (z. B. 21,5 °C). |
| `2` | **Standby** | Leichte Absenkung bei kurzzeitiger Abwesenheit / Nichtnutzung (z. B. 19,5 °C). |
| `3` | **Economy** (Night) | Nachtabsenkung bzw. längere Abwesenheit (z. B. 17,5 °C). |
| `4` | **Building Protection** (Frost/Heat Protection) | Minimaler Sollwert (z. B. 7 °C Frostschutz) bei geöffnetem Fenster oder Gebäude-Leerstand. |

---

## 3. Sollwertverschiebung: Standards & Fallstricke
Zur Anpassung der Raumtemperatur durch den Nutzer existieren in der Praxis zwei Verfahren:
1. **DPT 9.002 (Kelvin-Offset) [Empfohlener Standard]:**
   - Sendet eine relative Verschiebung als Fließkommazahl in Kelvin (z. B. `+1.0 K` oder `-1.5 K`).
   - Universell kompatibel mit modernen Visualisierungen und Home Assistant.
2. **DPT 6.010 (1-Byte Integer Schritt-Zähler):**
   - Sendet Stufen (-128 bis +127), die im Aktor mit einer Schrittweite multipliziert werden (z. B. Stufe 2 bei Schrittweite 0,5 K = +1,0 K).
   - Älterer Standard; bei Neuprojekten ist DPT 9.002 vorzuziehen.

---

## 4. Sommerlicher Kühlbetrieb & Taupunktschutz
- **Heiz-/Kühlumschaltung (`DPT 1.100`):**
  - `1 = Heizen`, `0 = Kühlen`.
  - Bei Fußbodenkühlung invertiert sich die Regellogik (Istwert > Sollwert erfordert Öffnen der Ventile).
- **Taupunktüberwachung (Kondensatschutz):**
  - Unterschreitet die Vorlauftemperatur den Raumtaupunkt (berechnet aus Raumtemperatur und relativer Feuchte `DPT 9.007`), schlägt der Taupunktwächter an (`DPT 1.006`).
  - **Sicherheitsreaktion:** Heizungsaktor schließt die Kühlkreise sofort, um Bauschäden durch Kondenswasser auf dem Fußboden zu verhindern.

---

## 5. Fehlerszenarien & Sensorüberwachung
- **Sensorausfall:**
  - Der Regler überwacht das periodische Eintreffen des Raumtemperatur-Istwerts (z. B. Watchdog-Zeit 30 Minuten).
  - Bleibt der Messwert aus, schaltet der Kanal in den **Notbetrieb** (z. B. feste Stellgröße von 25% im Winter, um Einfrieren zu verhindern, bzw. 0% im Sommer).
