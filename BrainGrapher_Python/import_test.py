"""
Ein kleines Test-Skript, das prüft, ob die benötigten Pakete in der aktiven Python-Umgebung importierbar sind.
Usage:
    python import_test.py
"""
import sys
print('Python executable:', sys.executable)
print('Python version:', sys.version)

ok = True

try:
    import PyQt5
    from PyQt5 import QtCore
    print('PyQt5 import: OK, QT_VERSION =', QtCore.QT_VERSION_STR)
except Exception as e:
    print('PyQt5 import: FAILED ->', repr(e))
    ok = False

try:
    import pyqtgraph as pg
    print('pyqtgraph import: OK, version =', getattr(pg, '__version__', 'unknown'))
except Exception as e:
    print('pyqtgraph import: FAILED ->', repr(e))
    ok = False

try:
    import serial
    print('pyserial import: OK, version =', getattr(serial, '__version__', 'unknown'))
except Exception as e:
    print('pyserial import: FAILED ->', repr(e))
    ok = False

print('\nSummary:')
if ok:
    print('All imports OK. Du kannst das Programm starten: python main_pyqt.py')
    sys.exit(0)
else:
    print('Mindestens ein Import fehlgeschlagen. Bitte poste die Fehlermeldungen hier.')
    sys.exit(2)
