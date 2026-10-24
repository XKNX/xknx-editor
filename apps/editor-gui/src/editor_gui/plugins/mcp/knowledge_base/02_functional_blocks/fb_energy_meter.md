---
id: fb_energy_meter
title: "Functional Block: Energiemessung & Netzeinspeisung (KNX Energy Management)"
category: energy_hems
standard_reference: "KNX Specifications Vol. 3/7/2 Datapoint Types v02.02.01 AS; Vol. 7/60/01 Metering M-Bus Data Collector v01.04.08 AS; Vol. 7/11/05 HWH LoadManagement v01.01.01 AS"
security_level: "Data Secure ready"
status: "open-knx-kb-profile"
normative_status: "vendor_neutral_profile_not_an_official_knx_functional_block"
units:
  active_power: "W"
  active_energy: "Wh"
  current: "A"
  frequency: "Hz"
communication_objects:
  - id: grid_active_power
    name: "Netz: Wirkleistung am Netzanschlusspunkt"
    direction: transmit
    dpt: "14.057"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "1-5 s; ereignisbasiert bei relevanter Änderung"
    description: "Vorzeichenbehaftete Wirkleistung am Netzanschlusspunkt. Die Zuordnung von Vorzeichen und Leistungsflussrichtung ist installationsweit festzulegen."
  - id: grid_import_power
    name: "Netzbezug: Wirkleistung"
    direction: transmit
    dpt: "14.057"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "1-5 s"
    description: "Nichtnegative Wirkleistung des Netzbezugs. Import wird unabhängig von der Einspeisung erfasst."
  - id: grid_export_power
    name: "Netzeinspeisung: Wirkleistung"
    direction: transmit
    dpt: "14.057"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "1-5 s"
    description: "Nichtnegative Wirkleistung der Netzeinspeisung. Export wird unabhängig vom Netzbezug erfasst."
  - id: grid_import_energy
    name: "Netzbezug: Wirkenergiezählerstand"
    direction: transmit
    dpt: "13.011"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "60-300 s; zusätzlich bei relevanter Zählerstandsänderung"
    description: "Kumulierte Wirkenergie des Netzbezugs in Wh gemäß DPT ActiveEnergy. Nicht mit Einspeiseenergie verrechnen."
  - id: grid_export_energy
    name: "Netzeinspeisung: Wirkenergiezählerstand"
    direction: transmit
    dpt: "13.011"
    flags: ["C", "R", "T"]
    required: true
    cycle_time_recommended: "60-300 s; zusätzlich bei relevanter Zählerstandsänderung"
    description: "Kumulierte Wirkenergie der Netzeinspeisung in Wh gemäß DPT ActiveEnergy. Separater monotoner Zählerstand."
  - id: phase_a_current
    name: "Phase L1: Strom"
    direction: transmit
    dpt: "14.020"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "1-10 s"
    description: "Elektrischer Strom der Phase L1 in Ampere."
  - id: phase_b_current
    name: "Phase L2: Strom"
    direction: transmit
    dpt: "14.020"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "1-10 s"
    description: "Elektrischer Strom der Phase L2 in Ampere."
  - id: phase_c_current
    name: "Phase L3: Strom"
    direction: transmit
    dpt: "14.020"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "1-10 s"
    description: "Elektrischer Strom der Phase L3 in Ampere."
  - id: grid_frequency
    name: "Netzfrequenz"
    direction: transmit
    dpt: "14.033"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "5-30 s"
    description: "Netzfrequenz in Hz. Messquelle und Gültigkeitsbereich sind zu dokumentieren."
  - id: energy_counter_64_import
    name: "Netzbezug: 64-Bit-Wirkenergiezählerstand"
    direction: transmit
    dpt: "29.010"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60-300 s"
    description: "64-Bit-Wirkenergiezählerstand gemäß DPT ActiveEnergy_V64. Einheit, Skalierung und unterstützten Wertebereich des konkreten Geräts prüfen."
  - id: energy_counter_64_export
    name: "Netzeinspeisung: 64-Bit-Wirkenergiezählerstand"
    direction: transmit
    dpt: "29.010"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "60-300 s"
    description: "64-Bit-Wirkenergiezählerstand für Export. Unabhängig vom Importzähler führen."
  - id: load_shedding_request
    name: "Lastmanagement: Leistungsreduzierung anfordern"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: false
    cycle_time_recommended: "ereignisbasiert; Wiederholung nach anwendungsspezifischer Strategie"
    description: "Boolesche Anforderung einer Lastreduzierung. Die konkrete Reaktion des Verbrauchers muss separat definiert werden."
  - id: evse_enable
    name: "EVSE: Ladefreigabe"
    direction: write
    dpt: "1.001"
    flags: ["C", "W"]
    required: false
    cycle_time_recommended: "ereignisbasiert"
    description: "Boolesche Freigabe für eine Ladeeinrichtung. Keine direkte Aussage über den tatsächlich fließenden Ladestrom."
  - id: evse_current_limit
    name: "EVSE: Stromgrenze"
    direction: write
    dpt: "14.020"
    flags: ["C", "W"]
    required: false
    cycle_time_recommended: "ereignisbasiert bei Sollwertänderung"
    description: "Strom-Sollwert in Ampere. Phasenbezug, zulässiger Wertebereich, Minimalstrom und Geräteprotokoll müssen separat festgelegt werden."
  - id: tariff_price
    name: "HEMS: Tarifpreis"
    direction: transmit
    dpt: "9.024"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "bei Preisänderung und bei Tarifwechsel"
    description: "KNX-DPT Power ist kein standardisierter Tarifpreis. Dieses Objekt darf nicht als Preisobjekt verwendet werden; für Tarifpreise ist ein gesondertes, eindeutig typisiertes Profil erforderlich."
  - id: tariff_mode
    name: "HEMS: Tarif- oder Betriebsmodus"
    direction: write
    dpt: "20.102"
    flags: ["C", "W"]
    required: false
    cycle_time_recommended: "bei Moduswechsel"
    description: "DPT HVACMode ist für HVAC-Betriebsarten definiert und nicht als allgemeiner dynamischer Stromtarif zu verwenden. Eine Tarifcodierung benötigt ein eigenständig definiertes, interoperables Mapping."
  - id: meter_fault
    name: "Zähler: Störung"
    direction: transmit
    dpt: "1.006"
    flags: ["C", "R", "T"]
    required: false
    cycle_time_recommended: "bei Statusänderung; periodische Überwachung separat"
    description: "Boolescher Fehlerstatus. True bedeutet Störung; Details und Ursache erfordern gegebenenfalls zusätzliche Diagnoseobjekte."
