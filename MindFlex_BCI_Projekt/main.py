"""
main.py
=======

Einstiegspunkt der MindFlex-BCI-Anwendung.

Verdrahtet alle Module miteinander:

    serial_receiver.SerialReceiver  (liest EEG-Arduino)
            |
            v
    brain_processor.BrainProcessor  (validiert, puffert)
            |
            +--> trainer.TrainingSession   (Trainingsmodus, CSV)
            |
            +--> classifier.BCIClassifier  (Training/Vorhersage)
            |
            +--> visualizer.MainWindow     (PyQt5-GUI)
                        |
                        v
                car_controller.CarController (RC-Auto-Arduino)

Aufruf::

    python main.py            # startet die grafische Oberflaeche (Echtzeitbetrieb)
    python main.py --mode train   # startet den interaktiven Konsolen-Trainingsmodus
    python main.py --mode retrain # trainiert die Modelle einmalig neu und beendet sich

Beide Arduino-Verbindungen (EEG-Empfang und RC-Auto-Ansteuerung) sind
unabhaengig voneinander: Fehlt das RC-Auto (z. B. beim reinen Trainieren
oder Testen), laeuft der Rest der Anwendung trotzdem weiter -- es werden
dann lediglich keine Fahrbefehle gesendet.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from ble_car_controller import BleCarController
from brain_processor import BrainProcessor
from car_controller import CarController
from classifier import BCIClassifier
from config import CONFIG
from ble_serial_receiver import BleEegReceiver
from serial_receiver import SerialReceiver
from trainer import TrainingSession, run_cli
from utils import setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parst die Kommandozeilenargumente.

    Args:
        argv: Argumentliste (Default: ``sys.argv[1:]``).

    Returns:
        Geparste Argumente mit Attribut ``mode``.
    """
    parser = argparse.ArgumentParser(description="MindFlex BCI - EEG-gesteuertes RC-Auto")
    parser.add_argument(
        "--mode",
        choices=["run", "train", "retrain"],
        default="run",
        help=(
            "run: grafische Oberflaeche mit Echtzeit-Vorhersage (Standard). "
            "train: interaktiver Konsolen-Trainingsmodus. "
            "retrain: Modelle einmalig aus training_data.csv neu trainieren und beenden."
        ),
    )
    return parser.parse_args(argv)


def _make_eeg_receiver() -> BleEegReceiver | SerialReceiver:
    """Waehlt den Uebertragungsweg vom EEG-Arduino zum Rechner.

    Standard ist das USB-Kabel. Mit ``MINDFLEX_EEG_TRANSPORT=ble`` laeuft
    die Uebertragung stattdessen ueber ein BLE-Modul am Sendepin des
    Arduino -- dann haengt das Headset an keinem Kabel mehr. Die Adresse
    des Moduls muss dafuer ueber ``MINDFLEX_EEG_BLE_ADDRESS`` gesetzt
    sein, weil sich die beiden Funkmodule des Projekts sonst nicht
    auseinanderhalten lassen.

    Returns:
        Ein noch nicht gestarteter Empfaenger.
    """
    if os.environ.get("MINDFLEX_EEG_TRANSPORT", "seriell").lower() == "ble":
        logger.info("EEG-Anbindung: Bluetooth Low Energy.")
        return BleEegReceiver()
    logger.info("EEG-Anbindung: seriell (USB-Kabel).")
    return SerialReceiver(CONFIG.eeg_serial)


def _make_car_controller() -> BleCarController | CarController:
    """Waehlt den Uebertragungsweg zum RC-Auto.

    Standard ist BLE, weil das Funkmodul am Auto-Arduino ein CC2541
    (HM-10-Familie) ist und sich nicht als serieller Port ansprechen
    laesst. Mit ``MINDFLEX_CAR_TRANSPORT=serial`` wird stattdessen die
    serielle Verbindung benutzt -- nuetzlich, wenn der Auto-Arduino zum
    Testen per USB-Kabel am Rechner haengt.

    Returns:
        Ein noch nicht verbundener Controller.
    """
    if os.environ.get("MINDFLEX_CAR_TRANSPORT", "ble").lower() == "serial":
        logger.info("RC-Auto-Anbindung: seriell (USB-Kabel).")
        return CarController()
    logger.info("RC-Auto-Anbindung: Bluetooth Low Energy.")
    return BleCarController()


