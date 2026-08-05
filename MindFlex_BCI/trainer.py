"""
trainer.py
==========

Interaktiver Trainingsmodus: Der Benutzer waehlt einen Fahrbefehl
(FORWARD/BACKWARD/LEFT/RIGHT/STOP), denkt fuer eine festgelegte Dauer
an den entsprechenden mentalen Zustand, und alle in dieser Zeit
empfangenen (gueltigen) EEG-Rohwerte werden mit diesem Label versehen
in ``training_data.csv`` gespeichert.

Dieses Modul kuemmert sich NUR um das Aufzeichnen roher, gelabelter
Zeilen. Die Umwandlung in ML-Features (gleitende Fenster, Statistiken)
passiert erst in ``classifier.py`` beim Trainieren der Modelle -- so
bleibt die CSV-Datei einfach lesbar und unabhaengig von der gewaehlten
Fenstergroesse.

Kann sowohl interaktiv per Konsole (``run_cli``) als auch programmatisch
(z. B. von der GUI in ``visualizer.py`` ueber ``record_label``)
verwendet werden.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable

from brain_processor import BrainProcessor
from config import CONFIG, TRAINING_CSV_COLUMNS
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

# Menu-Zuordnung fuer den interaktiven CLI-Trainingsmodus.
_MENU: dict[str, str] = {
    "1": "FORWARD",
    "2": "BACKWARD",
    "3": "LEFT",
    "4": "RIGHT",
    "5": "STOP",
}


class TrainingSession:
    """Kapselt das Aufzeichnen von gelabelten Trainingsdaten.

    Attributes:
        processor: ``BrainProcessor``, aus dem aktuelle EEG-Samples
            gelesen werden.
        csv_path: Pfad zur Trainings-CSV-Datei.
    """

    def __init__(
        self,
        processor: BrainProcessor,
        csv_path: Path | None = None,
    ) -> None:
        """Initialisiert die Trainingssession.

        Args:
            processor: Bereits laufender ``BrainProcessor``, der die
                serielle Verbindung im Hintergrund konsumiert.
            csv_path: Zielpfad fuer die CSV-Datei. Default aus
                ``CONFIG.paths.training_csv``.
        """
        self.processor = processor
        self.csv_path = csv_path if csv_path is not None else CONFIG.paths.training_csv
        self._ensure_csv_header()

    def _ensure_csv_header(self) -> None:
        """Legt die CSV-Datei mit Header an, falls sie noch nicht existiert."""
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(TRAINING_CSV_COLUMNS)
            logger.info("Neue Trainingsdatei angelegt: %s", self.csv_path)

    @safe_execute(logger, default=0)
    def record_label(
        self,
        label: str,
        duration: float | None = None,
        on_tick: Callable[[float, int], None] | None = None,
    ) -> int:
        """Zeichnet fuer ``duration`` Sekunden gelabelte EEG-Rohdaten auf.

        Nur Samples mit gueltigem Signal (Signal Quality != 200) werden
        gespeichert -- passend zur Anforderung, dass bei fehlendem
        Kontakt keine Daten verarbeitet werden.

        Args:
            label: Fahrbefehl-Label, muss in ``CONFIG.labels`` enthalten
                sein.
            duration: Aufnahmedauer in Sekunden. Default aus
                ``CONFIG.default_training_duration``.
            on_tick: Optionaler Callback ``(elapsed_seconds,
                samples_recorded)``, der einmal pro Sekunde aufgerufen
                wird -- z. B. um in der GUI einen Fortschrittsbalken zu
                aktualisieren.

        Returns:
            Anzahl der tatsaechlich aufgezeichneten (gueltigen) Samples.

        Raises:
            ValueError: Wenn ``label`` kein bekanntes Fahrbefehl-Label ist.
        """
        if label not in CONFIG.labels:
            raise ValueError(f"Unbekanntes Label: {label!r} (erlaubt: {CONFIG.labels})")

        record_duration = duration if duration is not None else CONFIG.default_training_duration

        logger.info("Trainiere: %s (%.0f Sekunden) ...", label, record_duration)

        recorded = 0
        start_time = time.time()
        last_tick_second = -1

        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            while True:
                elapsed = time.time() - start_time
                if elapsed >= record_duration:
                    break

                self.processor.poll()
                sample = self.processor.latest_sample

                if sample is not None and sample.is_valid:
                    writer.writerow(
                        [
                            sample.attention,
                            sample.meditation,
                            sample.delta,
                            sample.theta,
                            sample.alpha,
                            sample.beta,
                            sample.gamma,
                            label,
                        ]
                    )
                    recorded += 1

                current_second = int(elapsed)
                if on_tick is not None and current_second != last_tick_second:
                    last_tick_second = current_second
                    on_tick(elapsed, recorded)

                time.sleep(0.05)

        logger.info("Aufnahme fuer %s abgeschlossen: %d Samples gespeichert.", label, recorded)
        return recorded


def _print_menu() -> None:
    """Gibt das Auswahlmenue fuer den CLI-Trainingsmodus aus."""
    print("\nWaehle einen Fahrbefehl zum Trainieren:")
    print("  1 Forward")
    print("  2 Backward")
    print("  3 Left")
    print("  4 Right")
    print("  5 Stop")
    print("  q Beenden")


def run_cli(processor: BrainProcessor) -> None:
    """Startet den interaktiven, konsolenbasierten Trainingsmodus.

    Laeuft in einer Endlosschleife, bis der Benutzer ``q`` eingibt oder
    das Programm per Strg+C abgebrochen wird.

    Args:
        processor: Bereits laufender ``BrainProcessor``.
    """
    session = TrainingSession(processor)

    while True:
        _print_menu()
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nTraining beendet.")
            return

        if choice == "q":
            print("Training beendet.")
            return

        label = _MENU.get(choice)
        if label is None:
            print("Ungueltige Eingabe, bitte 1-5 oder q waehlen.")
            continue

        print(f"Trainiere: {label} ...")

        def _progress(elapsed: float, count: int, _duration=CONFIG.default_training_duration) -> None:
            remaining = max(0.0, _duration - elapsed)
            print(f"  {remaining:4.1f} s verbleibend, {count} Samples aufgezeichnet", end="\r")

        try:
            recorded = session.record_label(label, on_tick=_progress)
        except Exception:  # noqa: BLE001 - Trainingsmodus darf nie abstuerzen
            logger.exception("Fehler bei der Aufnahme fuer Label %s", label)
            print("\nFehler bei der Aufnahme, siehe Log.")
            continue

        print(f"\nFertig: {recorded} Samples fuer '{label}' gespeichert.")
