---
id: dev_push_button_panel
title: "Geräte-Archetyp: Mehrfach-Taster & Touch-Panels (Glastaster, Touch-Displays)"
device_class: sensor
implemented_functional_blocks:
  - fb_switch_actuator
  - fb_dimming_actuator
  - fb_shutter_blind
key_parameters:
  - name: "Tastenfunktion (Schalten / Dimmen / Jalousie / Wertgeber)"
    default_or_recommended: "Funktionsspezifisch je Taste (z. B. Zweitast-Bedienung oder Ein-Tasten-Umschaltung)"
    impact: "Bestimmt die erzeugten KNX-Objekte (z. B. getrennte KOs für Schalten, Rel. Dimmen, Kurz-/Langzeitfahrt)."
  - name: "Status-LED Zuordnung (Intern vs. Externes KO)"
    default_or_recommended: "Externes Kommunikationsobjekt (Status-GA des Aktors)"
    impact: "LED spiegelt zwingend den tatsächlichen Aktorstatus wider, nicht den lokalen Tastendruck."
  - name: "Langer Tastendruck Entprellzeit / Zeitschwelle"
    default_or_recommended: "400 ms"
    impact: "Trennt zuverlässig Kurzzeit-Stopp von Langzeitfahrt bei Beschattung oder Schalten von Dimmen."
  - name: "Display-Standby / Nachtabsenkung der Beleuchtung"
    default_or_recommended: "Aktiviert bei Nachtobjekt (DPT 1.001) oder Näherungssensor"
    impact: "Verhindert störende Raumaufhellung in Schlaf- und Wohnräumen."
---

# Geräte-Archetyp: Touch- & Glastaster (MDT, GVS, Zennio)

## 1. Funktion & Rolle im System
Moderne Raumtaster (z. B. MDT Glastaster II Smart, GVS Waltz, Zennio Flat/Tecla) sind multifunktionale Bedienzentralen. Sie vereinen Bedienung, Raumklimaerfassung (Temperatursensor) und Statusanzeigen (Farbdisplays, RGB-Status-LEDs).

---

## 2. Best-Practice Parametrierung

### 2.1 Status-LEDs: Das Hörer-Prinzip
> [!IMPORTANT]
> **Niemals interne Rückmeldung wählen:**
> Die LED darf nicht direkt an den Tastendruck gekoppelt werden. Ein Relais kann gesperrt sein, die Sicherung ausgelöst haben oder das Signal nicht beim Aktor angekommen sein.
> Die LED muss stets über ein **externes Status-KO** mit der Rückmelde-GA des jeweiligen Aktorkanals verknüpft sein.

### 2.2 Tastendruck-Differenzierung
- **Schalten / Dimmen (Eintastenbedienung):**
  - Kurzer Tastendruck (< 400 ms): Umschalten Ein/Aus (`DPT 1.001`).
  - Langer Tastendruck (> 400 ms): Relatives Dimmen (`DPT 3.007`).
- **Jalousiebedienung:**
  - Kurzer Tastendruck: Stopp / Lamellenverstellung (`DPT 1.007`).
  - Langer Tastendruck: Vollständiges Auf- oder Abfahren (`DPT 1.008`).

---

## 3. Patschfunktion & Abwesenheits-Geste
- Viele moderne Taster bieten eine **Patsch- bzw. Mehrfachberührungs-Funktion** (z. B. Berühren mit der ganzen Handfläche beim Verlassen des Raumes).
- **Best Practice:** Die Patschfunktion triggert eine lokale "Raum-Aus"-Szene oder das Ausschalten aller Lichtquellen im jeweiligen Raum, ohne nach einzelnen Tasten suchen zu müssen.