recommended_ga_structure:
  - slot: 0
    function: "Netzanschlusspunkt: Wirkleistung mit dokumentierter Vorzeichenkonvention"
    dpt: "14.057"
  - slot: 1
    function: "Netzbezug: Wirkleistung"
    dpt: "14.057"
  - slot: 2
    function: "Netzeinspeisung: Wirkleistung"
    dpt: "14.057"
  - slot: 3
    function: "Netzbezug: Wirkenergiezählerstand"
    dpt: "13.011"
  - slot: 4
    function: "Netzeinspeisung: Wirkenergiezählerstand"
    dpt: "13.011"
  - slot: 5
    function: "Phase L1: Strom"
    dpt: "14.020"
  - slot: 6
    function: "Phase L2: Strom"
    dpt: "14.020"
  - slot: 7
    function: "Phase L3: Strom"
    dpt: "14.020"
  - slot: 8
    function: "Netzfrequenz"
    dpt: "14.033"
  - slot: 9
    function: "Lastmanagement: Leistungsreduzierung anfordern"
    dpt: "1.001"
  - slot: 10
    function: "EVSE: Ladefreigabe"
    dpt: "1.001"
  - slot: 11
    function: "Zähler: Störung"
    dpt: "1.006"
---

# Fachliche Beschreibung & Ingenieursregeln

## 1. Phasenbezogene vs. saldierende Messung

Der Functional Block unterscheidet zwischen dem phasenbezogenen elektrischen Zustand und dem Energiefluss über den Netzanschlusspunkt.

### Phasenbezogene Messung
Für dreiphasige Installationen werden Ströme und, sofern verfügbar, weitere Messgrößen für L1, L2 und L3 getrennt erfasst.
- Jeder Phasenwert muss eindeutig der betreffenden Phase zugeordnet sein.
- Die Stromwerte der drei Phasen dürfen nicht als Gesamtstrom addiert werden, um daraus eine physikalisch korrekte Gesamtleistung abzuleiten.
- Unsymmetrische Lasten und unterschiedliche Leistungsfaktoren erfordern eine phasenrichtige Leistungsberechnung.
- Phasenwerte sind besonders wichtig für die Begrenzung der Belastung einzelner Außenleiter (Schieflastvermeidung) und für die Ansteuerung dreiphasiger Verbraucher (z. B. Wallboxen).

### Saldierende Messung
Der Netzanschlusspunkt liefert typischerweise eine vorzeichenbehaftete Gesamtwirkleistung. Bei einer festgelegten Vorzeichenkonvention gilt beispielsweise:
- $P_{\text{Netz}} > 0$: Netzbezug (Import).
- $P_{\text{Netz}} < 0$: Netzeinspeisung (Export).

