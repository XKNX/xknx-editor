---
id: dev_dali_gateway
title: "Geräte-Archetyp: KNX/DALI-2 Gateways (Gruppen- vs. EVG-Steuerung, DT8 Farbsteuerung & HCL)"
device_class: gateway
implemented_functional_blocks:
  - fb_dimming_actuator
  - fb_switch_actuator
key_parameters:
  - name: "Adressierungsmodus (Broadcast vs. Gruppe vs. Einzel-EVG)"
    default_or_recommended: "Gruppensteuerung für den Regelbetrieb; Einzel-EVG-Adressierung für Inbetriebnahme, Diagnose und individuelle Steuerung; Broadcast nur für bewusst globale Kommandos"
    impact: "Gruppen reduzieren die Anzahl wiederkehrender DALI-Kommandos und unterstützen ein koordiniertes Schalten und Dimmen. Einzeladressen ermöglichen differenzierte Steuerung und Diagnose, verursachen bei vielen Geräten aber mehr Buskommunikation."
  - name: "DALI Device Type (DT6 vs. DT8 Colour Control)"
    default_or_recommended: "DT6 für einfarbige Leuchten; DT8 für Tunable White oder RGBW, sofern EVG und Gateway den benötigten DT8-Farbtyp unterstützen"
    impact: "DT6 steuert einen Helligkeitskanal je EVG. DT8 kann Helligkeit und unterstützte Farbparameter über ein EVG adressieren. Zwei DT6-Kanäle für Warmweiß/Kaltweiß benötigen typischerweise getrennte EVG-Adressen und eine koordinierte Gateway-Logik."
  - name: "System Failure Level (Verhalten bei DALI-Busspannungsausfall)"
    default_or_recommended: "Anwendungsabhängig konfigurieren; einen sicheren, projektspezifisch abgestimmten Lichtzustand definieren und die EVG-Unterstützung prüfen"
    impact: "Bestimmt das Verhalten bei Verlust der DALI-Kommunikation. Die tatsächlich erreichbare Reaktion hängt von den EVG-Einstellungen und der Verfügbarkeit der Netzversorgung ab."
  - name: "Power On Level (Verhalten bei Netzspannungswiederkehr am EVG)"
    default_or_recommended: "Je Raum- und Sicherheitskonzept festlegen; nicht pauschal mit dem System Failure Level gleichsetzen"
    impact: "Legt das Einschaltverhalten nach Wiederkehr der EVG-Netzversorgung fest. Es ist von der Reaktion bei DALI-Busausfall zu unterscheiden."
  - name: "Lampen- und EVG-Fehlerüberwachung (Zykluszeit)"
    default_or_recommended: "Periodische Abfrage mit projektspezifischer Zykluszeit, gestaffelt nach Anlagenumfang und Diagnosebedarf"
    impact: "Häufige Abfragen erhöhen die DALI-Buslast. Statusobjekte müssen Lampenfehler, EVG-Fehler, Kommunikationsausfall und unbekannten Zustand semantisch unterscheiden."
---

# Fachliche Beschreibung & Ingenieursregeln

### 1. DALI-Gruppen vs. Einzel-EVG-Steuerung (Buslast-Grenzen auf dem 1200-Baud DALI-Bus)

- **Regelbetrieb:** Leuchten, die gemeinsam geschaltet und gedimmt werden sollen, sind bevorzugt über **DALI-Gruppen (0–15)** anzusteuern. Gruppenkommandos adressieren mehrere zugeordnete EVGs mit einem einzigen Telegramm, anstatt identische Befehle nacheinander an Einzeladressen zu senden.
- **Einzel-EVG-Steuerung:** Einzeladressen bleiben erforderlich, wenn Leuchten unabhängig voneinander gesteuert, identifiziert, konfiguriert oder diagnostiziert werden müssen. Sie sind nicht grundsätzlich verboten und für dynamische Einzelarbeitsplätze oder Szenen sinnvoll.
- **Broadcast:** Broadcast-Kommandos erreichen alle DALI-Betriebsgeräte im betreffenden Bussegment unadressiert. Sie sind für globale Aktionen (z. B. Zentral-Aus) geeignet, nicht als Standardersatz für die logische Gruppierung von Räumen.
- **Buslast:** DALI arbeitet mit nur 1200 bit/s. Neben Steuerkommandos beanspruchen Abfragen, Antworten, Wiederholungen und Inbetriebnahmeverkehr die verfügbare Kommunikationszeit.
- **Synchrones Schalten und Dimmen:** Gruppenbefehle vermeiden sichtbare zeitliche Kaskadeneffekte vieler separater Einzelkommandos. Für konsistentes Fade-Verhalten müssen die relevanten EVGs kompatible Fade-Einstellungen unterstützen (Fade-Zeit und Fade-Rate gezielt parametrieren).
- **Adressierungsregeln für die Wissensbasis:**
  - Gruppenmitgliedschaft und Einzeladresse sind getrennte Konfigurationsinformationen.
  - Die KNX-Gruppenadresse und die DALI-Gruppenadresse sind unterschiedliche Adressräume.
  - Das Gateway muss KNX-Befehl, DALI-Ziel und Rückmeldeverhalten eindeutig abbilden.
  - Broadcast-Steuerung darf nicht dazu führen, dass KNX-Statusobjekte fälschlich eine individuelle Leuchtenrückmeldung suggerieren.

