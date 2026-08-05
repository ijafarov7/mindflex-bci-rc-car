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

## 3. Arduino-Anschluss

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

## 4. Enthaltene Dateien

- **`*.py`**: kompletter Programmcode
- **`training_data.csv`**: bereits aufgezeichnete Trainingsdaten (aktuell FORWARD + STOP)
- **`trained_model.pkl`**: bereits trainiertes Modell -- kann direkt verwendet werden,
  muss nicht neu trainiert werden. Wird beim Start automatisch geladen.
- **`requirements.txt`**: benoetigte Python-Pakete

## 5. Weitere Fahrbefehle trainieren

Ueber den Button **"Training starten"** in der GUI koennen weitere Label
(LEFT/RIGHT/BACKWARD) aufgenommen werden. Danach ueber **"Modell neu trainieren"**
das Modell mit allen bis dahin gesammelten Daten neu berechnen.