Diese Konvention ist eine Profilentscheidung und muss mit der tatsächlichen Messgeräteausgabe abgeglichen werden.

Aus einer saldierenden Gesamtleistung lassen sich die einzelnen Phasenleistungen nicht zuverlässig rekonstruieren.

> [!IMPORTANT]
> **Verbindliche Integrationsregel:**
> Import und Export sind als eigenständige Größen abzubilden. Die saldierende Momentanleistung darf nicht als Ersatz für getrennte Import- und Export-Energiezählerstände verwendet werden.
> Bei Energiezählern sind außerdem Zählerstand (monoton wachsend), Zählerdifferenz und Momentanleistung strikt zu unterscheiden. Ein kumulierter Energiezählerstand ist kein momentaner Leistungswert.

---

## 2. Vermeidung von Busüberlastung bei Leistungsmessungen

Messwerte sollten sowohl bei relevanten Änderungen als auch zyklisch aktualisiert werden. Ausschließlich zyklisches Senden kann wichtige Zustandsänderungen verzögern; ausschließlich ereignisbasiertes Senden kann bei stark schwankenden Messwerten den Bus unzulässig belasten.

### Empfohlene Übertragungsstrategie:
1. **Delta-Senden:** Eine Aktualisierung erfolgt, wenn die Abweichung zum zuletzt gesendeten Wert eine definierte Schwelle überschreitet (z. B. $\pm 5\%$ oder $\pm 100\text{ W}$).
2. **Mindestabstand (Sendebegrenzung):** Aufeinanderfolgende Telegramme desselben Objekts werden zeitlich begrenzt (z. B. maximal alle 1–2 Sekunden).
3. **Maximale Aktualisierungsdauer (Zyklus):** Auch bei stabilen Messwerten wird nach einem definierten Intervall (z. B. alle 60–300 s) eine Aktualisierung gesendet.
4. **Änderungspriorisierung:** Momentane Leistungswerte werden dynamischer aktualisiert als kumulierte Energiezählerstände (Wh / kWh).
5. **Startverhalten:** Nach Neustart oder Kommunikationswiederkehr werden gültige aktuelle Werte zeitverzögert verfügbar gemacht.

---

## 3. Lastmanagement & Wallbox-Steuerung (§14a EnWG & ISO 15118)

Das HEMS kann den Energiezähler als Eingangsgröße für die Begrenzung steuerbarer Verbraucher verwenden. Die tatsächliche Steuerung muss jedoch von der reinen Messwerterfassung getrennt bleiben.

### Regelungskette:
1. Messung des aktuellen Netzbezugs und der Einspeisung.
2. Ermittlung der verfügbaren Leistung unter Berücksichtigung konfigurierter Anschlussgrenzen.
3. Priorisierung von Verbrauchern nach Energiebedarf, Betriebszustand und Nutzerpräferenzen.
4. Übermittlung eines Sollwerts oder einer Freigabe an das geeignete Steuersystem.
5. Rückmeldung des tatsächlichen Betriebszustands und Überprüfung der Wirkung.

### EVSE und ISO 15118:
ISO 15118 beschreibt die Kommunikation zwischen Elektrofahrzeug und Ladeeinrichtung (State of Charge SoC, Ladezeitplan, Plug & Charge). Ein KNX-Gruppenobjekt für eine boolesche Ladefreigabe ersetzt weder diese Kommunikation noch das Protokoll zur Aushandlung zulässiger Ladeparameter.

Für interoperables Lastmanagement müssen mindestens folgende Informationen getrennt betrachtet werden:
- Ladefreigabe (`evse_enable`),
- maximal zulässiger Ladestrom (`evse_current_limit`),
- tatsächlich gemessene Ladeleistung,
- verfügbare Hausanschlussleistung,
- Ladezustand beziehungsweise Ladeanforderung (SoC), sofern vom EV übermittelt,
- Fehler- und Kommunikationsstatus.

Ein Strom-Sollwert muss insbesondere bei dreiphasigem Laden auf Phasenbezug und zulässige Minimal- und Maximalwerte (z. B. IEC 61851 Untergrenze 6 A) geprüft werden.

### §14a EnWG (Dimmbarkeit durch den Verteilnetzbetreiber):
Die Anforderungen aus §14a EnWG und den Festlegungen der Bundesnetzagentur sind als externe regulatorische Randbedingungen zu behandeln. Ein allgemeines KNX-Schaltobjekt begründet für sich genommen keine rechtskonforme Steuerbarkeit.
Die Umsetzung muss den für die konkrete Anlage geltenden Vorgaben entsprechen (z. B. netzorientierte Steuerung über Steuerbox / FNN-Steuerbox an HEMS auf max. 4,2 kW Netzwirkschnittstelle).

