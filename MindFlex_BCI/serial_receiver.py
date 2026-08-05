"""
serial_receiver.py
===================

Liest die EEG-Rohdaten vom ersten Arduino (mit installierter Arduino
Brain Library / ThinkGear-Interface) ueber die serielle Schnittstelle.

Der Arduino sendet fortlaufend Zeilen im Format::

    SignalQuality,Attention,Meditation,Delta,Theta,LowAlpha,HighAlpha,
    LowBeta,HighBeta,LowGamma,HighGamma

Dieses Modul ist bewusst NUR fuer das Empfangen und Parsen zustaendig.
Die inhaltliche Bewertung (gueltig/ungueltig, Ringpuffer, Feature-
Extraktion) passiert in ``brain_processor.py``. Diese Trennung haelt
das Modul klein, testbar und leicht austauschbar (z. B. durch einen
Datei- oder Zufalls-basierten Simulator fuer Tests ohne Hardware).

Der Empfang laeuft in einem eigenen Thread, damit die GUI (PyQt5) nicht
blockiert. Alle geparsten Samples landen in einer ``queue.Queue``, die
von ``brain_processor.py`` konsumiert wird.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

from config import CONFIG, SerialConfig
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


@dataclass(frozen=True)
class EEGSample:
    """Ein einzelnes, geparstes EEG-Datenpaket.

    Attributes:
        timestamp: Empfangszeitpunkt (``time.time()``).
        signal_quality: 0 = perfekter Kontakt, 200 = kein Kontakt.
        attention: Aufmerksamkeitswert (0-100).
        meditation: Entspannungswert (0-100).
        delta, theta, low_alpha, high_alpha, low_beta, high_beta,
        low_gamma, high_gamma: Rohe EEG-Powerband-Werte.
    """

    timestamp: float
    signal_quality: int
    attention: int
    meditation: int
    delta: int
    theta: int
    low_alpha: int
    high_alpha: int
    low_beta: int
    high_beta: int
    low_gamma: int
    high_gamma: int

    @property
    def alpha(self) -> float:
        """Kombinierter Alpha-Wert (Mittel aus Low- und High-Alpha)."""
        return (self.low_alpha + self.high_alpha) / 2.0

    @property
    def beta(self) -> float:
        """Kombinierter Beta-Wert (Mittel aus Low- und High-Beta)."""
        return (self.low_beta + self.high_beta) / 2.0

    @property
    def gamma(self) -> float:
        """Kombinierter Gamma-Wert (Mittel aus Low- und High-Gamma)."""
        return (self.low_gamma + self.high_gamma) / 2.0

    @property
    def is_valid(self) -> bool:
        """``True``, wenn ein gueltiges EEG-Signal anliegt.

        Nach ThinkGear-Konvention bedeutet ``signal_quality == 200``,
        dass kein Kontakt zur Kopfhaut besteht -- alle Powerband-Werte
        sind dann bedeutungslos.
        """
        return self.signal_quality != CONFIG.invalid_signal_quality

    @staticmethod
    def from_csv_values(values: list[int]) -> "EEGSample":
        """Baut ein ``EEGSample`` aus einer Liste von 11 Ganzzahlen.

        Args:
            values: Liste mit genau 11 Werten in der Reihenfolge
                SignalQuality, Attention, Meditation, Delta, Theta,
                LowAlpha, HighAlpha, LowBeta, HighBeta, LowGamma,
                HighGamma.

        Returns:
            Das entsprechende ``EEGSample``.

        Raises:
            ValueError: Wenn ``values`` nicht genau 11 Elemente enthaelt.
        """
        if len(values) != CONFIG.num_eeg_values:
            raise ValueError(
                f"Erwarte {CONFIG.num_eeg_values} Werte, erhielt {len(values)}"
            )
        return EEGSample(time.time(), *values)


def find_port_by_hint(hint: str) -> str | None:
    """Sucht einen seriellen Port anhand eines Substrings in der Beschreibung.

    Nuetzlich, um nicht jedes Mal den COM-Port-Index von Hand pflegen zu
    muessen -- sucht z. B. nach "Arduino" in den vom Betriebssystem
    gemeldeten Geraetenamen.

    Args:
        hint: Teilstring, nach dem (gross-/kleinschreibungsunabhaengig)
            in Geraetename/-beschreibung gesucht wird.

    Returns:
        Der Port-Name (z. B. "COM5") des ersten Treffers, oder ``None``,
        wenn kein passender Port gefunden wurde.
    """
    hint_lower = hint.lower()
    for port_info in list_ports.comports():
        haystack = f"{port_info.description} {port_info.manufacturer or ''}".lower()
        if hint_lower in haystack:
            return port_info.device
    return None


def resolve_port(serial_config: SerialConfig) -> str | None:
    """Ermittelt den zu verwendenden Port aus einer ``SerialConfig``.

    Wenn ``serial_config.port`` explizit gesetzt ist, wird dieser
    verwendet. Andernfalls wird versucht, den Port anhand von
    ``port_hint`` automatisch zu finden.

    Args:
        serial_config: Die zu verwendende serielle Konfiguration.

    Returns:
        Der aufgeloeste Port-Name, oder ``None``, wenn keiner gefunden
        werden konnte.
    """
    if serial_config.port:
        return serial_config.port
    return find_port_by_hint(serial_config.port_hint)


class SerialReceiver:
    """Liest EEG-Pakete in einem Hintergrund-Thread von einem seriellen Port.

    Verwendung::

        receiver = SerialReceiver(CONFIG.eeg_serial)
        receiver.start()
        ...
        sample = receiver.queue.get(timeout=1.0)
        ...
        receiver.stop()

    Die Klasse versucht bei Verbindungsproblemen automatisch, die
    Verbindung erneut aufzubauen, damit ein kurzzeitig gezogenes USB-
    Kabel nicht das gesamte Programm zum Absturz bringt.

    Attributes:
        serial_config: Verwendete serielle Konfiguration.
        queue: Thread-sichere Queue, in die geparste ``EEGSample``-
            Objekte gelegt werden.
        packet_count: Anzahl erfolgreich geparster Pakete seit Start.
        connected: ``True``, wenn aktuell eine offene Verbindung besteht.
    """

    def __init__(self, serial_config: SerialConfig, max_queue_size: int = 1000) -> None:
        """Initialisiert den Empfaenger, oeffnet aber noch keine Verbindung.

        Args:
            serial_config: Zu verwendende serielle Konfiguration (Port,
                Baudrate, Timeout).
            max_queue_size: Maximale Groesse der internen Queue, um bei
                einem langsamen Konsumenten nicht unbegrenzt Speicher zu
                verbrauchen (aelteste Eintraege werden dann verworfen).
        """
        self.serial_config = serial_config
        self.queue: "queue.Queue[EEGSample]" = queue.Queue(maxsize=max_queue_size)
        self.packet_count = 0
        self.connected = False

        self._serial: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Startet den Hintergrund-Thread, der kontinuierlich Daten liest."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("SerialReceiver laeuft bereits, start() ignoriert.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="SerialReceiverThread", daemon=True
        )
        self._thread.start()
        logger.info("SerialReceiver-Thread gestartet.")

    def stop(self) -> None:
        """Stoppt den Hintergrund-Thread und schliesst den Port sauber."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close_port()
        logger.info("SerialReceiver gestoppt.")

    @safe_execute(logger)
    def _close_port(self) -> None:
        """Schliesst die serielle Verbindung, falls sie offen ist."""
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self.connected = False

    def _open_port(self) -> bool:
        """Versucht, den seriellen Port zu oeffnen.

        Returns:
            ``True`` bei Erfolg, sonst ``False``.
        """
        port_name = resolve_port(self.serial_config)
        if not port_name:
            logger.error(
                "Kein serieller Port gefunden (hint=%r). Verfuegbare Ports: %s",
                self.serial_config.port_hint,
                [p.device for p in list_ports.comports()],
            )
            return False

        try:
            self._serial = serial.Serial(
                port_name,
                self.serial_config.baudrate,
                timeout=self.serial_config.timeout,
            )
            self.connected = True
            logger.info("Verbunden mit %s @ %d Baud.", port_name, self.serial_config.baudrate)
            return True
        except serial.SerialException as exc:
            logger.error("Konnte Port %s nicht oeffnen: %s", port_name, exc)
            self.connected = False
            return False

    def _run(self) -> None:
        """Hauptschleife des Hintergrund-Threads.

        Liest Zeilen, parst sie, und legt gueltige Pakete in die Queue.
        Bei Verbindungsverlust wird periodisch versucht, neu zu
        verbinden, statt den Thread (und damit das Programm) abstuerzen
        zu lassen.
        """
        reconnect_delay = 2.0

        while not self._stop_event.is_set():
            if not self.connected:
                if not self._open_port():
                    time.sleep(reconnect_delay)
                    continue

            try:
                assert self._serial is not None
                raw_line = self._serial.readline()
            except (serial.SerialException, OSError) as exc:
                logger.error("Serieller Lesefehler, versuche Reconnect: %s", exc)
                self._close_port()
                time.sleep(reconnect_delay)
                continue

            if not raw_line:
                # Timeout ohne Daten -- normal, einfach erneut versuchen.
                continue

            self._handle_line(raw_line)

    @safe_execute(logger)
    def _handle_line(self, raw_line: bytes) -> None:
        """Dekodiert und parst eine einzelne empfangene Zeile.

        Fehlerhafte Zeilen (falsche Anzahl Werte, nicht-numerische
        Zeichen) werden geloggt und ignoriert -- sie fuehren nie zum
        Absturz des Threads.

        Args:
            raw_line: Rohe Bytes, wie von ``serial.Serial.readline()``
                geliefert.
        """
        try:
            text = raw_line.decode("utf-8", errors="ignore").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Konnte Zeile nicht dekodieren: %s", exc)
            return

        if not text:
            return

        parts = [p.strip() for p in text.split(",")]

        if len(parts) != CONFIG.num_eeg_values:
            logger.debug(
                "Ignoriere Paket mit %d statt %d Werten: %r",
                len(parts),
                CONFIG.num_eeg_values,
                text,
            )
            return

        try:
            int_values = [int(p) for p in parts]
        except ValueError:
            logger.debug("Ignoriere nicht-numerisches Paket: %r", text)
            return

        sample = EEGSample.from_csv_values(int_values)
        self.packet_count += 1
        self._put_sample(sample)

    def _put_sample(self, sample: EEGSample) -> None:
        """Legt ein Sample in die Queue, verwirft bei Ueberlauf das aelteste.

        Args:
            sample: Das einzufuegende ``EEGSample``.
        """
        try:
            self.queue.put_nowait(sample)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(sample)
