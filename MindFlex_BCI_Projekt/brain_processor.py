"""
brain_processor.py
===================

Bindeglied zwischen dem rohen seriellen Empfang (``serial_receiver.py``)
und allen "hoeheren" Konsumenten (Feature-Extraktion, Training, GUI).

Aufgaben dieser Klasse:

1. Samples aus der ``SerialReceiver``-Queue abholen.
2. Pruefen, ob ein gueltiges EEG-Signal anliegt (Signal Quality != 200).
   Ist das Signal ungueltig, werden -- exakt wie gefordert -- KEINE
   Graphen aktualisiert, KEINE Daten verarbeitet und KEINE Steuerbefehle
   erzeugt. Das Sample wird nur fuer die reine Verbindungsanzeige
   (Signalqualitaet/Connection-Light) verwendet.
3. Gueltige Samples in einem zeitbasierten Ringpuffer (letzte 60 s)
   vorhalten, aus dem sich sowohl die GUI-Graphen als auch der Feature
   Extractor bedienen.
"""

from __future__ import annotations

import queue
import time

from config import CONFIG
from serial_receiver import EEGSample, SerialReceiver
from utils import TimeRingBuffer, safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


class BrainProcessor:
    """Validiert und puffert eingehende EEG-Samples.

    Attributes:
        receiver: Der ``SerialReceiver``, aus dem Samples konsumiert
            werden.
        buffer: Zeitbasierter Ringpuffer mit den letzten
            ``CONFIG.history_seconds`` Sekunden an GUELTIGEN Samples.
        latest_sample: Das zuletzt empfangene Sample (gueltig oder
            ungueltig) -- wird fuer die Signalqualitaets-/
            Verbindungsanzeige benoetigt, auch wenn es ungueltig ist.
        valid_packet_count: Anzahl der bisher als gueltig eingestuften
            Pakete.
    """

    def __init__(self, receiver: SerialReceiver) -> None:
        """Initialisiert den Processor.

        Args:
            receiver: Bereits erzeugter (aber nicht notwendigerweise
                gestarteter) ``SerialReceiver``.
        """
        self.receiver = receiver
        self.buffer: TimeRingBuffer[EEGSample] = TimeRingBuffer(CONFIG.history_seconds)
        self.latest_sample: EEGSample | None = None
        self.valid_packet_count = 0

    @safe_execute(logger, default=0)
    def poll(self, max_items: int = 50) -> int:
        """Holt alle aktuell verfuegbaren Samples aus der Queue und verarbeitet sie.

        Wird typischerweise periodisch von einem GUI-Timer (siehe
        ``visualizer.py``) aufgerufen.

        Args:
            max_items: Maximale Anzahl an Samples, die in einem Aufruf
                verarbeitet werden (verhindert, dass ein GUI-Tick durch
                einen ploetzlichen Datenschwall blockiert wird).

        Returns:
            Anzahl der in diesem Aufruf verarbeiteten Samples.
        """
        processed = 0
        for _ in range(max_items):
            try:
                sample = self.receiver.queue.get_nowait()
            except queue.Empty:
                break
            self._process_sample(sample)
            processed += 1
        return processed

    def _process_sample(self, sample: EEGSample) -> None:
        """Validiert ein Sample und legt es ggf. im Puffer ab.

        Args:
            sample: Das zu verarbeitende ``EEGSample``.
        """
        self.latest_sample = sample

        if not sample.is_valid:
            # Bewusst: Bei ungueltigem Signal (Signal Quality == 200)
            # werden weder Graphen aktualisiert noch Daten verarbeitet.
            # Wir tun hier nichts weiter mit dem Sample.
            return

        self.valid_packet_count += 1
        self.buffer.append(sample.timestamp, sample)

    def has_valid_signal(self) -> bool:
        """Gibt zurueck, ob gerade ein brauchbares Signal anliegt.

        Geprueft wird zweierlei: ob das zuletzt empfangene Sample gueltig
        war **und** ob es noch aktuell ist.

        Die Aktualitaetspruefung ist sicherheitsrelevant. Wird das Headset
        ausgeschaltet oder reisst die Funkverbindung ab, kommen schlicht
        keine Pakete mehr -- ``latest_sample`` bleibt dann fuer immer auf
        dem letzten gueltigen Wert stehen. Ohne die Pruefung gaelte das
        Signal weiterhin als gut, der Klassifikator sagte auf veralteten
        Daten weiter voraus, und das RC-Auto fuehre mit dem letzten
        Fahrbefehl weiter -- die Wiederholung im Auto-Controller haelt ihn
        ja am Leben. Genau das ist am 2026-09-24 passiert: Headset aus,
        Auto faehrt geradeaus weiter.

        Returns:
            True, wenn ein gueltiges und nicht veraltetes Sample vorliegt.
        """
        if self.latest_sample is None or not self.latest_sample.is_valid:
            return False

        alter = time.time() - self.latest_sample.timestamp
        return alter <= CONFIG.signal_timeout_seconds

    def get_window(self, seconds: float | None = None) -> list[EEGSample]:
        """Liefert die gueltigen Samples der letzten ``seconds`` Sekunden.

        Args:
            seconds: Fenstergroesse in Sekunden. Falls ``None``, wird
                ``CONFIG.feature_window_seconds`` verwendet.

        Returns:
            Liste von ``EEGSample`` (chronologisch aufsteigend). Kann
            leer sein, wenn (noch) kein gueltiges Signal vorliegt.
        """
        window_seconds = seconds if seconds is not None else CONFIG.feature_window_seconds
        return self.buffer.recent(window_seconds)

    def get_history(self) -> list[EEGSample]:
        """Liefert alle Samples im 60-Sekunden-Ringpuffer (fuer die Graphen)."""
        return self.buffer.values()

    def has_enough_samples_for_window(self, seconds: float | None = None) -> bool:
        """Prueft, ob genug Samples fuer eine Feature-Berechnung vorliegen.

        Args:
            seconds: Siehe ``get_window``.

        Returns:
            ``True``, wenn mindestens ``CONFIG.min_samples_per_window``
            gueltige Samples im Fenster liegen.
        """
        return len(self.get_window(seconds)) >= CONFIG.min_samples_per_window
