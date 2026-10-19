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

import collections
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
        signal_quality: Rohwert des ThinkGear-Chips -- 0 = perfekter
            Kontakt, 200 = kein Kontakt. Fuer die Anzeige gibt es
            ``signal_percent``, wo 100 das beste Signal ist.
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
    def signal_percent(self) -> float:
        """Signalqualitaet als Prozentwert -- 100 ist bestmoeglich, 0 kein Signal.

        Der ThinkGear-Chip zaehlt andersherum: 0 bedeutet dort perfekten
        Kontakt und 200 gar keinen. Weil das beim Ablesen regelmaessig
        verwirrt, wird der Wert hier umgedreht und auf eine Prozentskala
        gebracht.

        Returns:
            Wert zwischen 0.0 und 100.0.
        """
        roh = max(0, min(CONFIG.invalid_signal_quality, self.signal_quality))
        return (1.0 - roh / CONFIG.invalid_signal_quality) * 100.0

    @property
    def is_valid(self) -> bool:
        """``True``, wenn ein gueltiges EEG-Signal anliegt.

        Gleichbedeutend mit ``signal_percent > 0``: Bei 0 Prozent besteht
        kein Kontakt zur Kopfhaut, alle Powerband-Werte sind dann
        bedeutungslos und die Messung wird ausgesetzt.
        """
        return self.signal_percent > 0.0

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


class RohdatenMitschnitt:
    """Haelt die zuletzt empfangenen Zeilen des EEG-Arduino zum Ansehen vor.

    Beide Empfaenger -- Kabel und Funk -- schreiben hier jede Zeile hinein,
    so wie sie ankommt, bevor irgendetwas davon ausgewertet wird. Auch
    Zeilen, die das Programm verwirft, landen hier: Genau die braucht man,
    wenn man wissen will, warum im Graphen nichts erscheint.

    Die Anzeige dazu ist ``RohdatenDialog``. Sie laeuft im GUI-Thread, die
    Empfaenger schreiben aus ihrem eigenen Thread -- deshalb die Sperre.
    Ohne sie koennte die Anzeige eine Liste lesen, an die gerade angehaengt
    wird, und das bricht in Python mit einem Fehler ab.

    Jede Zeile bekommt eine laufende Nummer. Die Anzeige merkt sich die
    letzte gesehene und holt nur, was danach kam.
    """

    # Art einer Zeile, wie sie in der Anzeige erscheint.
    EEG = "eeg"
    BATTERIE = "batterie"
    VERWORFEN = "verworfen"

    def __init__(self, max_zeilen: int = 2000) -> None:
        """Legt einen leeren Mitschnitt an.

        Args:
            max_zeilen: So viele Zeilen werden vorgehalten, aeltere fallen
                heraus. Bei einem Paket je Sekunde reichen 2000 fuer gut
                eine halbe Stunde.
        """
        self._zeilen: collections.deque = collections.deque(maxlen=max_zeilen)
        self._sperre = threading.Lock()
        self._nummer = 0
        self.verworfen = 0

    def eintragen(self, text: str, art: str) -> None:
        """Nimmt eine empfangene Zeile auf.

        Args:
            text: Die Zeile, bereits dekodiert und ohne Zeilenende.
            art: ``EEG``, ``BATTERIE`` oder ``VERWORFEN``.
        """
        with self._sperre:
            self._nummer += 1
            if art == self.VERWORFEN:
                self.verworfen += 1
            self._zeilen.append((self._nummer, time.time(), text, art))

    def seit(self, nummer: int) -> list[tuple[int, float, str, str]]:
        """Liefert alle Zeilen mit einer hoeheren laufenden Nummer.

        Args:
            nummer: Die zuletzt gesehene Nummer, 0 fuer alles Vorgehaltene.

        Returns:
            Liste aus ``(nummer, zeitpunkt, text, art)``, aelteste zuerst.
        """
        with self._sperre:
            return [zeile for zeile in self._zeilen if zeile[0] > nummer]

    @property
    def gesamt(self) -> int:
        """Anzahl aller bisher eingetragenen Zeilen, auch der herausgefallenen."""
        with self._sperre:
            return self._nummer

    @property
    def zuletzt(self) -> float | None:
        """Empfangszeitpunkt der juengsten Zeile, oder ``None``."""
        with self._sperre:
            return self._zeilen[-1][1] if self._zeilen else None


def zeile_mitschneiden(mitschnitt: RohdatenMitschnitt, text: str) -> EEGSample | None:
    """Traegt eine Zeile in den Mitschnitt ein und parst sie.

    Gemeinsamer Weg fuer beide Empfaenger, damit Mitschnitt und Auswertung
    nicht auseinanderlaufen koennen.

    Args:
        mitschnitt: Ziel fuer die Rohzeile.
        text: Die dekodierte Zeile ohne Zeilenende.

    Returns:
        Das Sample, oder ``None`` fuer Batteriemeldungen und unbrauchbare
        Zeilen.
    """
    if not text:
        return None

    if ist_batterie_zeile(text):
        mitschnitt.eintragen(text, RohdatenMitschnitt.BATTERIE)
        return None

    sample = parse_eeg_line(text)
    mitschnitt.eintragen(
        text,
        RohdatenMitschnitt.EEG if sample is not None else RohdatenMitschnitt.VERWORFEN,
    )
    return sample


def ist_batterie_zeile(text: str) -> bool:
    """Erkennt eine Batteriemeldung des Arduino.

    Die Anzeige des Akkustands wurde aus dem Programm entfernt. Die
    Arduino-Sketche senden ihre Meldungen im Format ``BAT,<millivolt>,
    <modus>`` aber weiterhin. Die Zeilen werden hier erkannt und still
    verworfen -- ohne diese Pruefung landeten sie im EEG-Parser und
    erzeugten dort bei jeder Meldung eine Warnung.

    Args:
        text: Eine bereits dekodierte Zeile.

    Returns:
        ``True``, wenn die Zeile eine Batteriemeldung ist.
    """
    return text.strip().startswith("BAT,")


def parse_eeg_line(text: str) -> EEGSample | None:
    """Parst eine CSV-Zeile des EEG-Arduino zu einem ``EEGSample``.

    Gemeinsam genutzt vom seriellen und vom BLE-Empfaenger, damit es nur
    eine Stelle gibt, an der das Datenformat definiert ist.

    Unbrauchbare Zeilen (falsche Anzahl Werte, nicht-numerische Zeichen)
    fuehren nie zu einer Exception, sondern zu ``None``.

    Args:
        text: Eine bereits dekodierte Zeile, z. B.
            ``"0,53,60,12345,...,678"``.

    Returns:
        Das geparste ``EEGSample``, oder ``None`` bei unbrauchbarer Zeile.
    """
    text = text.strip()
    if not text:
        return None

    parts = [p.strip() for p in text.split(",")]
    if len(parts) != CONFIG.num_eeg_values:
        logger.debug(
            "Ignoriere Paket mit %d statt %d Werten: %r",
            len(parts),
            CONFIG.num_eeg_values,
            text,
        )
        return None

    try:
        int_values = [int(p) for p in parts]
    except ValueError:
        logger.debug("Ignoriere nicht-numerisches Paket: %r", text)
        return None

    return EEGSample.from_csv_values(int_values)


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
        self.mitschnitt = RohdatenMitschnitt()

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

        sample = zeile_mitschneiden(self.mitschnitt, text)
        if sample is None:
            return

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
