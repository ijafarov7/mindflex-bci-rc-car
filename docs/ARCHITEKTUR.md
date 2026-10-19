# Architektur

Wie das System aufgebaut ist, warum die Bauteile so gewählt wurden und was man über die
Grenzen wissen sollte. Stand: Software-Version 3.4.

**Inhalt**

1. [Überblick](#1-überblick)
2. [Hardware](#2-hardware)
3. [Arduino-Sketches](#3-arduino-sketches)
4. [Python-Anwendung](#4-python-anwendung)
5. [Training, Kalibrierung und Bewertung](#5-training-kalibrierung-und-bewertung)
6. [Bluetooth-Strecke](#6-bluetooth-strecke)
7. [Sicherheit](#7-sicherheit)
8. [Bauteilwahl im Vergleich](#8-bauteilwahl-im-vergleich)
9. [Grenzen](#9-grenzen)
10. [Fehlerbilder und Lösungen](#10-fehlerbilder-und-lösungen)

---

## 1. Überblick

```
MindFlex-Headset ──T-Pin──▶ Arduino Uno ──USB oder BLE──▶ Python (Mac)
                                                            │
                     Empfang → Vorverarbeitung → Merkmale → Klassifikation → GUI
                                                            │
RC-Auto ◀── L293D-Shield ◀── Arduino Duemilanove ◀──BLE─────┘  F / B / L / R / S
```

Zwei Arduinos, weil die beiden Aufgaben nichts miteinander zu tun haben: Der eine liest nur das
Headset aus, der andere setzt nur Fahrbefehle in Motorbewegung um. Die eigentliche Arbeit –
Merkmale berechnen, Modelle trainieren, vorhersagen – läuft in Python auf dem Rechner, denn
scikit-learn gibt es nicht für Mikrocontroller. So lassen sich alle drei Teile einzeln testen.

---

## 2. Hardware

### 2.1 EEG-Seite

| Bauteil | Aufgabe |
|---|---|
| MindFlex-Headset (Mattel) | Trockenelektrode auf der Stirn (Position Fp1), Referenz über Ohrclip |
| NeuroSky **TGAM**-Modul im Headset | ThinkGear-Chip: filtert das Rohsignal und liefert einmal pro Sekunde Signalqualität, Attention, Meditation und acht Bandleistungen |
| Arduino Uno (hier ein Funduino-Nachbau) | liest den T-Pin des TGAM über RX, gibt CSV-Zeilen aus |
| BLE-Modul CC2541 (HM-10-Klon), optional | funkt die CSV-Zeilen statt über USB |
| 9-V-Block mit Schalter | versorgt den Uno über den Rundstecker, wenn kein USB-Kabel steckt |

Verkabelung:

```
TGAM T-Pin   → Uno Pin 0 (RX)
TGAM GND     → Uno GND

nur für Funk:
BLE VCC      → 3,3V
BLE GND      → GND
BLE RXD      → Pin 1 (TX)
BLE TXD      → nicht angeschlossen
```

Alle Verbindungen sind direkt, ohne Widerstände – so ist das Headset aufgebaut und getestet.
Die Bibliothek liest den Chip und schreibt die fertigen Zeilen auf derselben Hardware-
Schnittstelle – Pin 0 ist Eingang, Pin 1 Ausgang, das stört sich nicht. Das Funkmodul hört
einfach mit, was ohnehin an Pin 1 hinausgeht.

### 2.2 Auto-Seite

| Bauteil | Aufgabe |
|---|---|
| Arduino Duemilanove (ATmega328P) | empfängt Fahrbefehle, steuert das Shield |
| L293D-Motor-Shield (HW-130) | vier H-Brücken, angesteuert über ein Schieberegister (74HC595) |
| 4 TT-Getriebemotoren mit Rädern | Allradantrieb, Lenkung wie bei einem Panzer |
| BLE-Modul CC2541 (HM-10-Klon) | Funkempfang |
| 9-V-Block | Stromversorgung des Autos |
| 3D-gedrucktes Chassis | [`hardware/chassis_car.stl`](../hardware/chassis_car.stl) |

Belegung am Shield:

```
            vorne
   M2 (links)   M1 (rechts)
   M3 (links)   M4 (rechts)
            hinten
```

Funkmodul am Duemilanove:

```
BLE TXD → Pin 10   (SoftwareSerial RX)
BLE RXD → Pin A0   (nur für den Rückkanal nötig)
BLE VCC → 3,3V,  GND → GND
```

Auch hier ohne Widerstände. Der Duemilanove hat keinen eigenen 3,3-V-Regler; die 3,3 V
kommen aus seinem USB-Chip FT232RL (höchstens etwa 50 mA). Für das Funkmodul reicht das.

**Nicht Pin 11 verwenden.** Viele Anleitungen legen das Funkmodul auf `SoftwareSerial(10, 11)`,
aber auf dem HW-130 ist Pin 11 der PWM-Ausgang von Motor 1. Belegt sind durch das Shield:

| Pins | Verwendung |
|---|---|
| 3, 5, 6, 11 | PWM für M2, M3, M4, M1 |
| 4, 7, 8, 12 | Schieberegister für alle Motoren |
| 9, 10 | Servo-Anschlüsse, frei solange kein Servo steckt |
| 2, 13, A0–A5 | frei |

Eine Übersicht als Bild: [`docs/images/wiring.svg`](images/wiring.svg). Die Einkaufsliste steht
in [`docs/TEILELISTE.md`](TEILELISTE.md).

---

## 3. Arduino-Sketches

Alle Sketches liegen unter [`MindFlex_BCI_Projekt/arduino/`](../MindFlex_BCI_Projekt/arduino).

### `mindflex_eeg.ino` (Uno)

- beruht auf dem Beispiel *BrainSerialTest* der
  [Arduino Brain Library](https://github.com/kitschpatrol/Brain)
- 9600 Baud, eine Zeile pro Sekunde:
  `SignalQuality,Attention,Meditation,Delta,Theta,LowAlpha,HighAlpha,LowBeta,HighBeta,LowGamma,HighGamma`
- `SignalQuality` zählt andersherum: 0 ist perfekter Kontakt, 200 gar keiner
- sendet zusätzlich alle 5 s eine Zeile `BAT,...` mit der Versorgungsspannung; die Python-Seite
  verwirft sie stillschweigend
- **Beim Hochladen** das Headset ausschalten oder die Leitung an Pin 0 abziehen, sonst stört
  der Datenstrom den Upload

### `rc_car_4wd.ino` (Duemilanove)

- braucht die *Adafruit Motor Shield Library* in **Version 1** (`AFMotor`)
- Befehle über Bluetooth oder den Seriellen Monitor:

| Zeichen | Wirkung |
|---|---|
| `F` | alle vier Räder vorwärts (Geschwindigkeit 200 von 255) |
| `B` | alle vier Räder rückwärts |
| `L` | links rückwärts, rechts vorwärts – dreht auf der Stelle nach links (255) |
| `R` | links vorwärts, rechts rückwärts – dreht nach rechts |
| `S` | Stopp |
| `1`–`4` | nur diesen Motor kurz laufen lassen |
| `T` | alle vier Motoren nacheinander testen |

- Drehen bekommt volle Kraft, weil die Räder dabei seitlich über den Boden schleifen
- jeder Motor hat einen eigenen Schalter `INVERT[]`, falls er verkehrt herum läuft; zum
  Einstellen das Auto aufbocken und mit `T` prüfen
- **Watchdog:** kommt 1 s lang kein Befehl, stoppen alle Motoren (siehe [Sicherheit](#7-sicherheit))

### `rc_car_test.ino`

Test ohne Motoren: zeigt im Seriellen Monitor, welcher Befehl über welchen Weg ankam und
welche Pegel die Motoren bekommen würden. Pin 13 leuchtet, solange ein Fahrbefehl aktiv ist.

---

## 4. Python-Anwendung

Ordner [`MindFlex_BCI_Projekt/`](../MindFlex_BCI_Projekt). Start mit `python main.py`.

### 4.1 Module

| Modul | Aufgabe |
|---|---|
| `main.py` | Einstieg, verdrahtet alles; Modi `run` (GUI), `train` (Konsole), `retrain` |
| `config.py` | alle Einstellungen an einer Stelle, überschreibbar per Umgebungsvariable |
| `serial_receiver.py` | liest CSV-Zeilen per USB, sucht den Port selbst, verbindet neu bei Abbruch |
| `ble_serial_receiver.py` | dasselbe über Bluetooth (bleak) |
| `brain_processor.py` | prüft die Signalqualität, hält die letzten 60 s in einem Ringpuffer |
| `feature_extractor.py` | berechnet 33 Merkmale aus einem 5-s-Fenster |
| `classifier.py` | bewertet drei Verfahren, speichert das beste, sagt vorher |
| `trainer.py` | nimmt Trainingsdaten auf, schreibt `training_data.csv` |
| `ble_car_controller.py` | sendet Fahrbefehle per Bluetooth, mit Wiederholung |
| `car_controller.py` | dasselbe per USB-Kabel, zum Testen |
| `visualizer.py`, `gui_*.py` | Oberfläche (PyQt5 + pyqtgraph) |
| `ble_scan.py`, `ble_test.py`, `ble_remote.py` | Hilfsprogramme: Module finden, Befehle testen, Auto per Tastatur fahren |

### 4.2 Datenfluss

1. **Empfang:** Jede Zeile wird in ein `EEGSample` zerlegt. Alpha, Beta und Gamma werden aus
   den Low- und High-Werten gemittelt. Zeilen, die durch ein verlorenes Funkpaket zerrissen
   sind, und `BAT`-Zeilen werden verworfen.
2. **Vorverarbeitung:** Ein Sample mit Signalqualität 200 („kein Kontakt“) aktualisiert nur die
   Verbindungsanzeige – keine Graphen, keine Vorhersage, kein Fahrbefehl. Gültige Samples landen
   im Ringpuffer. Kommt 3 s lang gar nichts mehr, gilt das Signal als tot.
3. **Merkmale:** Für sieben Größen (Attention, Meditation, Delta, Theta, Alpha, Beta, Gamma)
   je Mittelwert, Maximum, Minimum und Standardabweichung, dazu Beta/Alpha-Verhältnis, Änderung
   von Attention und Meditation und deren Mittel der letzten 5 s – zusammen 33 Werte. Training
   und Live-Betrieb benutzen exakt dieselbe Berechnung in derselben Reihenfolge.
4. **Vorhersage:** einmal pro Sekunde über das jüngste 5-s-Fenster; Ergebnis ist ein Befehl und
   eine Konfidenz.
5. **Fahren:** Das Label wird in ein Zeichen übersetzt (`STOP`→`S`, `FORWARD`→`F`, …) und nur
   gesendet, wenn die Fahrsperre aus ist.

### 4.3 Oberfläche

- Statuszeile: Signalqualität des Headsets in Prozent, Paketzähler, Verbindung zum Auto,
  Knopf „Neu verbinden“, Umschalter hell/dunkel
- EEG-Verlauf (logarithmische Skala) und eSense-Werte, Kacheln je Band, Aufzeichnung anhalten
- Fahren: Vorhersage mit Pfeil und Konfidenz, **Fahrsperre** (Standard: an), **Handbetrieb** mit
  W A S D oder Pfeiltasten
- Aufnehmen: Training, Kalibrierung, einfache Aufnahmen; Dialoge für Trainingsdaten, Rohdaten
  und die Verwechslungsmatrix
- Modell: Genauigkeit aller drei Verfahren und die Basisrate
- Profile: jede Person hat einen eigenen Ordner `profile/<name>/` mit `training_data.csv`,
  `trained_model.pkl` und `kalibrierung.json`

### 4.4 Wichtige Umgebungsvariablen

| Variable | Bedeutung | Standard |
|---|---|---|
| `MINDFLEX_EEG_TRANSPORT` | `seriell` oder `ble` | `seriell` |
| `MINDFLEX_EEG_PORT` | fester Port des Uno | automatisch |
| `MINDFLEX_EEG_BLE_ADDRESS` | Adresse des Headset-Funkmoduls | in `config.py` |
| `MINDFLEX_CAR_TRANSPORT` | `ble` oder `serial` | `ble` |
| `MINDFLEX_CAR_BLE_ADDRESS` | Adresse des Auto-Funkmoduls | in `config.py` |
| `MINDFLEX_CAR_PORT` | Port des Duemilanove bei `serial` | automatisch |
| `MINDFLEX_PROFIL` | Profil beim Start | `standard` |
| `MINDFLEX_ACTIVE_LABELS` | trainierte Befehle, z. B. `STOP,FORWARD,BACKWARD` | `STOP,FORWARD,BACKWARD` |

Unter macOS sind die Bluetooth-Adressen CoreBluetooth-UUIDs, die **auf jedem Rechner anders**
sind. Auf einem neuen Rechner mit `python3 ble_scan.py` neu ermitteln – dabei jeweils nur ein
Modul einschalten, denn beide bewerben denselben Dienst.

---

## 5. Training, Kalibrierung und Bewertung

### 5.1 Daten sammeln

- **Training:** Befehl wählen, Dauer wählen, aufnehmen. Jede Aufnahme wird ein eigener Block.
- **Kalibrierung:** ein fester Plan, in dem jede mögliche Reihenfolge der Befehle genau einmal
  vorkommt – bei drei Befehlen sechs Reihenfolgen, also 18 Blöcke à 15 s. So steht jeder Befehl
  gleich oft am Anfang und am Ende und folgt jedem anderen gleich oft; Ermüdung und
  Nachwirkungen verteilen sich gleichmäßig. Vor jedem Block sagt der Mac die Anweisung an, dann
  folgt eine nicht aufgezeichnete Wechselzeit von 3 s (Alpha fällt nach dem Augenöffnen erst
  nach 2–3 s ab), dann die Aufnahme, dann ein Endton.
  Damit lässt sie sich auch mit geschlossenen Augen befolgen. Die Ansage nutzt `say` und
  `afplay` und funktioniert deshalb nur auf dem Mac.
- Die Anweisungen sind je Profil einstellbar. Voreingestellt:

| Befehl | Anweisung | Was das Headset sieht |
|---|---|---|
| STOP | Augen zu | Alpha steigt deutlich (Berger-Effekt) |
| FORWARD | Augen auf | Alpha fällt |
| BACKWARD | Kopf schütteln oder Kiefer zusammenbeißen | Muskel- und Bewegungsartefakte in Beta/Gamma |

### 5.2 Modelle

Verglichen werden ein Entscheidungsbaum, ein Random Forest (100 Bäume) und k-nächste Nachbarn
(k = 5, mit vorgeschalteter Standardisierung in einer Pipeline, damit die großen Bandwerte die
Abstände nicht dominieren). Das beste Verfahren wird anschließend auf allen Daten neu trainiert
und im Profil gespeichert.

### 5.3 Ehrlich bewerten

Aus einer Aufnahme entstehen viele Fenster, die sich fast vollständig überlappen. Ein
Zufallssplit über einzelne Fenster würde praktisch dieselben Daten im Training und im Test
haben und viel zu gute Zahlen liefern.

Deshalb gilt:

- Bewertet wird mit `StratifiedGroupKFold`: Eine **ganze Aufnahme** liegt entweder im Training
  oder im Test. Dafür braucht jede Klasse mindestens zwei getrennte Aufnahmen; sonst fällt das
  Programm auf einen Zufallssplit zurück und warnt deutlich.
- Neben jeder Genauigkeit steht die **Basisrate** – wie gut man wäre, wenn man immer die
  häufigste Klasse rät.
- Die **Verwechslungsmatrix** beruht auf denselben zurückgehaltenen Aufnahmen.

Ergebnisse stehen in der [README](../README.md#ergebnisse).

---

## 6. Bluetooth-Strecke

Beide Funkmodule sind CC2541-Module (HM-10-Familie) mit **Bluetooth Low Energy**, kein
klassisches Bluetooth wie beim HC-05. Das hat Folgen:

- Sie erscheinen weder in den Bluetooth-Einstellungen von macOS noch als serieller Port.
- Python spricht sie über GATT an (Bibliothek `bleak`): Dienst `FFE0`, Charakteristik `FFE1`.
  Was man in die Charakteristik schreibt, gibt das Modul an seinem TXD-Pin aus – und umgekehrt.
- Processing kann kein BLE. Für den BrainGrapher gibt es deshalb `ble_bridge.py`, das die
  Zeilen über TCP (127.0.0.1:5204) an den Sketch weiterreicht.
- Ein Modul akzeptiert nur eine Verbindung gleichzeitig. Läuft `ble_bridge.py`, kann
  `main.py` nicht zum Headset verbinden.

---

## 7. Sicherheit

Ein Auto, das auf veralteten Daten weiterfährt, ist das größte Risiko. Darum mehrere Stufen:

| Stufe | Wo | Wirkung |
|---|---|---|
| Fahrsperre | GUI | beim Start an; Vorhersagen werden angezeigt, aber nicht gesendet |
| Kein Kontakt | Python | bei Signalqualität 200 keine Vorhersage und kein Fahrbefehl |
| Signal-Timeout | Python | 3 s ohne Paket → Signal gilt als tot |
| Befehlswiederholung | Python | der aktuelle Befehl wird alle 0,4 s wiederholt |
| Watchdog | Arduino | 1 s ohne Befehl → Motoren aus |
| Handbetrieb | GUI | fährt nur, solange die Taste gedrückt ist; Loslassen oder Fensterwechsel → Stopp |

Die Wiederholung und der Watchdog gehören zusammen: Python sendet einen Befehl sonst nur bei
Änderung. Ohne Wiederholung bliebe das Auto bei dauerhaftem FORWARD nach einer Sekunde stehen;
ohne Watchdog führe es nach einem Funkabbruch einfach weiter.

---

## 8. Bauteilwahl im Vergleich

### Headset

| | MindFlex | Muse | Emotiv EPOC | OpenBCI |
|---|---|---|---|---|
| Kanäle | 1 (Stirn) | 4 | 14 | 8–16 |
| Rohdaten | nein, nur Bandleistungen | ja | ja | ja |
| Preis | Spielzeug, gebraucht günstig | mittel | hoch | hoch |

Das MindFlex war verfügbar und günstig, und die Arduino Brain Library liest es ohne Aufwand
aus. Der Preis dafür: eine einzige Elektrode und eine Blackbox, die nur fertig berechnete
Werte liefert (siehe [Grenzen](#9-grenzen)).

### Motortreiber

Das L293D-Shield (HW-130) treibt vier Motoren getrennt (600 mA Dauerstrom je Kanal, 1,2 A
Spitze) und wird einfach aufgesteckt. Ein L298N-Modul kann mehr Strom, hat aber nur zwei
Kanäle – für vier Motoren bräuchte man zwei Module und deutlich mehr Verkabelung.

### Stromversorgung

Headset und Auto laufen jeweils mit einem 9-V-Block – leicht, überall erhältlich und schnell
gewechselt. Am Headset hängen nur der Uno und ein Funkmodul, dafür reicht er locker. Im Auto
treibt er zusätzlich vier Motoren an. Ein 9-V-Block liefert nur wenige hundert Milliampere; wenn
alle Motoren gleichzeitig anlaufen, sinkt seine Spannung, und die Fahrzeit ist begrenzt. Wer mehr
Kraft oder längere Fahrten braucht, kann auf AA-Zellen oder einen Akku umsteigen.

### Vier Motoren statt zwei

Mit vier einzeln angesteuerten Motoren braucht das Auto kein Lenkservo: Zum Drehen laufen die
beiden Seiten gegenläufig. Das nutzt alle vier Kanäle des Shields und ist auf unebenem Boden
stabiler.

---

## 9. Grenzen

- **Eine Elektrode auf der Stirn.** Die Vorstellung von Links- und Rechtsbewegungen entsteht
  über dem motorischen Kortex (C3/C4), dort misst das Headset nicht. LEFT und RIGHT sind deshalb
  im Code vorhanden, aber nicht aktiv.
- **Was zuverlässig funktioniert, ist physiologisch gut begründet:** Augen zu gegen Augen auf
  (Berger-Effekt). BACKWARD beruht auf einem Muskelsignal – kein EEG im engeren Sinn, aber
  bewusst steuerbar.
- **Attention und Meditation** sind Werte des Herstellers mit unbekannter Berechnung, keine
  Messgrößen.
- **Modelle sind persönlich.** Ein Modell funktioniert praktisch nur für die Person, mit deren
  Daten es trainiert wurde. Darum die Profile.
- **Trockenelektrode:** Haare, Schweiß und Bewegung verschlechtern den Kontakt.
- **Verzögerung:** Das 5-s-Fenster und die Vorhersage im Sekundentakt machen das Auto träge.
  Eine genaue Messung der Latenz steht noch aus.

---

## 10. Fehlerbilder und Lösungen

| Symptom | Ursache | Lösung |
|---|---|---|
| Upload auf den Uno schlägt fehl | Das Headset sendet in Pin 0 | Headset aus oder RX-Leitung abziehen |
| `Kein serieller Port gefunden` | Der Duemilanove meldet sich als „FT232R USB UART“, nicht als „Arduino“ | Port fest setzen: `MINDFLEX_CAR_PORT=...` |
| Auto fährt eine Sekunde und bleibt stehen | Watchdog greift, weil der Befehl nur bei Änderung gesendet wurde | Befehlswiederholung alle 0,4 s (ist eingebaut) |
| Fahrbefehle kommen nicht an | Funkmodul an der Hardware-UART statt an SoftwareSerial | Modul an Pin 10 / A0, siehe oben |
| Motor 1 und Bluetooth stören sich | Funkmodul an Pin 11 = PWM von Motor 1 | Pin A0 statt 11 |
| Ein Rad dreht falsch herum | Motor gespiegelt eingebaut oder verkehrt angeklemmt | `INVERT[]` im Sketch, mit `T` prüfen |
| „BLE verbindet nicht“ | `MINDFLEX_EEG_PORT` erzwingt den Kabelweg | Für Funk `MINDFLEX_EEG_TRANSPORT=ble` setzen und `MINDFLEX_EEG_PORT` weglassen |
| Headset per Funk nicht erreichbar | `ble_bridge.py` hält die Verbindung | dort mit Strg-C beenden |
| Genauigkeit 100 % | zu wenige getrennte Aufnahmen, Zufallssplit | je Befehl mindestens drei, besser fünf getrennte Aufnahmen |
