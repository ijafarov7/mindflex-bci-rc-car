"""
car_controller.py
==================

Steuert das RC-Auto ueber einen ZWEITEN Arduino, der die
Bewegungsbefehle in Motoransteuerung umsetzt (dieser Arduino-Code
existiert bereits und wird von diesem Projekt nicht veraendert).

Python sendet dazu ausschliesslich Einzelzeichen ueber die serielle
Schnittstelle:

    F = FORWARD, B = BACKWARD, L = LEFT, R = RIGHT, S = STOP

Sicherheitsprinzip: Bei jedem Fehler (Port nicht erreichbar, Schreib-
fehler, Verbindungsabbruch) faellt der Controller auf STOP zurueck bzw.
sendet erst gar keinen Bewegungsbefehl -- ein RC-Auto soll im
Zweifelsfall stehen bleiben, nicht unkontrolliert weiterfahren.
"""

from __future__ import annotations

import serial

from config import CONFIG, LABEL_TO_COMMAND, SerialConfig
from serial_receiver import resolve_port
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


class CarController:
    """Sendet Fahrbefehle an den RC-Auto-Arduino.

    Attributes:
        serial_config: Serielle Konfiguration fuer die Auto-Verbindung.
        connected: ``True``, wenn aktuell eine offene Verbindung besteht.
        last_command: Zuletzt tatsaechlich gesendeter Befehl (Label),
            um doppeltes Senden desselben Befehls zu vermeiden.
    """

    def __init__(self, serial_config: SerialConfig | None = None) -> None:
        """Initialisiert den Controller, oeffnet aber noch keine Verbindung.

        Args:
            serial_config: Serielle Konfiguration. Default aus
                ``CONFIG.car_serial``.
        """
        self.serial_config = serial_config if serial_config is not None else CONFIG.car_serial
        self.connected = False
        self.last_command: str | None = None
        self._serial: serial.Serial | None = None

    @safe_execute(logger, default=False)
    def connect(self) -> bool:
        """Oeffnet die serielle Verbindung zum RC-Auto-Arduino.

        Returns:
            ``True`` bei Erfolg, sonst ``False``. Ein fehlgeschlagener
            Verbindungsaufbau ist kein fataler Fehler -- der Rest des
            Programms (Anzeige/Klassifikation) laeuft trotzdem weiter,
            es werden dann nur keine Fahrbefehle gesendet.
        """
        port_name = resolve_port(self.serial_config)
        if not port_name:
            logger.warning(
                "Kein serieller Port fuer das RC-Auto gefunden (hint=%r).",
                self.serial_config.port_hint,
            )
            self.connected = False
            return False

        try:
            self._serial = serial.Serial(
                port_name, self.serial_config.baudrate, timeout=self.serial_config.timeout
            )
            self.connected = True
            logger.info("RC-Auto verbunden ueber %s.", port_name)
            return True
        except serial.SerialException as exc:
            logger.error("Konnte RC-Auto-Port %s nicht oeffnen: %s", port_name, exc)
            self.connected = False
            return False

    @safe_execute(logger)
    def disconnect(self) -> None:
        """Sendet vorsichtshalber STOP und schliesst die Verbindung."""
        if self.connected:
            self.send_label("STOP", force=True)
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self.connected = False

    @safe_execute(logger, default=False)
    def send_label(self, label: str, force: bool = False) -> bool:
        """Uebersetzt ein Vorhersage-Label in ein Steuerzeichen und sendet es.

        Sendet nur, wenn sich der Befehl gegenueber dem zuletzt
        gesendeten unterscheidet (oder ``force=True``), um die serielle
        Leitung nicht unnoetig mit identischen Befehlen zu fluten.

        Args:
            label: Eines der Labels aus ``CONFIG.labels``.
            force: Wenn ``True``, wird auch bei unveraendertem Befehl
                erneut gesendet.

        Returns:
            ``True``, wenn ein Befehl tatsaechlich gesendet wurde.
        """
        command = LABEL_TO_COMMAND.get(label)
        if command is None:
            logger.warning("Unbekanntes Label fuer Fahrbefehl: %r", label)
            return False

        if not self.connected or self._serial is None:
            logger.debug("RC-Auto nicht verbunden, Befehl '%s' wird verworfen.", command)
            return False

        if not force and label == self.last_command:
            return False

        try:
            self._serial.write(command.encode("ascii"))
        except serial.SerialException as exc:
            logger.error("Fehler beim Senden an das RC-Auto: %s", exc)
            self.connected = False
            return False

        self.last_command = label
        logger.debug("Fahrbefehl gesendet: %s (%s)", label, command)
        return True
