---
id: domain_safety_diagnostics
title: "Gewerk: Sicherheits- & Systemdiagnose, Busüberwachung & Alarmierung"
scope: "Systemarchitektur für funktionale Sicherheit, Buslast-Monitoring, Alarmkaskaden (Wind, Brand, Wasser, Einbruch), Watchdog-Überwachung und strukturierte Diagnose im KNX-Netzwerk."
primary_functional_blocks:
  - fb_weather_station
subsystems_interfaces:
  - name: "Gefahrenmeldeanlage / Brandmeldeanlage (BMA / EMA)"
    protocol: "Potentialfreie VdS-Relais oder zertifiziertes KNX-Sicherheits-Interface"
    boundary_description: "Höchste Systempriorität: Brandmelder öffnen sofort alle Fluchtwege (Rollläden/Jalousien 100% AUF) und schalten Fluchtbeleuchtung auf 100% Dauerlicht."
  - name: "Bus-Diagnose & Schnittstellen (IP Secure / TP Coupler)"
    protocol: "KNX IP Secure & cEMI"
    boundary_description: "Überwachung der Busspannung, Telegrammraten je Sekunde, Resends und Telegrammverluste zur präventiven Störungserkennung."
---

# Sicherheits- & Systemdiagnose (KNX Safety Domain)

## 1. Prioritätskaskade für Notfall- und Sicherheitsbefehle

In der KNX-Gebäudeautomation gilt eine unumstößliche Prioritäten-Hierarchie:

```text
[ Priorität 1: Personenschutz & Brandfall ]
  (BMA-Alarm, Rauchmelder: Fluchtwege öffnen, Licht AN, Lüftung AUS)
        |
[ Priorität 2: Gebäudeschutz & Extremwetter ]
  (Windalarm, Hagelalarm, Frostschutz: Behänge in Schutzposition verriegeln)
        |
[ Priorität 3: Sachschutz & Wasserschaden ]
  (Wassermelder: Hauptventil schließen, Pumpen abschalten)
        |
[ Priorität 4: Technische Alarme & Überwachung ]
  (Sensor-Watchdog, Ausfall von Heizungsfühlern, Filterwechsel KWL)
        |
[ Priorität 5: Regel- & Automatikbetrieb ]
  (Konstantlichtregelung, Sonnennachführung, Einzelraumregelung)
        |
[ Priorität 6: Manuelle Bedienung ]
  (Taster, Visualisierung, App)
```

---

## 2. Buslast-Diagnose & Telegramm-Raten
- **Bandbreitengrenze von TP1 (Twisted Pair):**
  - Die physikalische Übertragungsrate auf KNX TP1 beträgt 9.600 Baud.
  - Dies entspricht einer maximalen Übertragungskapazität von etwa **40 bis 50 Telegrammen pro Sekunde**.
- **Kritische Buslast:**
  - Eine dauerhafte Buslast von über **30 % (ca. 15 Telegramme/s)** deutet auf Fehlkonfigurationen hin (z. B. ungedrosselte Leistungsmesser oder Telegrammschleifen).
  - Spitzenlasten (z. B. nach Spannungsausfall) dürfen durch gestaffelte Sendeverzögerungen nicht zum Pufferüberlauf führen.

---

## 3. Das Watchdog- & Heartbeat-Konzept (Eigensicherheit)
Alle sicherheitsrelevanten Komponenten müssen nach dem **Fail-Safe-Prinzip** arbeiten:
- Nicht das Vorhandensein eines Fehlertelegramms löst die Notfallreaktion aus, sondern das **Ausbleiben des periodischen Lebenszeichens**.
- Typische Heartbeat-Intervalle:
  - Windsensorik / Wetterstation: 60 s (Überwachungszeit im Aktor: 180 s)
  - Raumtemperaturfühler: 300–600 s (Überwachungszeit im Aktor: 30 min)
  - Systemuhr / NTP-Zeitserver: 1 Stunde (Überwachungszeit: 24 Stunden)
