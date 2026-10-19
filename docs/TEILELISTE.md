# Teileliste

Alles, was man zum Nachbauen braucht. Verkabelung: [`images/wiring.svg`](images/wiring.svg),
Begründungen für die Bauteilwahl: [`ARCHITEKTUR.md`](ARCHITEKTUR.md#8-bauteilwahl-im-vergleich).

## EEG-Seite

| Anzahl | Bauteil | Hinweis |
|---|---|---|
| 1 | MindFlex-Headset (Mattel) | enthält das TGAM-Modul von NeuroSky; gebraucht oft günstig |
| 1 | Arduino Uno oder Nachbau | hier ein Funduino Uno |
| 1 | BLE-Modul CC2541 (HM-10, AT-09 o. ä.) | nur für den Funkbetrieb |
| 1 | 9-V-Block mit Batteriefach, Schalter und Hohlstecker 5,5 × 2,1 mm | Versorgung ohne USB-Kabel |
| – | dünne Litze, Lötkolben | zwei Drähte an T-Pin und GND des TGAM-Moduls |
| – | Klett- oder Kabelbinder | Arduino und Batteriefach am Headset befestigen |

## Auto-Seite

| Anzahl | Bauteil | Hinweis |
|---|---|---|
| 1 | Arduino Duemilanove oder Uno | hier ein Duemilanove (ATmega328P) |
| 1 | L293D-Motor-Shield (HW-130) | Adafruit Motor Shield v1 oder kompatibel |
| 4 | TT-Getriebemotor 3–6 V | gelbe Standard-Getriebemotoren |
| 4 | Rad für TT-Motoren | ca. 65 mm |
| 1 | BLE-Modul CC2541 (HM-10, AT-09 o. ä.) | |
| 1 | 9-V-Block mit Batterieclip | Stromversorgung des Autos |
| 8 | Schraube M3 mit Mutter | Motorhalter am Chassis (8 Bohrungen Ø 3 mm) |
| 1 | Chassis, 3D-gedruckt | [`hardware/chassis_car.stl`](../hardware/chassis_car.stl), z. B. aus PLA |
| – | Dupont-Kabel | Funkmodul und Motoren anschließen |

## Rechner und Software

| Was | Wofür |
|---|---|
| Rechner mit Bluetooth 4.0 oder neuer | Verbindung zum Auto; entwickelt und getestet auf macOS |
| [Python](https://www.python.org/downloads/) 3.12 | Hauptprogramm, Pakete in `MindFlex_BCI_Projekt/requirements.txt` |
| [Arduino IDE](https://www.arduino.cc/en/software) | Sketches hochladen |
| [Arduino Brain Library](https://github.com/kitschpatrol/Brain) | Sketch für den Uno |
| Adafruit Motor Shield Library **Version 1** (`AFMotor`) | Sketch für das Auto, über den Bibliotheksverwalter der Arduino IDE |
| [Processing](https://processing.org/download) mit ControlP5 | nur für den BrainGrapher |
