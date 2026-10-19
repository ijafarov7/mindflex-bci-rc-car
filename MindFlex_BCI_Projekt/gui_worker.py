"""Hintergrund-Threads der Oberflaeche.

Drei Aufgaben duerfen den Hauptthread nicht blockieren, weil sie
Sekunden bis Minuten dauern: das Aufzeichnen einer Trainingsreihe, das
Neutrainieren der Modelle und der Neuaufbau der Funkverbindung. Jede
laeuft deshalb in einem eigenen Thread und meldet ihr Ergebnis per
Signal zurueck.
"""

from __future__ import annotations

import subprocess
import threading
import time

from PyQt5 import QtCore

from classifier import BCIClassifier
from config import CONFIG
from trainer import TrainingSession
from utils import setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


class TrainingWorker(QtCore.QThread):
    """Fuehrt eine Trainingsaufnahme in einem eigenen Thread aus.

    Verhindert, dass die 10+ Sekunden dauernde Blockier-Schleife von
    ``TrainingSession.record_label`` die GUI einfrieren laesst.

    Signals:
        progress: Wird periodisch mit ``(elapsed_seconds,
            samples_recorded)`` gesendet.
        finished_recording: Wird am Ende mit der Gesamtzahl
            aufgezeichneter Samples gesendet.
        failed: Wird bei einer Ausnahme mit der Fehlermeldung gesendet.
    """

    progress = QtCore.pyqtSignal(float, int)
    finished_recording = QtCore.pyqtSignal(int)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, session: TrainingSession, label: str, duration: float) -> None:
        """Initialisiert den Worker.

        Args:
            session: ``TrainingSession``, die die Aufnahme durchfuehrt.
            label: Aufzuzeichnendes Fahrbefehl-Label.
            duration: Aufnahmedauer in Sekunden.
        """
        super().__init__()
        self.session = session
        self.label = label
        self.duration = duration

    def run(self) -> None:
        """Thread-Einstiegspunkt: fuehrt die Aufnahme aus und meldet das Ergebnis."""
        try:
            count = self.session.record_label(
                self.label, self.duration, on_tick=lambda e, c: self.progress.emit(e, c)
            )
            self.finished_recording.emit(count or 0)
        except Exception as exc:  # noqa: BLE001 - GUI darf nie abstuerzen
            logger.exception("Fehler im Trainings-Worker")
            self.failed.emit(str(exc))


class RetrainWorker(QtCore.QThread):
    """Trainiert alle Modelle in einem eigenen Thread neu.

    Signals:
        finished_training: Wird mit dem Genauigkeits-Dict gesendet.
        failed: Wird bei einer Ausnahme mit der Fehlermeldung gesendet.
    """

    finished_training = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, classifier: BCIClassifier) -> None:
        """Initialisiert den Worker.

        Args:
            classifier: ``BCIClassifier``, dessen ``train()`` aufgerufen wird.
        """
        super().__init__()
        self.classifier = classifier

    def run(self) -> None:
        """Thread-Einstiegspunkt: trainiert alle Modelle und meldet das Ergebnis."""
        try:
            accuracies = self.classifier.train()
            self.finished_training.emit(accuracies)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fehler im Retrain-Worker")
            self.failed.emit(str(exc))



class ReconnectWorker(QtCore.QThread):
    """Baut die Verbindung zum RC-Auto in einem eigenen Thread neu auf.

    Der Verbindungsaufbau dauert bis zu einer halben Minute -- die Suche
    nach dem Funkmodul allein hat ein Zeitlimit von 15 Sekunden. Liefe das
    im Hauptthread, waere die Oberflaeche so lange eingefroren.

    Signals:
        fertig: Wird mit dem Ergebnis gesendet (True bei Erfolg).
    """

    fertig = QtCore.pyqtSignal(bool)

    def __init__(self, car_controller) -> None:
        """Initialisiert den Worker.

        Args:
            car_controller: Der Controller, der neu verbunden werden soll.
        """
        super().__init__()
        self.car_controller = car_controller

    def run(self) -> None:
        """Thread-Einstiegspunkt: trennt und verbindet neu."""
        try:
            # Erst sauber trennen. Ohne das haelt das Funkmodul die alte,
            # tote Verbindung unter Umstaenden noch offen und weist die
            # neue ab -- es kann immer nur eine gleichzeitig.
            self.car_controller.disconnect()
            self.fertig.emit(bool(self.car_controller.connect()))
        except Exception:  # noqa: BLE001
            logger.exception("Fehler beim Neuverbinden des RC-Autos")
            self.fertig.emit(False)


class TestaufnahmeWorker(QtCore.QThread):
    """Zeichnet eine Testaufnahme auf, ohne sie ins Training zu geben.

    Signals:
        progress: ``(vergangene_sekunden, aufgezeichnete_samples)``.
        fertig: Pfad der geschriebenen Datei.
        failed: Fehlermeldung.
    """

    progress = QtCore.pyqtSignal(float, int)
    fertig = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, session: TrainingSession, duration: float) -> None:
        """Initialisiert den Worker.

        Args:
            session: Die ``TrainingSession`` mit dem Zielprofil.
            duration: Aufnahmedauer in Sekunden.
        """
        super().__init__()
        self.session = session
        self.duration = duration

    def run(self) -> None:
        """Thread-Einstiegspunkt: nimmt auf und meldet den Dateipfad."""
        try:
            ziel = self.session.record_test(
                self.duration, on_tick=lambda t, n: self.progress.emit(t, n)
            )
            self.fertig.emit(str(ziel))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fehler bei der Testaufnahme")
            self.failed.emit(str(exc))


