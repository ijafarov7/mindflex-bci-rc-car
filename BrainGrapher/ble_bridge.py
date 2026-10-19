"""BLE-Bruecke fuer den Processing Brain Grapher.

Warum es diese Bruecke gibt
---------------------------
Processing kann kein Bluetooth Low Energy. Seine ``serial``-Bibliothek
spricht serielle Schnittstellen, und ein CC2541-Modul (HM-10-Familie)
erzeugt unter macOS keine -- BLE-Geraete tauchen dort weder in den
Bluetooth-Einstellungen noch als ``/dev/cu.*`` auf.

Dieses Skript schliesst die Luecke: Es verbindet sich per BLE mit dem
Funkmodul am EEG-Arduino und reicht jede empfangene Zeile unveraendert
ueber eine lokale TCP-Verbindung weiter. Der Brain Grapher liest sie dort
mit Processings eingebauter ``net``-Bibliothek.

    MindFlex -> Arduino -> BLE-Modul ~~> dieses Skript -> TCP -> Processing

Benutzung
---------
    pip3 install bleak
    python3 ble_bridge.py

Das Skript zuerst starten, dann den Brain Grapher. Es laeuft, bis du es
mit Strg-C beendest, und verbindet sich nach einem Funkabriss von selbst
neu.

Die Adresse des Moduls ist unter macOS eine CoreBluetooth-Kennung und gilt
nur fuer diesen Rechner. Auf einem anderen Mac neu ermitteln und ueber die
Umgebungsvariable setzen:

    MINDFLEX_EEG_BLE_ADDRESS=... python3 ble_bridge.py
"""

import asyncio
import os
import sys

from bleak import BleakClient, BleakScanner

# Kennung des Funkmoduls am EEG-Arduino.
ADRESSE = os.environ.get(
    "MINDFLEX_EEG_BLE_ADDRESS", "076EFF65-8F8C-7941-795A-11DB6D8CFE9C"
)

# Serieller Dienst der HM-10-Familie. Wird zur Suche benutzt, falls das
# Modul unter der Adresse oben nicht gefunden wird.
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Hier lauscht die Bruecke auf den Brain Grapher.
HOST = "127.0.0.1"
PORT = int(os.environ.get("MINDFLEX_BRIDGE_PORT", "5204"))

SCAN_TIMEOUT = 15.0
RECONNECT_DELAY = 3.0

# Alle gerade verbundenen Processing-Instanzen.
empfaenger: set[asyncio.StreamWriter] = set()

# BLE liefert den Bytestrom in Haeppchen von etwa 20 Byte. Eine CSV-Zeile
# verteilt sich deshalb ueber mehrere Pakete und muss bis zum
# Zeilenumbruch gesammelt werden.
puffer = bytearray()

zeilen_gesamt = 0


async def client_verbunden(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Haelt eine Verbindung vom Brain Grapher offen, bis er sie schliesst."""
    peer = writer.get_extra_info("peername")
    empfaenger.add(writer)
    print(f"[TCP ] Brain Grapher verbunden ({peer}).")
    try:
        # Der Grapher sendet nichts. Wir warten nur darauf, dass er geht.
        await reader.read()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        empfaenger.discard(writer)
        writer.close()
        print("[TCP ] Brain Grapher getrennt.")


def verteile(zeile: bytes) -> None:
    """Schickt eine vollstaendige Zeile an alle verbundenen Grapher."""
    if not empfaenger:
        return
    for writer in list(empfaenger):
        try:
            writer.write(zeile + b"\n")
        except Exception:  # noqa: BLE001 - ein toter Empfaenger darf nicht stoeren
            empfaenger.discard(writer)


def bei_benachrichtigung(_sender, daten: bytearray) -> None:
    """Nimmt BLE-Haeppchen entgegen und gibt vollstaendige Zeilen weiter."""
    global puffer, zeilen_gesamt

    puffer.extend(daten)

    # Schutz gegen Muell ohne Zeilenumbruch: Puffer nicht endlos wachsen
    # lassen, sonst frisst ein einziges kaputtes Paket den Speicher.
    if len(puffer) > 4096:
        puffer.clear()
        return

    while b"\n" in puffer:
        zeile, _, rest = puffer.partition(b"\n")
        puffer = bytearray(rest)

        text = zeile.strip()
        if not text:
            continue

        zeilen_gesamt += 1
        if zeilen_gesamt <= 3 or zeilen_gesamt % 30 == 0:
            print(f"[BLE ] {zeilen_gesamt:5d} Zeilen, zuletzt: {text.decode('ascii', 'replace')}")

        verteile(bytes(text))


async def finde_geraet():
    """Sucht das Funkmodul, erst ueber die Adresse, dann ueber den Dienst."""
    geraet = await BleakScanner.find_device_by_address(ADRESSE, timeout=SCAN_TIMEOUT)
    if geraet is not None:
        return geraet

    print(f"[BLE ] Unter {ADRESSE} nichts gefunden, suche ueber Dienst {SERVICE_UUID} ...")
    gefunden = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
    for kandidat, adv in gefunden.values():
        for uuid in adv.service_uuids or []:
            if uuid.lower() == SERVICE_UUID:
                print(f"[BLE ] Modul ueber den Dienst gefunden: {kandidat.address}")
                return kandidat
    return None


async def ble_schleife() -> None:
    """Haelt die BLE-Verbindung dauerhaft offen und baut sie bei Bedarf neu auf."""
    while True:
        try:
            print("[BLE ] Suche Funkmodul ...")
            geraet = await finde_geraet()
            if geraet is None:
                print(f"[BLE ] Nicht gefunden. Ist der Arduino eingeschaltet? Neuer Versuch in {RECONNECT_DELAY:.0f}s.")
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            async with BleakClient(geraet) as client:
                print(f"[BLE ] Verbunden mit {geraet.address}.")
                puffer.clear()
                await client.start_notify(CHAR_UUID, bei_benachrichtigung)
                print("[BLE ] Empfang laeuft. Jetzt den Brain Grapher starten.")

                while client.is_connected:
                    await asyncio.sleep(1.0)

            print("[BLE ] Verbindung verloren.")
        except Exception as exc:  # noqa: BLE001 - die Bruecke darf nie sterben
            print(f"[BLE ] Fehler: {exc}")

        await asyncio.sleep(RECONNECT_DELAY)


async def main() -> None:
    server = await asyncio.start_server(client_verbunden, HOST, PORT)
    print(f"[TCP ] Bruecke lauscht auf {HOST}:{PORT}.")
    async with server:
        await ble_schleife()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nBeendet.")
    sys.exit(0)
