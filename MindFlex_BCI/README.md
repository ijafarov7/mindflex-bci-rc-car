# MindFlex BCI - Setup auf einem neuen Rechner

## 1. Voraussetzungen installieren

1. **Python 3.12** installieren: https://www.python.org/downloads/release/python-31210/
   - Bei der Installation unbedingt den Haken bei **"Add python.exe to PATH"** setzen.
2. In diesem Ordner ein Terminal oeffnen (im Explorer in den Ordner klicken, dann oben
   "Terminal oeffnen" bzw. Shift+Rechtsklick -> "PowerShell-Fenster hier oeffnen") und
   ausfuehren:

   ```
   pip install -r requirements.txt
   ```

## 2. Starten

- Doppelklick auf `start.bat`, **oder**
- im Terminal: `python main.py`

Weitere Modi:
```
python main.py --mode train     # interaktiver Konsolen-Trainingsmodus
python main.py --mode retrain   # Modell einmalig aus training_data.csv neu trainieren
```

## 3. Arduino-Bibliothek (EEG-Arduino)

Auf dem EEG-Arduino muss die [Arduino Brain Library](https://github.com/kitschpatrol/Brain)
installiert sein (liest den ThinkGear-Chip des MindFlex-Headsets aus und sendet die
CSV-Zeilen ueber Serial, die dieses Programm erwartet). Installation in der Arduino IDE:

1. ZIP von https://github.com/kitschpatrol/Brain herunterladen.
2. Arduino IDE -> Sketch -> Bibliothek einbinden -> .ZIP-Bibliothek hinzufuegen -> die
   heruntergeladene ZIP auswaehlen (oder manuell nach `Dokumente/Arduino/libraries`
   entpacken).
3. Das Beispiel-Sketch der Bibliothek (bzw. euer eigenes, darauf aufbauendes Sketch) auf
   den EEG-Arduino hochladen.

Der **RC-Auto-Arduino** ist nicht Teil dieses Projekts -- er wird als bereits
vorhandene Hardware vorausgesetzt, die die Einzelzeichen `F`/`B`/`L`/`R`/`S` per Serial
empfaengt und in Motoransteuerung umsetzt.

## 4. Arduino-Anschluss

Das Programm sucht automatisch nach einem seriellen Port, dessen Windows-Geraetename
"Arduino" enthaelt -- in der Regel muss NICHTS manuell eingestellt werden, einfach
Arduino(s) per USB anschliessen und starten.

Falls zwei Arduinos angeschlossen sind (EEG-Headset + RC-Auto) und ihr den Port lieber
fest vorgeben wollt, koennt ihr das ueber Umgebungsvariablen tun, bevor ihr startet:

```
set MINDFLEX_EEG_PORT=COM5
set MINDFLEX_CAR_PORT=COM7
python main.py
```

(Windows-Geraetemanager -> "Anschluesse (COM & LPT)" zeigt, welcher Port welchem
Arduino entspricht.)

## 5. Enthaltene Dateien

- **`*.py`**: kompletter Programmcode
- **`training_data.csv`**: bereits aufgezeichnete Trainingsdaten (aktuell FORWARD + STOP)
- **`trained_model.pkl`**: bereits trainiertes Modell -- kann direkt verwendet werden,
  muss nicht neu trainiert werden. Wird beim Start automatisch geladen.
- **`requirements.txt`**: benoetigte Python-Pakete

## 6. Weitere Fahrbefehle trainieren

Ueber den Button **"Training starten"** in der GUI koennen weitere Label
(LEFT/RIGHT/BACKWARD) aufgenommen werden. Danach ueber **"Modell neu trainieren"**
das Modell mit allen bis dahin gesammelten Daten neu berechnen.