def _run_gui(processor: BrainProcessor, classifier: BCIClassifier, training_session: TrainingSession) -> int:
    """Startet die PyQt5-Oberflaeche fuer den Echtzeitbetrieb.

    Args:
        processor: Laufender ``BrainProcessor``.
        classifier: ``BCIClassifier`` mit ggf. bereits geladenem Modell.
        training_session: ``TrainingSession`` fuer den "Training starten"-Button.

    Returns:
        Exit-Code der Qt-Anwendung.
    """
    # Import erst hier, damit "--mode train" ohne installiertes PyQt5/pyqtgraph
    # funktioniert (z. B. auf einem Kopf-losen Trainings-Rechner).
    from PyQt5 import QtWidgets

    from visualizer import MainWindow

    car_controller = _make_car_controller()
    car_controller.connect()

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(processor, classifier, car_controller, training_session)
    window.show()

    _beenden_per_signal_ermoeglichen(app, window)

    return app.exec_()


def _beenden_per_signal_ermoeglichen(app, window) -> None:
    """Laesst sich das Programm von aussen sauber beenden.

    Ohne das hier wuerde ein ``kill`` den Prozess sofort abraeumen, und
    ``closeEvent`` im Fenster kaeme nie zum Zug -- das RC-Auto bekaeme
    also kein STOPP mehr und die Funkverbindung bliebe offen haengen. Mit
    der Behandlung nimmt ein Signal denselben Weg wie das Schliessen des
    Fensters.

    Das funktioniert, weil die Oberflaeche ohnehin im Takt eines QTimer
    laeuft: Python-Signalbehandlungen greifen nur, wenn zwischendurch
    Python-Code ausgefuehrt wird, und genau das tut der Timer.

    Von aussen beenden:

        pkill -TERM -f MindFlex_BCI_Projekt/main.py

    Args:
        app: Die laufende ``QApplication``.
        window: Das Hauptfenster, dessen ``closeEvent`` aufraeumen soll.
    """

    def behandeln(signalnummer: int, _rahmen) -> None:
        logger.info("Signal %s empfangen -- Programm wird beendet.", signalnummer)
        window.close()
        app.quit()

    # SIGINT ist Strg+C im Terminal, SIGTERM das freundliche kill.
    signal.signal(signal.SIGINT, behandeln)
    signal.signal(signal.SIGTERM, behandeln)

    logger.info(
        "Beenden von aussen: pkill -TERM -f MindFlex_BCI_Projekt/main.py"
    )


def main(argv: list[str] | None = None) -> int:
    """Hauptfunktion: initialisiert alle Komponenten und startet den gewaehlten Modus.

    Args:
        argv: Kommandozeilenargumente (ohne Programmname).

    Returns:
        Exit-Code des Prozesses (0 = Erfolg).
    """
    args = _parse_args(argv)

    receiver = _make_eeg_receiver()
    processor = BrainProcessor(receiver)
    training_session = TrainingSession(processor)
    classifier = BCIClassifier()

    # Automatisches Laden des letzten Modells, falls vorhanden.
    if classifier.load_model():
        logger.info("Vorheriges Modell geladen: %s", classifier.last_accuracies)
    else:
        logger.info("Kein gespeichertes Modell vorhanden -- bitte zuerst trainieren.")

    exit_code = 0

    try:
        receiver.start()

        if args.mode == "train":
            run_cli(processor)
        elif args.mode == "retrain":
            accuracies = classifier.train()
            if accuracies:
                print("Trainingsergebnis:")
                for model_name, accuracy in sorted(accuracies.items(), key=lambda kv: -kv[1]):
                    print(f"  {model_name}: {accuracy * 100:.1f} %")
            else:
                print("Nicht genug Trainingsdaten in training_data.csv.")
        else:
            exit_code = _run_gui(processor, classifier, training_session)
    except KeyboardInterrupt:
        logger.info("Programm durch Benutzer abgebrochen (Strg+C).")
    except Exception:  # noqa: BLE001 - letzte Sicherheitsnetz, Programm darf nie abstuerzen
        logger.exception("Unerwarteter Fehler im Hauptprogramm.")
        exit_code = 1
    finally:
        receiver.stop()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
