"""Fernsteuerung für das RC-Auto über Bluetooth.

Steuert das Auto mit der Tastatur, ohne EEG. Nützlich, um die Mechanik
und die Funkstrecke zu prüfen, bevor die Klassifikation dazukommt.

    W  vorwärts        A  links drehen
    S  rückwärts       D  rechts drehen
    Leertaste  stopp   Q  beenden

Benutzt denselben BleCarController wie main.py, inklusive der
Befehlswiederholung -- ein Tastendruck genügt also, das Auto fährt weiter,
bis du etwas anderes drückst.

Aufruf:

    python3 ble_remote.py
"""

import sys
import termios
import tty

from ble_car_controller import BleCarController

KEYS = {
    "w": "FORWARD",
    "s": "BACKWARD",
    "a": "LEFT",
    "d": "RIGHT",
    " ": "STOP",
}

PFEILE = {
    "FORWARD": "^ vorwaerts",
    "BACKWARD": "v rueckwaerts",
    "LEFT": "< links drehen",
    "RIGHT": "> rechts drehen",
    "STOP": "# stopp",
}


def read_key() -> str:
    """Liest einen einzelnen Tastendruck, ohne auf Enter zu warten.

    Returns:
        Das gedrückte Zeichen in Kleinschreibung.
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1).lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main() -> None:
    controller = BleCarController()

    print("Verbinde mit dem RC-Auto ...")
    if not controller.connect():
        print("Keine Verbindung. Ist der Arduino eingeschaltet?")
        raise SystemExit(1)

    print("Verbunden.\n")
    print("  W vorwaerts     A links drehen")
    print("  S rueckwaerts   D rechts drehen")
    print("  Leertaste stopp Q beenden\n")

    try:
        while True:
            key = read_key()

            if key in ("q", "\x03"):          # q oder Strg-C
                break

            label = KEYS.get(key)
            if label is None:
                continue

            controller.send_label(label)
            print(f"  {PFEILE[label]}")
    finally:
        print("\nStoppe und trenne ...")
        controller.disconnect()
        print("Fertig.")


main()
