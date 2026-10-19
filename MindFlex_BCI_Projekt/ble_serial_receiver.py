"""
ble_serial_receiver.py
======================

Empfaengt die EEG-Daten des MindFlex-Arduino ueber Bluetooth Low Energy
statt ueber ein USB-Kabel.

Aufbau: Am Sendepin (TX, Pin 1) des EEG-Arduino haengt ein BLE-Modul der
HM-10-Familie (CC2541). Der Arduino gibt dort wie bisher eine CSV-Zeile
pro Sekunde aus; das Modul funkt diesen Bytestrom unveraendert weiter.
Der Mac abonniert die Charakteristik ``FFE1`` und bekommt die Daten als
Benachrichtigungen.

Am Sketch des Arduino aendert sich dadurch nichts.

Verkabelung::

    Modul VCC -> 3,3V
    Modul GND -> GND
    Modul RXD -> Pin 1 (TX), direkt verbunden
    Modul TXD -> nichts

Anders als beim RC-Auto ist RXD hier Pflicht: Der Datenstrom laeuft genau
darueber in das Modul hinein.

Diese Klasse ist ein direkter Ersatz fuer ``SerialReceiver`` und hat
dieselbe Schnittstelle: ``start()``, ``stop()``, ``queue``,
``packet_count`` und ``connected``.

Ein Hinweis zur Adresse: Beide Funkmodule im Projekt -- Auto und Headset
-- bewerben denselben Dienst ``FFE0``. Sie lassen sich deshalb nicht
anhand des Dienstes auseinanderhalten. Die Adresse des EEG-Moduls muss
ueber ``MINDFLEX_EEG_BLE_ADDRESS`` gesetzt werden; zu ermitteln mit
``python3 ble_scan.py``, am besten waehrend das Auto ausgeschaltet ist.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

from bleak import BleakClient, BleakScanner

from config import CONFIG, BleConfig
from serial_receiver import EEGSample, RohdatenMitschnitt, zeile_mitschneiden
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

# Wartezeit zwischen zwei Verbindungsversuchen.
RECONNECT_DELAY = 3.0

# Sicherheitsgrenze fuer den Zeilenpuffer. Kommt ueber diese Menge hinweg
# kein Zeilenumbruch, stimmt etwas mit dem Datenstrom nicht (falsche
# Baudrate am Modul) -- dann wird verworfen statt Speicher zu fuellen.
MAX_PUFFER = 4096


class BleEegReceiver:
    """Liest EEG-Pakete per Bluetooth Low Energy in einen Queue-Puffer.

    Verwendung::

        receiver = BleEegReceiver(CONFIG.eeg_ble)
        receiver.start()
        ...
        sample = receiver.queue.get(timeout=1.0)
        ...
        receiver.stop()

    Bricht die Funkverbindung ab, versucht die Klasse selbstaendig, sie
    wieder aufzubauen -- ein kurz aus dem Empfangsbereich getragenes
    Headset legt also nicht das Programm lahm.

    Attributes:
        ble_config: Verwendete BLE-Konfiguration.
        queue: Thread-sichere Queue mit geparsten ``EEGSample``-Objekten.
        packet_count: Anzahl erfolgreich geparster Pakete seit Start.
        connected: ``True``, wenn aktuell eine Verbindung besteht.
    """

    def __init__(self, ble_config: BleConfig | None = None, max_queue_size: int = 1000) -> None:
        """Initialisiert den Empfaenger, verbindet aber noch nicht.

        Args:
            ble_config: BLE-Konfiguration. Default aus ``CONFIG.eeg_ble``.
            max_queue_size: Maximale Groesse der internen Queue. Bei
                Ueberlauf wird das aelteste Sample verworfen.
        """
        self.ble_config = ble_config if ble_config is not None else CONFIG.eeg_ble
        self.queue: "queue.Queue[EEGSample]" = queue.Queue(maxsize=max_queue_size)
        self.packet_count = 0
        self.connected = False
        self.mitschnitt = RohdatenMitschnitt()

        self._puffer = bytearray()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Oeffentliche Schnittstelle (synchron, wie beim seriellen Empfaenger)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Startet den Hintergrund-Thread, der die Funkverbindung haelt."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("BleEegReceiver laeuft bereits, start() ignoriert.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="BleEegReceiverThread", daemon=True
        )
        self._thread.start()
        logger.info("BleEegReceiver-Thread gestartet.")

    def stop(self) -> None:
        """Beendet den Hintergrund-Thread und trennt die Verbindung."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.connected = False
        logger.info("BleEegReceiver gestoppt.")

    # ------------------------------------------------------------------
    # Hintergrund-Thread mit eigener Event-Loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Betreibt eine eigene asyncio-Event-Loop fuer bleak."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._supervisor())
        except Exception:  # noqa: BLE001 - der Thread darf nie hart sterben
            logger.exception("BLE-EEG-Thread unerwartet beendet.")
        finally:
            loop.close()

    async def _supervisor(self) -> None:
        """Haelt die Verbindung und baut sie nach einem Abbruch neu auf."""
        while not self._stop_event.is_set():
            try:
                await self._sitzung()
            except Exception as exc:  # noqa: BLE001 - Funkfehler sind normal
                logger.error("BLE-EEG-Verbindung fehlgeschlagen: %s", exc)

            self.connected = False
            if self._stop_event.is_set():
                break

            logger.info("Neuer Verbindungsversuch in %.0f s ...", RECONNECT_DELAY)
            await asyncio.sleep(RECONNECT_DELAY)

    async def _sitzung(self) -> None:
        """Baut eine Verbindung auf und empfaengt, bis sie endet."""
        device = await self._geraet_finden()
        if device is None:
            return

        async with BleakClient(device) as client:
            char = self._charakteristik_finden(client)
            if char is None:
                logger.error(
                    "Charakteristik %s am EEG-Modul nicht vorhanden.",
                    self.ble_config.char_uuid,
                )
                return

            self._puffer.clear()
            await client.start_notify(char, self._on_notify)
            self.connected = True
            logger.info("EEG per BLE verbunden (%s).", device.address)

            while not self._stop_event.is_set() and client.is_connected:
                await asyncio.sleep(0.2)

            try:
                await client.stop_notify(char)
            except Exception:  # noqa: BLE001 - beim Abbruch oft schon zu spaet
                pass

        logger.info("BLE-Verbindung zum EEG-Modul beendet.")

    async def _geraet_finden(self) -> Any:
        """Sucht das EEG-Funkmodul.

        Bevorzugt ueber die konfigurierte Adresse. Ohne Adresse wird nach
        Geraeten mit dem seriellen Dienst gesucht, wobei das Modul des
        RC-Autos ausgeschlossen wird -- sonst landet man leicht beim
        falschen der beiden.

        Returns:
            Das gefundene Geraet, oder ``None``.
        """
        if self.ble_config.address:
            device = await BleakScanner.find_device_by_address(
                self.ble_config.address, timeout=self.ble_config.scan_timeout
            )
            if device is None:
                logger.warning(
                    "EEG-Modul unter %s nicht gefunden. Ist der Arduino "
                    "eingeschaltet?",
                    self.ble_config.address,
                )
            return device

        logger.warning(
            "Keine Adresse fuer das EEG-Modul gesetzt "
            "(MINDFLEX_EEG_BLE_ADDRESS). Suche ueber den Dienst -- das "
            "kann das falsche Modul treffen, wenn das Auto eingeschaltet "
            "ist. Adresse ermitteln mit: python3 ble_scan.py"
        )

        gefunden = await BleakScanner.discover(
            timeout=self.ble_config.scan_timeout, return_adv=True
        )
        gesucht = self.ble_config.service_uuid.lower()
        auto_adresse = (CONFIG.car_ble.address or "").lower()

        for device, adv in gefunden.values():
            if device.address.lower() == auto_adresse:
                continue
            for uuid in adv.service_uuids or []:
                if uuid.lower() == gesucht:
                    logger.info("EEG-Modul ueber Dienst gefunden: %s", device.address)
                    return device

        logger.warning("Kein passendes BLE-Modul fuer das EEG gefunden.")
        return None

    def _charakteristik_finden(self, client: BleakClient) -> Any:
        """Sucht die Charakteristik, ueber die der Datenstrom kommt.

        Args:
            client: Verbundener BLE-Client.

        Returns:
            Die Charakteristik, oder ``None``.
        """
        gesucht = self.ble_config.char_uuid.lower()
        for service in client.services:
            for char in service.characteristics:
                if char.uuid.lower() == gesucht:
                    return char
        return None

    # ------------------------------------------------------------------
    # Datenstrom auswerten
    # ------------------------------------------------------------------

    @safe_execute(logger)
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        """Nimmt ein BLE-Paket entgegen und setzt daraus ganze Zeilen zusammen.

        BLE liefert den Bytestrom in Haeppchen von etwa 20 Byte -- eine
        CSV-Zeile verteilt sich also ueber mehrere Pakete und muss
        gepuffert werden, bis der Zeilenumbruch kommt.

        Args:
            _sender: Von bleak uebergebene Kennung, hier ungenutzt.
            data: Die empfangenen Bytes.
        """
        self._puffer.extend(data)

        if len(self._puffer) > MAX_PUFFER:
            logger.warning(
                "Kein Zeilenumbruch nach %d Byte -- Puffer verworfen. "
                "Stimmt die Baudrate am Funkmodul (9600)?",
                len(self._puffer),
            )
            self._puffer.clear()
            return

        while b"\n" in self._puffer:
            zeile, _, rest = self._puffer.partition(b"\n")
            self._puffer = bytearray(rest)

            # strip: Der Arduino beendet Zeilen mit \r\n, getrennt wird am
            # \n -- das \r gehoert nicht in den Mitschnitt.
            text = zeile.decode("utf-8", errors="ignore").strip()

            sample = zeile_mitschneiden(self.mitschnitt, text)
            if sample is None:
                continue

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
                self.queue.put_nowait(sample)
            except queue.Empty:
                pass
