# MindFlex BCI – EEG-controlled RC car

**English** | [Deutsch](README.de.md)

A brain-computer interface project: a modified MindFlex EEG headset sends its readings through
an Arduino – over USB or wirelessly via Bluetooth LE. A Python application analyses the signals
in real time, trains several machine-learning models on recorded sessions and turns their
predictions into driving commands for a self-designed, 3D-printed RC car with four motors.

Built as the practical part of a school research paper (*Seminarfacharbeit*) on
*“Brain-computer interfaces – how thoughts control machines”*.

<p align="center">
  <img src="docs/images/hardware-headset-front.jpg" alt="The MindFlex headset from the front with Arduino and 9 V battery" width="640">
</p>

<p align="center">
  <img src="docs/images/car-angle.jpg" alt="The finished RC car with 3D-printed chassis, four motors and an Arduino with motor shield" width="640">
</p>

**[⬇ Download version 3.4](https://github.com/brutterm-gif/mindflex-bci-car/releases/latest)** (release in Moritz Brutter's repo) ·
[Parts list](docs/TEILELISTE.md) · [Wiring](docs/images/wiring.svg) ·
[Architecture](docs/ARCHITEKTUR.md)

> **Team:** A joint project by [@ijafarov7](https://github.com/ijafarov7) and
> [Moritz Brutter (@brutterm-gif)](https://github.com/brutterm-gif). The first version
> (Processing visualisation and Python ML pipeline) was created in this repo in August 2026.
> Moritz extended the project with the hardware (Arduino sketches, Bluetooth LE, 4WD car,
> chassis) and developed the software further to version 3.4, see
> [brutterm-gif/mindflex-bci-car](https://github.com/brutterm-gif/mindflex-bci-car).

> The application interface and the detailed documentation in `docs/` are in German.

## Screenshots

Live prediction with EEG history, eSense values, driving command and model accuracy:

| FORWARD | STOP |
|---|---|
| ![Prediction FORWARD](docs/images/gui-prediction-forward.png) | ![Prediction STOP](docs/images/gui-prediction-stop.png) |

| Calibration | Confusion matrix |
|---|---|
| ![Calibration finished](docs/images/gui-calibration.png) | ![Confusion matrix of the KNN model](docs/images/gui-confusion-matrix.png) |

<details>
<summary><b>More screenshots</b></summary>

| Prediction BACKWARD | Car connected, drive lock off |
|---|---|
| ![Prediction BACKWARD](docs/images/gui-prediction-backward.png) | ![Car connected, drive lock off](docs/images/gui-car-connected.png) |

| Manual driving (W A S D) | Headset without skin contact |
|---|---|
| ![Manual driving](docs/images/gui-manual-drive.png) | ![Headset without skin contact, recording paused](docs/images/gui-no-contact.png) |

| Calibration running | Light mode |
|---|---|
| ![Calibration: block 1 of 18, eyes closed](docs/images/gui-calibration-run.png) | ![Light mode](docs/images/gui-light-mode.png) |

| Training data with recommendations | Single recording as a graph |
|---|---|
| ![Overview of the training data](docs/images/gui-training-data.png) | ![Recording FORWARD as a graph](docs/images/gui-recording-graph.png) |

</details>

## Results

The headset measures with a single electrode on the forehead. That is enough to tell apart
states that show up clearly there – not “thinking left or right”. The car is therefore
controlled with three states you can produce on purpose:

| Command | What you do | Why it works |
|---|---|---|
| STOP | close your eyes | alpha waves rise sharply (Berger effect) |
| FORWARD | open your eyes | alpha drops again |
| BACKWARD | shake your head or clench your jaw | muscle signal picked up by the forehead electrode |

**Accuracy** (one profile, three commands, 12 recordings totalling 285 seconds, evaluated on
held-out whole recordings):

| Method | Accuracy |
|---|---|
| k-nearest neighbours | **85.8 %** |
| Random forest | 85 % |
| Decision tree | 81 % |
| Baseline (always guess the most frequent class) | 48.7 % |

STOP was recognised correctly 90 % of the time, BACKWARD 84 % and FORWARD 81 %. The most common
confusion is FORWARD vs. STOP – mostly right after opening the eyes, because alpha only drops
after 2–3 seconds.

These numbers are for one person and a small amount of data. BACKWARD has only two separate
recordings; the application itself warns that its figure is therefore still uncertain. Models
do not transfer between people; everyone needs their own profile.

## Project structure

```
MindFlex_BCI_Projekt/   Python application: data input (USB/BLE), feature extraction,
                        training and classification (KNN, random forest, decision tree),
                        PyQt5 GUI with calibration, profiles, drive lock and manual driving,
                        car control
  arduino/              sketches for the EEG Arduino and the RC car
BrainGrapher/           Processing sketch for live visualisation of the raw EEG values,
                        extended with a Bluetooth bridge (ble_bridge.py)
BrainGrapher_Python/    port of the BrainGrapher to Python (PyQt5/pyqtgraph)
hardware/               3D model of the chassis (STL)
docs/                   architecture, parts list, images
```

## Origin and own work

`BrainGrapher/` is based on the open-source
[Processing Brain Grapher](https://github.com/kitschpatrol/Processing-Brain-Grapher) by
Eric Mika (MIT licence, see [`BrainGrapher/LICENSE.txt`](BrainGrapher/LICENSE.txt)). It served
as the starting point for reliably receiving and visualising the headset's data. Bluetooth LE
support was added. `BrainGrapher_Python/` is a port of that sketch to Python.

The sketch `mindflex_eeg.ino` is based on the *BrainSerialTest* example of the
[Arduino Brain Library](https://github.com/kitschpatrol/Brain), also by Eric Mika.

`MindFlex_BCI_Projekt/` is original work: the data pipeline (validation, ring buffer, feature
extraction), the training and classification system with automatic selection of the best
model, the GUI with calibration and profiles, the Bluetooth link and the RC car control. The
sketch `rc_car_4wd.ino` and the chassis are original work as well.

## Hardware

All components: [parts list](docs/TEILELISTE.md). Wiring:

![Wiring: headset with Arduino Uno, car with Duemilanove and L293D shield](docs/images/wiring.svg)

1. **Headset and EEG Arduino (Uno):** The MindFlex contains NeuroSky's TGAM module. Two wires
   are soldered to its T pin and GND and lead to the Arduino. Using the
   [Arduino Brain Library](https://github.com/kitschpatrol/Brain), the Uno outputs one CSV line
   per second (SignalQuality, Attention, Meditation, Delta, Theta, LowAlpha, HighAlpha, LowBeta,
   HighBeta, LowGamma, HighGamma) – over USB or a BLE module. Arduino and radio sit directly on
   the headset, powered by a switched 9 V battery.

| Headset mit Arduino · Headset with Arduino | Vorderansicht · Front view |
|---|---|
| ![MindFlex headset with Arduino Uno and BLE module attached](docs/images/hardware-headset-arduino.jpg) | ![MindFlex headset from the front](docs/images/hardware-headset-front.jpg) |
| **Geöffnet: TGAM-Modul mit angelöteten Drähten · Opened: TGAM module with soldered wires** | **Rückseite: 9-V-Block und Ohrclip · Back: 9 V battery and ear clip** |
| ![Opened headset: green NeuroSky TGAM board with wires soldered on, leading to the Arduino](docs/images/hardware-headset-open.jpg) | ![Back of the headset with the 9 V battery box for the Arduino and the ear clip reference electrode](docs/images/hardware-headset-back.jpg) |

2. **RC car (Arduino Duemilanove):** receives single-character commands (`F`/`B`/`L`/`R`/`S`)
   over Bluetooth LE and drives four motors through an L293D motor shield. It steers like a
   tank: to turn, the two sides run in opposite directions. Power comes from a 9 V battery
   that sits inside the chassis below the Arduino.

| Seite mit 9-V-Block · Side with 9 V battery | Von oben · Top | Seite mit USB-Anschluss · Side with USB port |
|---|---|---|
| ![RC car, side with the 9 V battery inside the chassis](docs/images/car-side-battery.jpg) | ![RC car from above: motor shield and wiring of the four motors](docs/images/car-top.jpg) | ![RC car, side with the Arduino's USB port](docs/images/car-side-usb.jpg) |

| Chassis | Technische Zeichnung · Technical drawing |
|---|---|
| ![Chassis](docs/images/chassis-car.png) | ![Dimensioned drawing of the chassis](docs/images/chassis-car-drawing.png) |

The chassis is included as [`hardware/chassis_car.stl`](hardware/chassis_car.stl) and can be
printed directly.

## Quick start

**Requirements:** Python 3.12, the [Arduino IDE](https://www.arduino.cc/en/software) with the
[Arduino Brain Library](https://github.com/kitschpatrol/Brain) and the Adafruit Motor Shield
Library v1, optionally [Processing](https://processing.org/download) for `BrainGrapher/`.

1. Upload `MindFlex_BCI_Projekt/arduino/mindflex_eeg/mindflex_eeg.ino` to the EEG Arduino and
   `MindFlex_BCI_Projekt/arduino/rc_car_4wd/rc_car_4wd.ino` to the car's Arduino.
2. Start the Python application:

```bash
cd MindFlex_BCI_Projekt
pip install -r requirements.txt
python main.py
```

On a Mac you can also double-click `start.command`, on Windows `start.bat`. Setting up
Bluetooth, creating a profile and calibrating are described (in German) in
[`MindFlex_BCI_Projekt/README.md`](MindFlex_BCI_Projekt/README.md).

For signal visualisation only, open `BrainGrapher/BrainGrapher.pde` in the Processing IDE
(requires the ControlP5 library, available via *Sketch → Import Library → Add Library*).

## Data flow

![MindFlex BCI pipeline](docs/images/pipeline.svg)

## Privacy

Training profiles and EEG recordings of test participants are not part of this repository.
Everyone creates and calibrates their own profile in the application; the data stays local in
the `profile/` folder.

## Licence

- `MindFlex_BCI_Projekt/`, `hardware/`, `docs/`: MIT, see [`LICENSE`](LICENSE)
- `BrainGrapher/`, `BrainGrapher_Python/`: MIT, Copyright (c) 2010-2025 Eric Mika, see
  [`BrainGrapher/LICENSE.txt`](BrainGrapher/LICENSE.txt); extensions MIT, see [`LICENSE`](LICENSE)
