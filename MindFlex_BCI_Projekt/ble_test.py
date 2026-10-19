"""Sendetest fuer die BLE-Funkstrecke zum RC-Auto-Arduino.

Verbindet sich mit dem CC2541-Modul (HM-10-Familie) und schickt die
Fahrbefehle F, L, R, B, S nacheinander. Am Arduino muessen sie im
Seriellen Monitor als "[BT ] empfangen: ..." auftauchen.

Aufruf:
    python3 ble_test.py                 # feste Adresse aus ADDRESS
    python3 ble_test.py <adresse>       # andere Adresse
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

# CoreBluetooth-UUID des Moduls, ermittelt mit ble_scan.py. Auf macOS ist das
# keine MAC-Adresse, sondern eine Mac-spezifische Kennung -- auf einem anderen
# Rechner sieht dasselbe Modul anders aus.
ADDRESS = "95F44EE1-426A-D939-25DD-1A3F14527B4F"

# Serielle Charakteristik der HM-10-Familie. Was hier hineingeschrieben wird,
# kommt am TXD-Pin des Moduls heraus.
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

SEQUENCE = ["F", "L", "R", "B", "S"]


async def main(address: str) -> None:
    print(f"Suche Modul {address} ...")
    device = await BleakScanner.find_device_by_address(address, timeout=15.0)
    if device is None:
        print("Modul nicht gefunden. Ist der Arduino mit Strom versorgt?")
        return

    print("Verbinde ...")
    async with BleakClient(device) as client:
        print(f"Verbunden: {client.is_connected}\n")

        # Charakteristik suchen; manche Klone melden FFE1 erst nach dem Verbinden.
        char = None
        for service in client.services:
            for candidate in service.characteristics:
                if candidate.uuid.lower() == CHAR_UUID:
                    char = candidate
                    break

        if char is None:
            print("Charakteristik FFE1 nicht gefunden. Verfuegbar:")
            for service in client.services:
                for candidate in service.characteristics:
                    print(f"  {candidate.uuid}  {candidate.properties}")
            return

        # write-without-response ist bei diesen Modulen der uebliche Weg und
        # deutlich schneller; manche Klone koennen nur write-with-response.
        without_response = "write-without-response" in char.properties
        print(f"Charakteristik gefunden, ohne Bestaetigung senden: {without_response}\n")

        for command in SEQUENCE:
            await client.write_gatt_char(
                char, command.encode("ascii"), response=not without_response
            )
            print(f"gesendet: {command}")
            await asyncio.sleep(1.5)

        print("\nFertig. Im Seriellen Monitor muessen fuenf [BT ]-Zeilen stehen.")


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ADDRESS))
