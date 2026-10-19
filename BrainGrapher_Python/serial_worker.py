import threading
import queue
import time

try:
    import serial
    from serial.serialutil import SerialException
    HAS_SERIAL = True
except Exception:
    HAS_SERIAL = False

class SerialReader(threading.Thread):
    def __init__(self, port, callback, baudrate=9600):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.callback = callback
        self._stop = threading.Event()
        self.ser = None

    def run(self):
        if not HAS_SERIAL:
            return
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
        except SerialException as e:
            print('Serial open error:', e)
            return

        while not self._stop.is_set():
            try:
                line = self.ser.readline().decode('utf-8').strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split(',')]
                vals = []
                for p in parts:
                    try:
                        vals.append(int(p))
                    except ValueError:
                        pass
                if vals:
                    self.callback(vals)
            except Exception as e:
                print('Serial read error:', e)
                time.sleep(0.1)

    def stop(self):
        self._stop.set()
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
