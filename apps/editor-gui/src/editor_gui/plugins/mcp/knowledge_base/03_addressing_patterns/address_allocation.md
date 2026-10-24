# Gruppenadressen-Bedarfsmatrix je Verbrauchertyp

Dieses Dokument definiert den exakten Adressbedarf (GA-Slots) für jeden typischen Aktor- und Sensorkanal in einem KNX-Projekt.

## 1. Übersicht nach Verbrauchertyp

| Verbrauchertyp | Minimaler GA-Bedarf | Standard GA-Bedarf | Erweiterter Bedarf (HCL / RGBW / Diagnose) | Empfohlene Dekadengröße |
|---|:---:|:---:|:---:|:---:|
| **Schaltkanal (Standard)** | 2 GAs | 3 GAs | 5 GAs | 5er oder 10er Block |
| **Schaltkanal (Treppenhaus/Last)** | 3 GAs | 4 GAs | 6 GAs (inkl. Strommessung) | 10er Block |
| **Dimmkanal (Mono / Weiß)** | 4 GAs | 5 GAs | 7 GAs (inkl. Dimmkurve/Nacht) | 10er Block |
| **Dimmkanal (Tunable White)** | 6 GAs | 8 GAs | 10 GAs | 10er Block |
| **Dimmkanal (RGBW / HSV)** | 6 GAs | 9 GAs | 12 GAs | 15er oder 20er Block |
| **Rollladenkanal** | 3 GAs | 4 GAs | 6 GAs (inkl. Aussperrschutz, Automatik) | 10er Block |
| **Jalousiekanal (Raffstore mit Lamelle)** | 4 GAs | 6 GAs | 9 GAs (inkl. Sonnenstand, Windalarm) | 10er Block |
| **Heizkanal / Raumthermostat (RTR)** | 4 GAs | 6 GAs | 9 GAs (inkl. Taupunkt, Sollwertverschiebung) | 10er Block |
| **Präsenzmelder (1-Kanal)** | 2 GAs | 4 GAs | 6 GAs (inkl. Helligkeit, Tag/Nacht, Sperre) | 10er Block |
| **Wetterstation (Gesamtsystem)** | 6 GAs | 12 GAs | 20 GAs (Fassadenazimut, Schwellwerte) | Eigene Mittelgruppe (z. B. 0/2/x) |

---

## 2. Standard-Slot-Belegung im Detail

### 2.1 Schaltkanal (10er Dekade)
- `Slot 0`: Schalten (Befehl) `DPT 1.001`
- `Slot 1`: Status Schalten `DPT 1.001`
- `Slot 2`: Treppenhaus / Zeitschaltung `DPT 1.010`
- `Slot 3`: Sperren / Zwangsführung `DPT 1.003` / `DPT 2.001`
- `Slot 4`: Szenenaufruf `DPT 18.001`
- `Slot 5`: Stromwert / Lastüberwachung `DPT 7.012` oder `DPT 9.021` (bei C-Last mit Strommessung)
- `Slot 6..9`: Reserve

### 2.2 Dimmkanal Tunable White (10er Dekade)
- `Slot 0`: Schalten Ein/Aus `DPT 1.001`
- `Slot 1`: Status Schalten `DPT 1.001`
- `Slot 2`: Relatives Dimmen Helligkeit `DPT 3.007`
- `Slot 3`: Absoluter Dimmwert (0-100%) `DPT 5.001`
- `Slot 4`: Status Dimmwert `DPT 5.001`
- `Slot 5`: Farbtemperatur Kelvin (Befehl) `DPT 7.600`
- `Slot 6`: Status Farbtemperatur `DPT 7.600`
- `Slot 7`: Sperren / Nachtmodus `DPT 1.003`
- `Slot 8`: Szenenaufruf `DPT 18.001`
- `Slot 9`: Relatives Dimmen Farbtemperatur `DPT 3.007`

### 2.3 Jalousie / Raffstore (10er Dekade)
- `Slot 0`: Auf/Ab Langzeit `DPT 1.008`
- `Slot 1`: Schritt/Stopp Kurzzeit `DPT 1.007`
- `Slot 2`: Absolute Höhe (0-100%) `DPT 5.001`
- `Slot 3`: Status Höhe `DPT 5.001`
- `Slot 4`: Absolute Lamelle (0-100%) `DPT 5.001`
- `Slot 5`: Status Lamelle `DPT 5.001`
- `Slot 6`: Status Fahrt aktiv `DPT 1.002`
- `Slot 7`: Aussperrschutz / Fensterkontakt `DPT 1.003`
- `Slot 8`: Automatikbetrieb Freigabe `DPT 1.003`
- `Slot 9`: Szenenaufruf `DPT 18.001`