class KalibrierungsWorker(QtCore.QThread):
    """Fuehrt eine Kalibrierung aus: viele kurze Aufnahmen nach festem Plan.

    Ablauf je Block, streng nacheinander:

    1. **Ansage** -- "Rueckwaerts. Kiefer zusammenbeissen." Der Thread
       wartet, bis sie zu Ende gesprochen ist. Vorher lief die Wechselzeit
       schon waehrend der Ansage, und die Aufnahme begann praktisch mit dem
       letzten Wort.
    2. **Wechselzeit** (nicht aufgezeichnet): Das EEG zeigt den neuen
       Zustand erst nach ein paar Sekunden -- Alpha faellt nach dem
       Augenoeffnen erst nach 2-3 s ab.
    3. **Aufnahme** ueber ``TrainingSession.record_label``, derselbe Weg wie
       beim normalen Training; jede Aufnahme wird ein eigener Block.
    4. **Endton** -- mit geschlossenen Augen der einzige Hinweis, dass der
       Block vorbei ist.

    Ton und Sprache laufen hier im Hintergrund-Thread und nicht in der
    Oberflaeche, damit der Ablauf auf ihr Ende warten kann, ohne das Fenster
    einzufrieren.

    Signals:
        phase: ``(art, block, gesamt, label, naechstes)`` mit ``art`` aus
            ``ansage``, ``uebergang``, ``aufnahme``, ``ende``.
        uebergang_rest: Verbleibende Sekunden der Wechselzeit.
        progress: ``(sekunden, samples)`` waehrend der Aufnahme.
        fertig: ``(bloecke, samples)`` wenn alles durch ist.
        abgebrochen: ``(bloecke, samples)`` nach einem Abbruch.
        failed: Fehlermeldung.
    """

    phase = QtCore.pyqtSignal(str, int, int, str, str)
    uebergang_rest = QtCore.pyqtSignal(float)
    progress = QtCore.pyqtSignal(float, int)
    fertig = QtCore.pyqtSignal(int, int)
    abgebrochen = QtCore.pyqtSignal(int, int)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        session: TrainingSession,
        plan: list[str],
        sekunden: float,
        uebergang: float,
        ansagen: dict[str, str] | None = None,
        endton: str | None = None,
        stimme: str = "Anna",
    ) -> None:
        """Initialisiert den Worker.

        Args:
            session: Die Aufnahme-Session des aktiven Profils.
            plan: Befehle in Aufnahmereihenfolge, siehe ``kalibrierungsplan``.
            sekunden: Aufnahmedauer je Block.
            uebergang: Nicht aufgezeichnete Wechselzeit nach der Ansage.
            ansagen: Befehl -> gesprochener Text, oder ``None`` fuer keine
                Ansage.
            endton: Pfad einer Tondatei fuer das Blockende, oder ``None``.
            stimme: Stimme der macOS-Sprachausgabe.
        """
        super().__init__()
        self.session = session
        self.plan = plan
        self.sekunden = sekunden
        self.uebergang = uebergang
        self.ansagen = ansagen
        self.endton = endton
        self.stimme = stimme
        self._abbrechen = threading.Event()

    def abbrechen(self) -> None:
        """Beendet die Kalibrierung nach spaetestens einem Schleifendurchlauf."""
        self._abbrechen.set()

    def _abspielen(self, befehl: list[str]) -> None:
        """Spielt Ton oder Sprache ab und wartet, bis es zu Ende ist.

        Ein Abbruch beendet auch die laufende Ausgabe, statt sie ausklingen
        zu lassen.
        """
        try:
            prozess = subprocess.Popen(
                befehl, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            logger.warning("Ausgabe nicht moeglich (%s): %s", befehl[0], exc)
            return
        while prozess.poll() is None:
            if self._abbrechen.is_set():
                prozess.terminate()
                return
            time.sleep(0.05)

    def run(self) -> None:
        """Arbeitet den Plan Block fuer Block ab."""
        bloecke = samples = 0
        gesamt = len(self.plan)
        try:
            for index, label in enumerate(self.plan):
                naechstes = self.plan[index + 1] if index + 1 < gesamt else ""
                block = index + 1

                if self.ansagen:
                    self.phase.emit("ansage", block, gesamt, label, naechstes)
                    self._abspielen(["say", "-v", self.stimme, self.ansagen.get(label, label)])

                self.phase.emit("uebergang", block, gesamt, label, naechstes)
                ende = time.time() + self.uebergang
                while time.time() < ende and not self._abbrechen.is_set():
                    self.uebergang_rest.emit(ende - time.time())
                    time.sleep(0.1)
                if self._abbrechen.is_set():
                    self.abgebrochen.emit(bloecke, samples)
                    return

                self.phase.emit("aufnahme", block, gesamt, label, naechstes)
                anzahl = self.session.record_label(
                    label,
                    self.sekunden,
                    on_tick=lambda e, c: self.progress.emit(e, c),
                    abbrechen=self._abbrechen,
                )
                samples += anzahl or 0

                if self._abbrechen.is_set():
                    # Ein angefangener Block zaehlt mit -- seine Zeilen
                    # stehen bereits in der Datei.
                    self.abgebrochen.emit(bloecke + (1 if anzahl else 0), samples)
                    return
                bloecke += 1

                if self.endton:
                    self.phase.emit("ende", block, gesamt, label, naechstes)
                    self._abspielen(["afplay", self.endton])

            self.fertig.emit(bloecke, samples)
        except Exception as exc:  # noqa: BLE001 - GUI darf nie abstuerzen
            logger.exception("Fehler in der Kalibrierung")
            self.failed.emit(str(exc))
