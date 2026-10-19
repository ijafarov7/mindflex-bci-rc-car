"""Integrationstest fuer den BleCarController.

Benutzt die Klasse genau so, wie main.py es tut -- inklusive der
automatischen Befehlswiederholung. Am Arduino muss im Seriellen Monitor
sichtbar sein, dass 'F' etwa alle 0,4 Sekunden erneut ankommt, obwohl
send_label nur einmal aufgerufen wurde.
"""

import time

from ble_car_controller import BleCarController

controller = BleCarController()

print("Verbinde ...")
if not controller.connect():
    print("Keine Verbindung. Ist der RC-Auto-Arduino mit Strom versorgt?")
    raise SystemExit(1)

print("Verbunden.\n")

for label, seconds in [("FORWARD", 3.0), ("LEFT", 3.0), ("STOP", 2.0)]:
    sent = controller.send_label(label)
    print(f"{label:<9} gesendet={sent}  (halte {seconds:.0f}s)")
    time.sleep(seconds)

print("\nTrenne ...")
controller.disconnect()
print("Fertig.")
print("Erwartung im Seriellen Monitor: 'F' etwa 8x, 'L' etwa 8x, dann 'S'.")