---

## 4. Dynamische Stromtarife & Speicher-Ladestrategien

Dynamische Stromtarife erfordern mindestens einen Preiswert, einen Gültigkeitszeitraum und eine eindeutige Währungs- und Energieeinheit. Ein allgemeiner KNX-DPT für elektrische Leistung darf nicht als Tarifpreis interpretiert werden.

Für eine herstellerneutrale HEMS-Kopplung ist deshalb ein zusätzliches Tarifprofil erforderlich, das mindestens Folgendes definiert:
- Preis je Energieeinheit, beispielsweise EUR/kWh oder Cent/kWh,
- Beginn und Ende des Gültigkeitszeitraums (Timestamp-Array),
- Herkunft und Aktualisierungszeitpunkt des Preises,
- Umgang mit fehlenden oder veralteten Preisen (Fallback auf Standardtarif),
- optionale Netzentgelte, Steuern und weitere Preisbestandteile,
- Zeitzone und Sommerzeitbehandlung.

### Speicherstrategie:
Ein Energiemanagementsystem kann Preisprognosen mit PV-Erzeugungsprognosen, Speichergrenzen und erwarteten Haushaltslasten kombinieren.
Eine robuste Strategie berücksichtigt:
- Mindestladezustand und erforderliche Energiereserve (Notstrom-Reserve),
- Lade- und Entladeleistungsgrenzen der Batterie (C-Rate),
- Wirkungsgrad und Alterungskosten je Vollzyklus,
- maximale Netzbezugsleistung,
- erwartete solare Einspeisung,
- Nutzerprioritäten und Notfallreserven.

> [!CAUTION]
> Preisoptimierung darf niemals die elektrischen Schutzgrenzen oder die für den Netzanschluss geltenden Steuerungsvorgaben außer Kraft setzen.

---

## 5. Fehlerszenarien & Diagnosematrix

| Fehlerfall | Erkennung | Empfohlene Reaktion |
|---|---|---|
| **Stromwandler ausgefallen** | Plausibilitätsprüfung, Diagnose oder unplausibler Messwert | Messwert als ungültig markieren; keine ungeprüfte Regelung auf Basis des betroffenen Kanals. |
| **Kommunikationsverlust zum Zähler** | Fehlende Aktualisierung innerhalb eines projektierten Zeitlimits | Messwert als veraltet kennzeichnen; sichere Ersatzstrategie aktivieren. |
| **Kommunikationsverlust zum Wechselrichter** | Fehlende Aktualisierung oder Diagnosemeldung | PV-Erzeugung nicht ungeprüft als verfügbar annehmen; Batterieladung pausieren. |
| **Zählerstand springt rückwärts** | Plausibilitätsprüfung, Neustart oder Zählerwechsel | Reset, Überlauf und Gerätewechsel unterscheiden; Historie nicht stillschweigend korrigieren. |
| **Import und Export widersprechen sich** | Vorzeichen- und Konsistenzprüfung | Konfiguration, Messpunkt und Vorzeichenkonvention prüfen. |
| **Tarifdaten fehlen** | Ablauf der Preisgültigkeit | Preisoptimierung deaktivieren oder auf eine ausdrücklich konfigurierte Ersatzstrategie wechseln. |
| **EVSE nicht erreichbar** | Ausbleibende Statusrückmeldung | Keine erfolgreiche Leistungsreduzierung unterstellen; tatsächlichen Zustand überwachen. |
| **Messwert bleibt unverändert** | Vergleich mit erwarteter Aktivität und Kommunikationsstatus | Zwischen realer Konstanz und Kommunikationsausfall unterscheiden. |

---

## 6. Normative DPT-Referenztabelle (KNX Vol. 3/7/2)

| DPT | Bezeichnung nach KNX-Norm | Verwendung im Profil |
|---|---|---|
| `14.057` | `DPT_Value_Power` | Momentane Wirkleistung in Watt (W) |
| `13.011` | `DPT_ActiveEnergy` | Wirkenergie in Wattstunden (Wh, 32-Bit signed) |
| `29.010` | `DPT_ActiveEnergy_V64` | Wirkenergie in Wattstunden (Wh, 64-Bit signed) |
| `14.020` | `DPT_Value_Electric_Current` | Strom in Ampere (A) |
| `14.033` | `DPT_Value_Frequency` | Netzfrequenz in Hertz (Hz) |
| `1.001` | `DPT_Switch` | Binäre Freigaben & Lastabwurf-Trigger |
| `1.006` | `DPT_Alarm` | Störmeldungen & Fehlerstatus |
