# MindFlex BCI – so startest du das Programm

## Mac
1. Python 3 installieren (https://www.python.org/downloads/), falls noch nicht vorhanden.
2. Doppelklick auf `start.command`.
   - Beim ersten Start werden die nötigen Pakete automatisch installiert (Internet nötig, ein paar Minuten).
   - Meldet macOS „nicht verifizierter Entwickler": Rechtsklick auf `start.command` → Öffnen.

Alternativ im Terminal, in diesem Ordner:
```
pip3 install -r requirements.txt
python3 main.py
```

## Windows
`pip install -r requirements.txt` ausführen, dann Doppelklick auf `start.bat`.

## Wie es weitergeht
Bluetooth einrichten, erstes Profil anlegen, kalibrieren: siehe [`README.md`](README.md).

## Inhalt
- `main.py` und die übrigen `.py`-Dateien – das Programm
- `profile/` – entsteht beim ersten Start; Profile mit Trainingsdaten und Modellen
- `arduino/` – Sketches für EEG-Arduino (`mindflex_eeg`) und RC-Auto (`rc_car_4wd`, `rc_car_test`), mit der Arduino IDE hochladen

## Hardware
MindFlex-Headset mit Arduino Uno (USB oder BLE) und RC-Auto mit Arduino Duemilanove + BLE-Modul.
Ohne Hardware startet die Oberfläche, zeigt aber keine Daten.
