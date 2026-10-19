## Anbindung per Bluetooth (Ergänzung, 2026-09-23)

Dieser Sketch wurde so erweitert, dass er die EEG-Daten auch **kabellos**
empfangen kann — über das BLE-Modul am Arduino statt über USB.

**Warum es dafür ein zweites Programm braucht:** Processing kann kein
Bluetooth Low Energy. Seine `serial`-Bibliothek spricht serielle
Schnittstellen, und ein CC2541-Modul erzeugt unter macOS keine. Deshalb
läuft daneben `ble_bridge.py`: Es bedient die Funkstrecke und reicht jede
Zeile über eine lokale Netzwerkverbindung an Processing weiter.

```
MindFlex → Arduino → BLE-Modul ~~> ble_bridge.py → TCP → Brain Grapher
```

**Reihenfolge beim Start — erst die Brücke, dann der Sketch:**

1. Einmalig: `pip3 install bleak`
2. Im Terminal: `python3 ble_bridge.py`
   Warten, bis dort `Empfang laeuft` steht.
3. `BrainGrapher.pde` in Processing öffnen und starten.

Die Brücke verbindet sich nach einem Funkabriss von selbst neu und läuft,
bis du sie mit Strg-C beendest.

**Zurück auf Kabel:** In `BrainGrapher.pde` oben `USE_BLE = false;` setzen.
Dann gilt wieder der ursprüngliche Weg über den seriellen Anschluss, den
Index stellst du über `SERIAL_INDEX` ein. Die unveränderte Originaldatei
liegt als `BrainGrapher_original_seriell.pde.bak` daneben.

**Moduladresse:** Sie steht oben in `ble_bridge.py` und ist unter macOS
eine rechnerspezifische Kennung — auf einem anderen Mac muss sie neu
ermittelt werden (im BCI-Projektordner mit `python3 ble_scan.py`) und dann
über `MINDFLEX_EEG_BLE_ADDRESS` gesetzt werden.

---

##Processing Brain Grapher

####Overview
This is a simple Processing application for graphing changes in brain waves over time. It's designed to read data from a hacked MindFlex EEG headset connected via USB.

It's mostly a proof of concept, demonstrating how to parse serial packets from the Arduino Brain Library, monitor signal strength, etc.

`BrainGrapher.pde` is the main project file. Open this in the Processing PDE to work with the project.

You may need to modfiy the index value in the line `serial = new Serial(this, Serial.list()[0], 9600);` inside the app's `setup()` function file depending on which serial / USB port your Arduino is connected to. (Try ` Serial.list()[1]`, ` Serial.list()[2]`, ` Serial.list()[3]`, etc.)

####Repository Rename
This project was formerly “Processing-Brain-Grapher” on GitHub, but was renamed to just “BrainGrapher” in 2014 for simplicity's sake.

####Dependencies
- The core Processing project. Tested with [Processing 1.5.1](http://processing.org/download/) and [Processing 2.0a5](http://code.google.com/p/processing/downloads/list).

- Version 2.0.4 of the [ControlP5 GUI Library](http://www.sojamo.de/libraries/controlP5/) is included with this project in the `/code` folder. No installation is necessary.

- If you're using this with a hacked MindFlex, you'll need the [Arduino Brain Library](https://github.com/kitschpatrol/Brain) installed and running on your Arduino. Additional instructions at [frontiernerds.com/brain-hack](](http://frontiernerds.com/brain-hack). 


####Colophon
Created by Eric Mika at NYU ITP in the spring of 2010. Revised in Spring 2012 to keep up with Processing and ControlP5 updates. Updated once more in early 2014 with bundled dependencies and more fixes for Control P5.

####Contact
Eric Mika  
ermika@gmail.com  
@kitschpatrol