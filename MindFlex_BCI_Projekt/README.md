# MindFlex BCI – Anleitung

Die Python-Anwendung zum Projekt. Überblick und Hardware: [Haupt-README](../README.md),
technische Details: [`docs/ARCHITEKTUR.md`](../docs/ARCHITEKTUR.md).

## 1. Installieren

Benötigt wird **Python 3.12** ([python.org](https://www.python.org/downloads/)). Unter Windows
bei der Installation den Haken bei **„Add python.exe to PATH“** setzen.

**Mac:** Doppelklick auf `start.command`. Beim ersten Start richtet das Skript eine eigene
Python-Umgebung (`.venv`) ein und installiert die Pakete – das dauert ein paar Minuten. Meldet
macOS „nicht verifizierter Entwickler“: Rechtsklick auf `start.command` → Öffnen.

**Windows:** In diesem Ordner ein Terminal öffnen und einmalig

```
pip install -r requirements.txt
```

ausführen, danach Doppelklick auf `start.bat`.

**Von Hand (alle Systeme):**

```bash
pip install -r requirements.txt
python main.py
```

## 2. Arduinos vorbereiten

Die Sketches liegen in [`arduino/`](arduino):

| Sketch | Board | Bibliothek |
|---|---|---|
| `mindflex_eeg/` | Arduino Uno am Headset | [Arduino Brain Library](https://github.com/kitschpatrol/Brain) |
| `rc_car_4wd/` | Arduino Duemilanove im Auto | Adafruit Motor Shield Library **v1** (`AFMotor`) |
| `rc_car_test/` | Auto-Arduino, ohne Motoren | – |

Vor dem Hochladen auf den Uno das Headset ausschalten – sein Datenstrom an Pin 0 stört
sonst den Upload. Im Auto-Sketch prüft man mit `T` im Seriellen Monitor, ob alle Räder
vorwärts drehen, und stellt falsch laufende Motoren in `INVERT[]` um.

## 3. Bluetooth einrichten

Das Auto wird standardmäßig per Bluetooth angesteuert, das Headset standardmäßig per
USB-Kabel. Die Funkmodule sind BLE-Module (CC2541) und tauchen **nicht** in den
Bluetooth-Einstellungen auf.

1. Nur das Auto einschalten, dann

   ```bash
   python ble_scan.py
   ```

   Das Programm listet alle Module mit dem Dienst `FFE0` auf.
2. Adresse übernehmen – entweder dauerhaft in `config.py` (`MINDFLEX_CAR_BLE_ADDRESS`) oder
   beim Start:

   ```bash
   MINDFLEX_CAR_BLE_ADDRESS=<Adresse> python main.py
   ```

3. Zum Testen ohne EEG: `python ble_test.py` schickt jeden Befehl einmal,
   `python ble_remote.py` steuert das Auto mit W A S D (nur Mac und Linux).

Unter macOS ist die Adresse eine UUID, die auf jedem Mac anders ist – auf einem neuen Rechner
also neu suchen.

**Headset per Funk** statt Kabel (Uno mit eigenem BLE-Modul, nur das Headset eingeschaltet
lassen und ebenfalls mit `ble_scan.py` suchen):

```bash
MINDFLEX_EEG_TRANSPORT=ble MINDFLEX_EEG_BLE_ADDRESS=<Adresse> python main.py
```

## 4. Erste Schritte in der Oberfläche

1. **Profil anlegen** (oben rechts, „Neues Profil …“). Jede Person braucht ein eigenes Profil,
   denn EEG-Muster unterscheiden sich stark. Gespeichert wird in `profile/<name>/`.
2. **Headset aufsetzen** und warten, bis oben „Signal 100 %“ steht. Gelb heißt: Sitz prüfen.
3. **Kalibrierung …** starten. Das Programm führt durch 18 kurze Blöcke und sagt auf dem Mac
   jede Anweisung an (Voreinstellung: Augen zu = STOP, Augen auf = FORWARD, Kopf schütteln =
   BACKWARD; die Anweisungen lassen sich ändern). Danach „Modell jetzt neu trainieren“.
4. **Genauigkeit prüfen:** Unten rechts stehen die drei Verfahren und die Basisrate. Liegt das
   beste Verfahren nicht klar über der Basisrate, mehr Daten aufnehmen. Über
   „Trainingsdaten ansehen …“ gibt es Empfehlungen, welche Befehle noch Daten brauchen.
5. **Fahren:** Fahrsperre lösen. Mit „Manuell fahren“ lässt sich das Auto jederzeit per
   Tastatur übernehmen – es fährt nur, solange eine Taste gedrückt ist.

## 5. Weitere Startoptionen

```bash
python main.py --mode train     # Training in der Konsole statt in der Oberfläche
python main.py --mode retrain   # Modell des Profils einmal neu trainieren und beenden
```

| Umgebungsvariable | Wirkung |
|---|---|
| `MINDFLEX_PROFIL=<name>` | mit diesem Profil starten |
| `MINDFLEX_EEG_PORT=<port>` | Port des Uno fest vorgeben (z. B. `COM5` oder `/dev/cu.usbmodem…`) |
| `MINDFLEX_CAR_TRANSPORT=serial` | Auto per USB-Kabel statt Bluetooth, Port mit `MINDFLEX_CAR_PORT` |
| `MINDFLEX_ACTIVE_LABELS=STOP,FORWARD` | nur diese Befehle trainieren und vorhersagen |

Unter Windows werden Umgebungsvariablen vorher mit `set NAME=Wert` gesetzt. Das Programm
schreibt ein Protokoll nach `mindflex_bci.log`.

## 6. Dateien

| Datei | Inhalt |
|---|---|
| `main.py` und die übrigen `.py`-Dateien | das Programm |
| `config.py` | alle Einstellungen an einer Stelle |
| `profile/` | entsteht beim ersten Start; je Person Trainingsdaten, Modell und Kalibrierung. **Wird nicht zu GitHub hochgeladen.** |
| `arduino/` | die drei Sketches |
| `requirements.txt` | benötigte Python-Pakete |
