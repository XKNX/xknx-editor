# Standardisierte 3-stufige Gruppenadress-Struktur (Best Practice)

## 1. Philosophie & Zielsetzung
In professionellen KNX-Projekten ist die Struktur der Gruppenadressen (GAs) das Rückgrat der gesamten Gebäudeautomation. Eine unstrukturierte Vergabe führt unweigerlich zu Integrationsfehlern, erschwerter Fehlersuche bei Busstörungen und Inkonsistenzen bei Visualisierungen und Gateways (z. B. Home Assistant).

Die hier definierte Struktur kombiniert die bewährten Richtlinien der **KNX Swiss** mit dem **deterministischen Slot-Offset-Prinzip**.

---

## 2. Hauptgruppen-Aufteilung (Gewerk-Ebene)
Die Hauptgruppe (0 bis 31) definiert das **Gewerk bzw. die Systemebene**:

| Hauptgruppe | Gewerk / Funktionsbereich | Beschreibung |
|---|---|---|
| **0** | **Zentral- & Sicherheitsfunktionen** | Gewerkeübergreifende Notbefehle, Wind-/Regenalarm, Brandfall, Panik, Zentral Aus, Abwesenheit |
| **1** | **Beleuchtung** | Schalten, Dimmen, DALI-Gruppen, Tunable White, RGBW, Licht-Szenen |
| **2** | **Beschattung / Sonnenschutz** | Jalousien (Raffstore), Rollläden, Markisen, Fensterantriebe, Fassadenautomatik |
| **3** | **Heizung, Lüftung, Klima (HVAC)** | Raumtemperaturregler (RTR), Stellgrößen, Betriebsarten, Taupunktüberwachung, KWL |
| **4** | **Sensorik & Umwelt** | Präsenzmelder, Bewegungsmelder, Wetterdaten (Lux, Wind, Temperatur, Sonnenstand) |
| **5** | **Energie & HEMS** | PV-Erzeugung, Stromspeicher, Zählerwerte (Wirkleistung, Wirkarbeit), EV-Wallboxen |
| **6** | **Multimedia & Zutritt** | Audio-Multiroom, Sprechanlagen, Türöffner, RFID/Keyless Entry |
| **7** | **Logik & Hilfsobjekte** | Virtuelle Verknüpfungen, Schwellwertschalter, Zeitschaltprogramme |
| **8–13** | *Reserve / Projektbezogen* | Z. B. Gartenbewässerung, Poolsteuerung, gewerbliche Sonderanlagen |
| **14** | **Visualisierung & Schnittstellen** | Spezifische Handshake- und Sammelobjekte für Drittsysteme |
| **15** | **Systemüberwachung & Diagnose** | Heartbeat-Signale, Busspannungsüberwachung, Gerätefehler, Linienkoppler-Status |

---

## 3. Mittelgruppen-Aufteilung (Orts- / Bereichs-Ebene)
Die Mittelgruppe (0 bis 7) unterteilt das Projekt geografisch nach **Geschoss bzw. Bauteil**:

| Mittelgruppe | Gebäudeebene | Beispiele |
|---|---|---|
| **0** | **Zentral / Übergreifend** | Gewerke-Zentralbefehle für das gesamte Gebäude (z. B. Zentral-Aus Licht) |
| **1** | **Erdgeschoss (EG)** | Flur, Küche, Wohnzimmer, Gäste-WC, Terrasse |
| **2** | **Obergeschoss (OG)** | Schlafzimmer, Kinderzimmer, Hauptbad, Ankleide |
| **3** | **Dachgeschoss (DG) / Staffelgeschoss** | Studio, Büro, Technikraum oben |
| **4** | **Untergeschoss / Keller (KG)** | Technikzentrale, Waschküche, Vorratsraum, Werkstatt |
| **5** | **Außenbereich / Garten / Garage** | Zufahrt, Carport, Gartenbeleuchtung, Fassade |
| **6** | **Nebengebäude / Einliegerwohnung** | Werkstatt, Gartenhaus, separates Apartment |
| **7** | **Technik & Verteiler** | Zählerschränke, UV-interne Sammelbefehle |

---

## 4. Untergruppen-Aufteilung: Das Dekaden-/Slot-Offset-Muster
Die Untergruppe (0 bis 255) wird in **feste 10er-Blöcke (Dekaden)** je Verbraucher unterteilt. Dadurch lässt sich die Funktion eines Kommunikationsobjekts sofort deterministisch am GA-Offset ablesen:

```text
Untergruppe = (Verbraucher-Index * 10) + Slot-Offset
```

### Beispiel: Beleuchtungskanal (Universal-Dimmkanal)
Verbraucher 1 in Raum EG Wohnzimmer:
- `1/1/10` = Licht WZ Decke -- Schalten Befehl (Offset `0`)
- `1/1/11` = Licht WZ Decke -- Status Schalten (Offset `1`)
- `1/1/12` = Licht WZ Decke -- Relatives Dimmen (Offset `2`)
- `1/1/13` = Licht WZ Decke -- Absoluter Dimmwert Befehl (Offset `3`)
- `1/1/14` = Licht WZ Decke -- Status Dimmwert (Offset `4`)
- `1/1/15` = Licht WZ Decke -- Sperren / Nachtmodus (Offset `5`)
- `1/1/16` = Licht WZ Decke -- Farbtemperatur TW Befehl (Offset `6`)
- `1/1/17` = Licht WZ Decke -- Status Farbtemperatur (Offset `7`)

### Beispiel: Jalousiekanal
Verbraucher 1 in Raum EG Küche:
- `2/1/10` = Jalousie Küche -- Auf / Ab Langzeitbefehl (Offset `0`)
- `2/1/11` = Jalousie Küche -- Schritt / Stopp Kurzzeitbefehl (Offset `1`)
- `2/1/12` = Jalousie Küche -- Absolute Höhe Befehl (Offset `2`)
- `2/1/13` = Jalousie Küche -- Status Höhe Rückmeldung (Offset `3`)
- `2/1/14` = Jalousie Küche -- Absolute Lamelle Befehl (Offset `4`)
- `2/1/15` = Jalousie Küche -- Status Lamelle Rückmeldung (Offset `5`)
- `2/1/16` = Jalousie Küche -- Status Fahrt aktiv (Offset `6`)
- `2/1/17` = Jalousie Küche -- Aussperrschutz Tür geöffnet (Offset `7`)

---

## 5. Vorteile dieses Schemas
1. **Kein Adress-Chaos:** Ingenieure und Software-Agenten können ohne ETS-Lookup anhand von Kanal und Gewerk die exakte GA voraussagen.
2. **Kollisionsfreiheit:** Befehl und Status sind durch strikt getrennte Offsets (z. B. Gerade = Befehl, Ungerade = Status) unverwechselbar.
3. **Filtertabellen in Linienkopplern:** Geschossbezogene Mittelgruppen ermöglichen saubere Routing-Filter in TP-Linienkopplern, sodass interner Raumverkehr nicht über die Hauptlinie fließt.
