---
id: domain_energy_hems
title: "Gewerk: Energie- & Lastmanagement, PV, Speicher & Elektromobilität (HEMS)"
scope: "Systemarchitektur für ganzheitliches Home Energy Management (HEMS) in KNX: Erfassung von Erzeugung und Verbrauch, dynamische Einspeise- und Bezugssteuerung, Sektorkopplung (Wärmepumpe, Batteriespeicher, EV-Ladestationen nach ISO 15118) und netzdienliche Steuerung nach §14a EnWG."
primary_functional_blocks:
  - fb_energy_meter
subsystems_interfaces:
  - name: "Wechselrichter & Hybrid-Speichersysteme"
    protocol: "Modbus TCP / SunSpec oder KNX-Schnittstelle"
    boundary_description: "Erfassung von PV-Leistung, Batterie-Ladezustand (SoC) und Lade-/Entladeleistung. Begrenzung der Netzeinspeisung am Netzanschlusspunkt."
  - name: "Elektrofahrzeug-Ladestation (EVSE / Wallbox)"
    protocol: "OCPP 1.6J/2.0.1, Modbus TCP oder KNX nativ (ISO 15118)"
    boundary_description: "Lastmanagement-Interface: Dynamische Vorgabe des maximalen Ladestroms (6 A bis 32 A, 1-/3-Phasenumschaltung) basierend auf PV-Überschuss und Hausanschlusskapazität."
  - name: "Netzbetreiber-Schnittstelle (§14a EnWG)"
    protocol: "Potentialfreie Steuerbox-Kontakte oder digitales Protokoll (EEBUS / IEC 61850)"
    boundary_description: "Netzdienliche Dimmung steuerbarer Verbrauchseinrichtungen (SteuVE) auf max. 4,2 kW Netzwirksamkeit bei Netzengpässen."
---

# Energie- & Lastmanagement (KNX Energy & HEMS Domain)

## 1. Systemgrenzen & Architektur
Das Energiemanagement verbindet die klassische Gebäudeautomation mit der elektrischen Leistungsebene und externen Energiemärkten.

```text
[ Netzbetreiber / Markt ]   [ Photovoltaik & Speicher ]   [ Gebäude & Verbraucher ]
    (Dynamische Tarife,            (Wechselrichter,              (KNX Gebäudeautomation,
      §14a Steuerbox)             Batterie-Management)          Wärmepumpe, Wallbox)
            |                              |                             |
            +--------------> [ HEMS-Zentrale / KNX ] <-------------------+
                               (Optimierungslogik,
                                 Lastmanagement)
```

### Die Rolle von KNX im Energiemanagement:
- **Echtzeit-Kommunikation im Gebäude:** Sichere, deterministische Verteilung von Messgrößen (Wirkleistung, Zählerstände) und Steuerbefehlen ohne Cloud-Abhängigkeit.
- **Sektorkopplung:** Automatische Sollwertanhebung der Wärmepumpe (z. B. Warmwasserüberhöhung oder Pufferspeicher-Überladung) bei solarem Energieüberschuss.
- **Visualisierung & Abrechnung:** Aggregation von Energiezählerständen für Mieterstrommodelle oder interne Kostenstellen.

---

## 2. Netzbezug vs. Netzeinspeisung: Das Vorzeichen-Paradigma
> [!IMPORTANT]
> **Vorzeichenkonvention am Netzanschlusspunkt:**
> - $P_{\text{Netz}} > 0$: **Netzbezug** (Energie fließt aus dem öffentlichen Stromnetz in das Gebäude).
> - $P_{\text{Netz}} < 0$: **Netzeinspeisung** (Überschussenergie fließt in das Versorgungsnetz).
>
> Für eine fehlerfreie Saldierung und Abrechnung müssen Import- und Export-Energiezähler (`DPT 13.011` / `DPT 29.010`) getrennt als jeweils monoton steigende Zähler geführt werden.

---

## 3. Buslast-Management bei Energiemessungen
Im Gegensatz zu Tastern oder Lichtschaltungen generieren Energiezähler und Wechselrichter hochfrequente Datenströme. Um den KNX-Bus nicht zu überlasten:
1. **Schwellwertgeführtes Senden (Delta-Senden):**
   - Wirkleistung wird nur gesendet, wenn sich der Wert um mindestens $\pm 3–5\%$ oder einen Absolutbetrag (z. B. $\pm 100\text{ W}$) ändert.
2. **Mindestsendeabstand:**
   - Ein KO darf maximal alle 1–2 Sekunden senden.
3. **Zählerstände zyklisch:**
   - Kumulierte Energiezählerstände (Wh / kWh) ändern sich kontinuierlich; sie werden nicht bei jedem Wattstunden-Inkrement gesendet, sondern typisch alle **60 bis 300 Sekunden**.

---

## 4. Wallbox-Integration & Lastmanagement (§14a EnWG)
- **Mindestladestrom nach IEC 61851:** Ein Elektrofahrzeug benötigt mindestens **6 A je Phase** (ca. 1,4 kW einphasig, ca. 4,1 kW dreiphasig), um den Ladevorgang zu starten.
- **Phasenumschaltung:** Automatisches Umschalten zwischen 1-phasigem Laden (1,4 kW – 3,7 kW bei geringer PV-Erzeugung) und 3-phasigem Laden (4,1 kW – 11/22 kW bei starkem Sonnenschein).
- **Steuerbarkeit nach §14a EnWG:** Das System muss garantieren, dass bei Signal der Steuerbox die Netzbezugsleistung der SteuVE innerhalb von Sekunden auf maximal 4,2 kW gedrosselt wird (ohne den Eigenverbrauch aus PV oder Batterie einzuschränken).
