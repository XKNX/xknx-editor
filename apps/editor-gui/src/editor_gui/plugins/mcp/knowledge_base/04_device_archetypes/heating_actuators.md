---
id: dev_heating_actuator
title: "Geräte-Archetyp: Heizungsaktoren für thermoelektrische & motorische Antriebe"
device_class: actuator
implemented_functional_blocks:
  - fb_room_thermostat
key_parameters:
  - name: "Ventilantriebs-Charakteristik"
    default_or_recommended: "Stromlos geschlossen (NC - Normally Closed)"
    impact: "Sicherheitslogik bei Spannungsausfall; bestimmt, ob 0% oder 100% Bestromung erfordert."
  - name: "Regelverfahren (Schaltend PWM vs. Stetig Motorisch)"
    default_or_recommended: "Pulsweitenmodulation (PWM) mit 15-20 min Zykluszeit für Fußbodenheizung"
    impact: "Schont Halbleiterausgänge und thermische Wachs-Dehnstoffelemente; verhindert Überschwingen."
  - name: "Notprogramm bei Sensorausfall (Watchdog)"
    default_or_recommended: "Aktiv nach 30 Minuten ohne Temperatur-Telegramm (25% Heizleistung)"
    impact: "Verhindert Frostschäden im Winter oder unkontrollierte Raumüberhitzung."
  - name: "Automatischer Ventilschutz (Kalkschutz)"
    default_or_recommended: "Zyklisch alle 7 Tage für 5 Minuten Spülen/Öffnen"
    impact: "Verhindert Festsetzen der Ventilstößel während der langen Sommer-Heizpause."
---

# Geräte-Archetyp: Heizungsaktoren (z. B. MDT AKH, Theben, ABB)

## 1. Funktion & Rolle im System
Heizungsaktoren steuern thermoelektrische Ventilantriebe (230 V oder 24 V Dehnstoffelemente) an den Heizkreisverteilern von Fußbodenheizungen oder Radiatoren. Moderne Aktoren (wie die MDT AKH-Serie) besitzen integrierte PI-Temperaturregler für jeden Kanal.

---

## 2. Antriebstypen & Ansteuerungsarten

### 2.1 Thermoelektrische Stellantriebe (PWM)
- Funktionsweise: Ein PTC-Widerstand erwärmt ein Wachs-Dehnstoffelement, das gegen eine Feder das Ventil öffnet oder schließt.
- Trägheit: Benötigt 2 bis 4 Minuten bis zur vollständigen Hubänderung.
- Ansteuerung: **Pulsweitenmodulation (PWM)**:
  - Bei einer Stellgröße von 30% und einer Zykluszeit von 20 Minuten schaltet der Ausgang für 6 Minuten EIN und 14 Minuten AUS.
  - **Achtung:** Eine zu kurze Zykluszeit (< 10 min) führt zu keiner linearen Hubbewegung, sondern belastet lediglich das Heizelement unnötig.

### 2.2 Stetige motorische Antriebe (DPT 5.001)
- Funktionsweise: Ein kleiner Schrittmotor bewegt den Ventilstift präzise auf den Prozentwert (0–100%).
- Vorteil: Geräuschlos, extrem präzise und sofortige Reaktion; ideal für Radiatoren in Schlafzimmern.

---

## 3. Notprogramm & Sensorüberwachung
Bleibt der zyklische Istwert eines Raumfühlers für mehr als die konfigurierte Überwachungszeit (z. B. 30 Minuten) aus:
- Der Kanal schlägt Alarm (Diagnoseobjekt `DPT 1.006`).
- Der Ausgang wechselt in die Notlauf-Stellgröße (z. B. 20–30% PWM), um das Gebäude vor Frost zu schützen, ohne unnötig Heizenergie zu verheizen.
