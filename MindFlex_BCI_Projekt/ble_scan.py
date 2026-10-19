"""BLE-Scan: sucht nach dem CC2541-Modul (HM-10 / Klon) am RC-Auto-Arduino.

Interessant ist jedes Geraet, das den Service FFE0 bewirbt -- das ist die
serielle Charakteristik der HM-10-Familie.
"""

import asyncio

from bleak import BleakScanner

SERIAL_SERVICE = "ffe0"


async def main() -> None:
    print("Scanne 12 Sekunden ...\n")
    found = await BleakScanner.discover(timeout=12.0, return_adv=True)

    if not found:
        print("Keine BLE-Geraete gefunden.")
        return

    rows = []
    for device, adv in found.values():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        is_serial = any(SERIAL_SERVICE in u for u in uuids)
        rows.append((is_serial, adv.rssi if adv.rssi is not None else -999, device, adv))

    rows.sort(key=lambda r: (not r[0], -r[1]))

    for is_serial, rssi, device, adv in rows:
        marker = "  <== SERIELLER SERVICE FFE0" if is_serial else ""
        name = adv.local_name or device.name or "(kein Name)"
        print(f"{name:<28} {device.address}  RSSI={rssi:>5}{marker}")
        if adv.service_uuids:
            print(f"{'':<28} Services: {', '.join(adv.service_uuids)}")

    print(f"\n{len(rows)} Geraete gefunden.")


asyncio.run(main())