---

### 2. DT6 (Single Channel) vs. DT8 (Tunable White & RGBW nach IEC 62386-209)

- **DT6:** DALI Device Type 6 steuert einen Helligkeitskanal. Eine Tunable-White-Leuchte, die aus zwei getrennt steuerbaren EVGs für Warmweiß und Kaltweiß aufgebaut ist, belegt zwei DALI-Kurzadressen. Das Gateway koordiniert Helligkeit und Farbtemperatur über beide Adressen.
- **DT8:** DALI Device Type 8 unterstützt integrierte Farbsteuerung nach IEC 62386-209. Helligkeit und unterstützte Farbparameter (Farbtemperatur $T_c$, RGBWAF, Koordinaten $x/y$) werden über **dieselbe DALI-Kurzadresse** gesteuert.
- **KNX-DPT-Zuordnungen aus KNX Standard V3.0.0:**

| Funktion | DPT | Bezeichnung nach KNX-Norm | Verwendung |
|---|---|---|---|
| Helligkeitswert in Prozent | `DPT 5.001` | `DPT_Scaling` | Prozentuale Helligkeit (0–100%) |
| Absolute Farbtemperatur | `DPT 7.600` | `DPT_Absolute_Colour_Temperature` | Farbtemperatur in Kelvin (z. B. 2700 K – 6500 K) |
| RGBW-Farbwert | `DPT 251.600` | `DPT_Colour_RGBW` | Kombiniertes 6-Byte Farbobjekt (RGBW + Gültigkeitsmaske) |
| Kombinierte Helligkeit & Farbtemperatur | `DPT 250.600` | `DPT_Brightness_Colour_Temperature_Control` | Nur verwenden, wenn vom Gateway-Objektprofil unterstützt |

- **RGBW vs. HSV:** RGBW und HSV repräsentieren unterschiedliche Farbräume. Eine KNX-Anwendung darf HSV-Werte nicht unverändert als RGBW-Daten übertragen. Unterstützt das Gateway beide Formate, muss eine definierte Umrechnung inklusive Weißkanalverhalten (Weißausgleich) erfolgen.

---

### 3. Human Centric Lighting (HCL) & Tageslichtnachführung

- **Autarke Gateway-Zeitkurve:** Das Gateway verwaltet eine zeitabhängige Farbtemperatur- und Helligkeitskurve selbstständig (Astronomische Uhr im Gateway). Vorteil: Keine zyklischen Sollwert-Telegramme auf dem KNX-Bus.
- **Externer KNX-Zeitgeber / Logik:** Alternativ liefern zentrale KNX-Logiken Sollwerte für Farbtemperatur und Helligkeit.
- **Keine Doppelregelung:** Eine autarke Gateway-Kurve und eine externe KNX-Regelung dürfen niemals unkoordiniert dieselben Sollwerte überschreiben. Es muss einen eindeutigen Sollwert-Master je Funktion geben.
- **Manuelle Übersteuerung (Override):** Manuelle Bedienung über Taster oder Visu löst einen definierten Übersteuerungszustand aus. Während des Overrides darf die HCL-Automatik den manuell eingestellten Wert nicht sofort wieder überschreiben.
- **Auto-Reset:** Rückfall in den Automatikbetrieb nach konfigurierbarer Zeit (z. B. 2 Stunden nach letzter manueller Bedienung oder beim nächsten regulären Schaltzyklus).

---

### 4. Fehlerüberwachung & Instandhaltung (Maintenance)

- **Statussemantik:** Lampenfehler, EVG-Fehler, DALI-Kommunikationsverlust und unbestimmter Zustand sind getrennt abzubilden.
- **KNX-DPT-Prüfung:**
  - `DPT 1.006` ist **`DPT_Alarm`** und eignet sich für binäre Alarmsemantik.
  - `DPT 237.600` ist **`DPT_DALI_Control_Gear_Diagnostics`** (DALI-Steuergeräte-Diagnose nach KNX-Norm).
- **Automatische EVG-Tausch-Funktion (Easy Replacement):**
  - Defektes EVG wird im stromlosen Zustand 1:1 durch ein typgleiches Neugerät ersetzt.
  - Das Gateway erkennt ein neues unadressiertes EVG und weist ihm automatisch die Adresse, Gruppe und Parameter des ausgefallenen EVGs zu, ohne dass ETS gestartet werden muss.

---

### 5. Notlichtintegration (DALI Notleuchten mit Einzelbatterie nach IEC 62386-202)

- **Prüf- und Testzyklen:**
  - **Funktionstest:** Kurzer Funktionstest von Leuchtmittel, Elektronik und Umschaltkontakt (z. B. wöchentlich/monatlich).
  - **Betriebsdauertest:** Vollständige Entladung zur Überprüfung der Nennbetriebsdauer (z. B. 1 h oder 3 h nach DIN EN 50172 / VDE 0108-100, jährlich).
- **Testkoordination über KNX:** Das Gateway triggert Tests und meldet Restkapazität, Ladezustand und Testergebnis strukturiert an übergeordnete Facility-Management-Systeme zurück.
