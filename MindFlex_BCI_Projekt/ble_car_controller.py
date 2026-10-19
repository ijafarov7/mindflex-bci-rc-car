"""
ble_car_controller.py
=====================

Steuert das RC-Auto ueber eine Bluetooth-Low-Energy-Funkstrecke.

Das Funkmodul am RC-Auto-Arduino ist ein CC2541 (HM-10-Familie). Es ist
*kein* HC-05: Es benutzt kein klassisches SPP-Profil, erscheint deshalb
nicht in den Bluetooth-Einstellungen und erzeugt keinen seriellen Port.
``pyserial`` kann es prinzipiell nicht ansprechen. Stattdessen wird ueber
GATT in die Charakteristik ``FFE1`` geschrieben; was dort ankommt, gibt
das Modul unveraendert an seinem TXD-Pin aus und landet damit beim
Arduino.

Gesendet werden dieselben Einzelzeichen wie bisher:

    F = FORWARD, B = BACKWARD, L = LEFT, R = RIGHT, S = STOP

Diese Klasse ist ein direkter Ersatz fuer ``car_controller.CarController``
und hat dieselbe Schnittstelle (``connect`` / ``send_label`` /
``disconnect`` sowie die Attribute ``connected`` und ``last_command``).

Technischer Hintergrund: ``bleak`` arbeitet asynchron, der Rest der
Anwendung (PyQt-GUI) synchron. Diese Klasse betreibt deshalb einen
eigenen Hintergrund-Thread mit eigener Event-Loop und reicht Aufrufe per
``run_coroutine_threadsafe`` hinein. Nach aussen bleibt alles synchron.

Sicherheitsprinzip wie beim seriellen Vorgaenger: Bei jedem Fehler
(Modul nicht gefunden, Verbindungsabbruch, Schreibfehler) wird eher kein
Befehl gesendet, als unkontrolliert weiterzufahren -- und der Rest der
Anwendung laeuft weiter, es werden dann nur keine Fahrbefehle gesendet.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine, TypeVar

from bleak import BleakClient, BleakScanner

from config import CONFIG, LABEL_TO_COMMAND, BleConfig
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

T = TypeVar("T")


class BleCarController:
    """Sendet Fahrbefehle per Bluetooth Low Energy an den RC-Auto-Arduino.

    Attributes:
        ble_config: BLE-Konfiguration (Adresse, UUIDs, Zeitlimits).
        connected: ``True``, wenn aktuell eine offene Verbindung besteht.
        last_command: Zuletzt gesendetes Label, um doppeltes Senden
            desselben Befehls zu vermeiden.
    """

    def __init__(self, ble_config: BleConfig | None = None) -> None:
        """Initialisiert den Controller, baut aber noch keine Verbindung auf.

        Args:
            ble_config: BLE-Konfiguration. Default aus ``CONFIG.car_ble``.
        """
        self.ble_config = ble_config if ble_config is not None else CONFIG.car_ble
        self.connected = False
        self.last_command: str | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: BleakClient | None = None
        self._char: Any = None
        self._write_response = True
        self._current_command: str | None = None
        self._heartbeat: asyncio.Task | None = None

        # Unterscheidet das planmaessige Trennen vom echten Verbindungsabriss,
        # damit nur letzterer als Warnung im Log landet.
        self._closing = False

    # ------------------------------------------------------------------
    # Oeffentliche Schnittstelle (synchron, wie beim seriellen Vorgaenger)
    # ------------------------------------------------------------------

    @safe_execute(logger, default=False)
    def connect(self) -> bool:
        """Startet den BLE-Thread und verbindet sich mit dem Funkmodul.

        Returns:
            ``True`` bei Erfolg, sonst ``False``. Ein fehlgeschlagener
            Verbindungsaufbau ist kein fataler Fehler -- Anzeige und
            Klassifikation laufen weiter, es werden dann nur keine
            Fahrbefehle gesendet.
        """
        self._closing = False
        self._start_loop()
        if self._loop is None:
            logger.error("BLE-Hintergrund-Thread konnte nicht gestartet werden.")
            return False

        # Grosszuegiges Zeitlimit: Suche plus Verbindungsaufbau.
        result = self._run(self._async_connect(), timeout=self.ble_config.scan_timeout + 20.0)
        self.connected = bool(result)
        return self.connected

    @safe_execute(logger, default=False)
    def send_label(self, label: str, force: bool = False) -> bool:
        """Uebersetzt ein Vorhersage-Label in ein Steuerzeichen und sendet es.

        Sendet nur, wenn sich der Befehl gegenueber dem zuletzt gesendeten
        unterscheidet (oder ``force=True``). Damit der Arduino trotzdem
        nicht in seinen Sicherheits-Timeout laeuft, wiederholt ein
        Hintergrund-Task den aktuellen Befehl regelmaessig (siehe
        ``BleConfig.resend_interval``).

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

        if not self.connected or self._loop is None:
            logger.debug("RC-Auto nicht verbunden, Befehl '%s' wird verworfen.", command)
            return False

        if not force and label == self.last_command:
            return False

        if not self._run(self._async_write(command), timeout=5.0):
            return False

        self.last_command = label
        self._current_command = command
        logger.debug("Fahrbefehl gesendet: %s (%s)", label, command)
        return True

    @safe_execute(logger)
    def disconnect(self) -> None:
        """Sendet vorsichtshalber STOP und faehrt die Verbindung herunter."""
        if self.connected:
            self.send_label("STOP", force=True)

        self._closing = True

        if self._loop is not None:
            self._run(self._async_disconnect(), timeout=10.0)
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread is not None:
            self._thread.join(timeout=5.0)

        self._loop = None
        self._thread = None
        self._client = None
        self._char = None
        self.connected = False

    # ------------------------------------------------------------------
    # Thread- und Loop-Verwaltung
    # ------------------------------------------------------------------

    def _start_loop(self) -> None:
        """Startet einmalig den Hintergrund-Thread mit eigener Event-Loop."""
        if self._loop is not None:
            return

        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=runner, name="ble-car", daemon=True)
        self._thread.start()
        ready.wait(timeout=5.0)

    def _run(self, coro: Coroutine[Any, Any, T], timeout: float) -> T | None:
        """Fuehrt eine Coroutine im BLE-Thread aus und wartet auf ihr Ergebnis.

        Args:
            coro: Auszufuehrende Coroutine.
            timeout: Maximale Wartezeit in Sekunden.

        Returns:
            Das Ergebnis der Coroutine, oder ``None`` bei Zeitueberschreitung
            oder Fehler.
        """
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            logger.error("BLE-Operation hat das Zeitlimit von %.1fs ueberschritten.", timeout)
            future.cancel()
            return None
        except Exception as exc:  # noqa: BLE001 - darf die Anwendung nie stoppen
            logger.error("BLE-Operation fehlgeschlagen: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Asynchroner Teil (laeuft ausschliesslich im BLE-Thread)
    # ------------------------------------------------------------------

    async def _async_connect(self) -> bool:
        """Sucht das Modul, verbindet sich und merkt sich die Charakteristik."""
        device = None

        if self.ble_config.address:
            device = await BleakScanner.find_device_by_address(
                self.ble_config.address, timeout=self.ble_config.scan_timeout
            )
            if device is None:
                logger.info(
                    "Modul unter %s nicht gefunden, suche ueber Service %s ...",
                    self.ble_config.address,
                    self.ble_config.service_uuid,
                )

        if device is None:
            device = await self._find_by_service()

        if device is None:
            logger.warning(
                "Kein BLE-Funkmodul gefunden. Ist der RC-Auto-Arduino mit Strom versorgt?"
            )
            return False

        # Zweite Sicherung: Auch eine versehentlich auf das Headset gesetzte
        # Autoadresse darf nicht dazu fuehren, dass Fahrbefehle an das
        # EEG-Modul gehen und dessen Verbindung mitverwaltet wird.
        eeg_adresse = (CONFIG.eeg_ble.address or "").lower()
        if eeg_adresse and device.address.lower() == eeg_adresse:
            logger.error(
                "Adresse %s gehoert zum EEG-Modul -- Verbindung als RC-Auto "
                "verweigert. MINDFLEX_CAR_BLE_ADDRESS pruefen.",
                device.address,
            )
            return False

        client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await client.connect()

        char = self._find_characteristic(client)
        if char is None:
            logger.error(
                "Charakteristik %s am Modul nicht vorhanden.", self.ble_config.char_uuid
            )
            await client.disconnect()
            return False

        self._client = client
        self._char = char
        # Ohne Bestaetigung zu senden ist deutlich schneller; manche Klone
        # beherrschen aber nur den bestaetigten Schreibzugriff.
        self._write_response = "write-without-response" not in char.properties

        logger.info(
            "RC-Auto per BLE verbunden (%s, Bestaetigung=%s).",
            device.address,
            self._write_response,
        )

        if self.ble_config.resend_interval > 0:
            self._heartbeat = asyncio.get_running_loop().create_task(self._heartbeat_loop())

        return True

    async def _find_by_service(self) -> Any:
        """Sucht ein Geraet, das den seriellen Service bewirbt.

        Das EEG-Modul ist dabei ausgeschlossen. Beide Funkmodule im Projekt
        sind baugleiche CC2541 und bewerben denselben Dienst ``FFE0`` --
        ohne den Ausschluss nimmt die Suche das erste, das sie findet.

        Genau das ist am 2026-09-25 um 18:29 passiert: Das Auto war aus, die
        Suche fand das Modul am Headset und verband sich damit. Die
        Oberflaeche meldete "RC-Auto verbunden", obwohl kein Auto da war,
        und beim Beenden riss die Trennung vom vermeintlichen Auto die
        EEG-Verbindung gleich mit ab -- es war ja dieselbe. Danach war das
        Modul erst nach einem Neustart der Headset-Seite wieder zu finden.

        Returns:
            Das gefundene Geraet, oder ``None``.
        """
        found = await BleakScanner.discover(
            timeout=self.ble_config.scan_timeout, return_adv=True
        )
        wanted = self.ble_config.service_uuid.lower()
        eeg_adresse = (CONFIG.eeg_ble.address or "").lower()

        for device, adv in found.values():
            if eeg_adresse and device.address.lower() == eeg_adresse:
                logger.info(
                    "Funkmodul %s uebersprungen -- das ist das EEG-Modul, "
                    "nicht das Auto.",
                    device.address,
                )
                continue
            for uuid in adv.service_uuids or []:
                if uuid.lower() == wanted:
                    logger.info("Funkmodul ueber Service gefunden: %s", device.address)
                    return device
        return None

    def _find_characteristic(self, client: BleakClient) -> Any:
        """Sucht die Schreib-Charakteristik in den Diensten des Geraets.

        Args:
            client: Verbundener BLE-Client.

        Returns:
            Die Charakteristik, oder ``None``.
        """
        wanted = self.ble_config.char_uuid.lower()
        for service in client.services:
            for char in service.characteristics:
                if char.uuid.lower() == wanted:
                    return char
        return None

    async def _async_write(self, command: str) -> bool:
        """Schreibt ein Steuerzeichen in die Charakteristik.

        Args:
            command: Einzelzeichen ``F``/``B``/``L``/``R``/``S``.

        Returns:
            ``True`` bei Erfolg.
        """
        if self._client is None or self._char is None:
            return False
        try:
            await self._client.write_gatt_char(
                self._char, command.encode("ascii"), response=self._write_response
            )
            return True
        except Exception as exc:  # noqa: BLE001 - Funkfehler duerfen nicht abstuerzen
            logger.error("Fehler beim Senden an das RC-Auto: %s", exc)
            self.connected = False
            return False

    async def _heartbeat_loop(self) -> None:
        """Wiederholt den aktuellen Fahrbefehl in festem Abstand.

        Der Arduino stoppt die Motoren, wenn laenger als eine Sekunde kein
        Befehl ankommt. Da ``send_label`` nur bei Aenderung sendet, wuerde
        das Auto ohne diese Wiederholung nach einer Sekunde stehenbleiben.
        """
        while True:
            await asyncio.sleep(self.ble_config.resend_interval)
            if self._current_command is None or not self.connected:
                continue
            await self._async_write(self._current_command)

    async def _async_disconnect(self) -> None:
        """Beendet den Heartbeat und trennt die Verbindung."""
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            try:
                await self._heartbeat
            except asyncio.CancelledError:
                pass
            self._heartbeat = None

        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Callback von bleak, wenn die Funkverbindung endet.

        Feuert sowohl beim planmaessigen Trennen als auch beim echten
        Abriss -- nur letzterer ist eine Warnung wert.
        """
        if self._closing:
            logger.info("BLE-Verbindung zum RC-Auto planmaessig getrennt.")
        else:
            logger.warning("BLE-Verbindung zum RC-Auto verloren.")
        self.connected = False
        self._char = None
