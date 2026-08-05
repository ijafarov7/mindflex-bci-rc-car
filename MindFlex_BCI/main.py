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
import sys

from brain_processor import BrainProcessor
from car_controller import CarController
from classifier import BCIClassifier
from config import CONFIG
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

    car_controller = CarController()
    car_controller.connect()

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(processor, classifier, car_controller, training_session)
    window.show()
    return app.exec_()


def main(argv: list[str] | None = None) -> int:
    """Hauptfunktion: initialisiert alle Komponenten und startet den gewaehlten Modus.

    Args:
        argv: Kommandozeilenargumente (ohne Programmname).

    Returns:
        Exit-Code des Prozesses (0 = Erfolg).
    """
    args = _parse_args(argv)

    receiver = SerialReceiver(CONFIG.eeg_serial)
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
