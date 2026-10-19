#!/usr/bin/env python3
"""
PyQtGraph-based Brain Grapher MVP
- Shows a multi-channel time-series plot and simple monitors
- Uses mock data by default; optional serial input via --serial PORT

Run:
    python main_pyqt.py          # mock data
    python main_pyqt.py --serial /dev/tty.usbmodemXXXX

Dependencies: PyQt5, pyqtgraph, pyserial (if using serial)
"""
import sys
import time
import argparse
import threading
from collections import deque
import math
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

try:
    import serial
    from serial.serialutil import SerialException
    HAS_PYSERIAL = True
except Exception:
    HAS_PYSERIAL = False

from data_model import Channel
from serial_worker import SerialReader

CHANNEL_NAMES = [
    "Signal Quality", "Attention", "Meditation", "Delta", "Theta",
    "Low Alpha", "High Alpha", "Low Beta", "High Beta", "Low Gamma", "High Gamma"
]

CHANNEL_COLORS = [
    (0,0,0), (100,100,100), (50,50,50), (219,211,42), (245,80,71),
    (237,0,119), (212,0,149), (158,18,188), (116,23,190), (39,25,159), (23,26,153)
]

class BrainGrapher(QtWidgets.QMainWindow):
    def __init__(self, use_serial_port=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Brain Grapher - PyQtGraph")
        self.resize(1200, 800)

        # Data model
        self.channels = []
        for i, name in enumerate(CHANNEL_NAMES):
            ch = Channel(name=name, color=CHANNEL_COLORS[i])
            # manual overrides matching original
            if i == 0:
                ch.minValue = 0; ch.maxValue = 200; ch.allowGlobal = False
            if i in (1,2):
                ch.minValue = 0; ch.maxValue = 100; ch.allowGlobal = False
            self.channels.append(ch)

        self.globalMax = 0
        self.packetCount = 0

        # Serial reader (optional)
        self.serial_reader = None
        if use_serial_port and HAS_PYSERIAL:
            try:
                self.serial_reader = SerialReader(use_serial_port, self.handle_packet)
                self.serial_reader.start()
            except SerialException as e:
                print('Serial start failed:', e)
                self.serial_reader = None

        # UI
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Top: graph
        self.plotWidget = pg.PlotWidget(title="EEG Bands")
        self.plotWidget.addLegend(offset=(10,10))
        self.plotWidget.showGrid(x=True, y=True)
        layout.addWidget(self.plotWidget, 3)

        self.curves = []
        for i, ch in enumerate(self.channels):
            pen = pg.mkPen(color=ch.color, width=2)
            curve = self.plotWidget.plot([], [], pen=pen, name=ch.name)
            self.curves.append(curve)

        # Bottom: monitors and controls
        bottom = QtWidgets.QHBoxLayout()
        layout.addLayout(bottom, 1)

        # Monitors area (scroll area to hold many monitors)
        monitors_widget = QtWidgets.QWidget()
        monitors_layout = QtWidgets.QHBoxLayout(monitors_widget)
        self.monitor_bars = []
        for i, ch in enumerate(self.channels[1:]):  # skip signal quality
            w = pg.PlotWidget(title=ch.name)
            w.setMouseEnabled(x=False, y=False)
            w.hideAxis('bottom'); w.hideAxis('left')
            bar = pg.BarGraphItem(x=[0], height=[0], width=0.6, brush=pg.mkBrush(ch.color))
            w.addItem(bar)
            monitors_layout.addWidget(w)
            self.monitor_bars.append((w, bar))
        bottom.addWidget(monitors_widget, 4)

        # Controls
        ctrl = QtWidgets.QVBoxLayout()
        bottom.addLayout(ctrl, 1)

        self.conn_label = QtWidgets.QLabel('Connection: N/A')
        self.packet_label = QtWidgets.QLabel('Packets: 0')
        ctrl.addWidget(self.conn_label)
        ctrl.addWidget(self.packet_label)

        self.scale_mode = 'Local'
        self.scale_btn = QtWidgets.QPushButton('Toggle Scale (Local)')
        self.scale_btn.clicked.connect(self.toggle_scale)
        ctrl.addWidget(self.scale_btn)

        self.time_window = 10.0  # seconds
        self.time_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.time_slider.setMinimum(1); self.time_slider.setMaximum(60)
        self.time_slider.setValue(int(self.time_window))
        self.time_slider.valueChanged.connect(self.change_time_window)
        ctrl.addWidget(QtWidgets.QLabel('Time window (s)'))
        ctrl.addWidget(self.time_slider)

        # Timer for updates
        self._start_time = time.time()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(50)  # 20 Hz

        # Mock data timer
        self.mock_timer = QtCore.QTimer()
        self.mock_timer.timeout.connect(self.generate_mock)
        self.mock_timer.start(200)

    def handle_packet(self, values):
        # values: list of ints
        self.packetCount += 1
        now = time.time()
        if len(values) < len(self.channels):
            return
        for i, v in enumerate(values):
            val = int(v)
            if (int(values[0]) == 200) and (i > 2):
                val = 0
            self.channels[i].append_point(now, val)
            if val > self.channels[i].maxValue:
                self.channels[i].maxValue = val
            if val < self.channels[i].minValue:
                self.channels[i].minValue = val

    def generate_mock(self):
        now = time.time()
        t = now
        signal = 0 if int(t) % 5 != 0 else 200
        attention = int((math.sin(t * 0.7) * 0.5 + 0.5) * 100)
        meditation = int((math.cos(t * 0.5) * 0.5 + 0.5) * 100)
        bands = [int((math.sin(t * (0.3 + i * 0.1)) * 0.5 + 0.5) * (20 + i * 10)) for i in range(3, 11)]
        vals = [signal, attention, meditation] + bands
        self.handle_packet(vals)

    def update_ui(self):
        # Update globalMax
        self.globalMax = 0
        for ch in self.channels[3:]:
            if ch.maxValue > self.globalMax:
                self.globalMax = ch.maxValue

        # Update plot
        now = time.time()
        left = now - self.time_window
        for i, ch in enumerate(self.channels):
            times, vals = ch.get_points_since(left)
            if len(times) == 0:
                self.curves[i].setData([], [])
                continue
            # scale Y depending on scale mode
            if self.scale_mode == 'Global' and i > 2 and self.globalMax > 0:
                ys = [v / float(self.globalMax) for v in vals]
                # map to 0..1 then scale to 0..100 for visibility
                ys = [y * 100 for y in ys]
            else:
                minv = ch.minValue if ch.minValue != ch.maxValue else ch.minValue - 1
                maxv = ch.maxValue if ch.maxValue != ch.minValue else ch.maxValue + 1
                ys = [ (v - minv) / (maxv - minv) * 100 for v in vals ]
            xs = [t - now for t in times]
            xs = [x + self.time_window for x in xs]  # shift to 0..time_window
            self.curves[i].setData(xs, ys)

        # Update monitors (show latest value)
        for idx, (w, bar) in enumerate(self.monitor_bars):
            ch = self.channels[idx+1]
            latest = ch.get_latest_value()
            if latest is None:
                height = 0
            else:
                if self.scale_mode == 'Global' and ch.allowGlobal and self.globalMax>0:
                    height = float(latest) / self.globalMax * 100
                else:
                    minv = ch.minValue if ch.minValue != ch.maxValue else ch.minValue - 1
                    maxv = ch.maxValue if ch.maxValue != ch.minValue else ch.maxValue + 1
                    height = float(latest - minv) / (maxv - minv) * 100
            # replace bar
            w.clear()
            b = pg.BarGraphItem(x=[0], height=[height], width=0.6, brush=pg.mkBrush(ch.color))
            w.addItem(b)

        # Connection and packet labels
        sig = self.channels[0].get_latest_value() or 200
        if sig == 200:
            self.conn_label.setText('Connection: NO SIGNAL')
            self.conn_label.setStyleSheet('color: red')
        elif sig == 0:
            self.conn_label.setText('Connection: GOOD')
            self.conn_label.setStyleSheet('color: green')
        else:
            self.conn_label.setText(f'Connection: {sig}')
            self.conn_label.setStyleSheet('color: orange')

        self.packet_label.setText(f'Packets: {self.packetCount}')

    def toggle_scale(self):
        if self.scale_mode == 'Local':
            self.scale_mode = 'Global'
        else:
            self.scale_mode = 'Local'
        self.scale_btn.setText(f'Toggle Scale ({self.scale_mode})')

    def change_time_window(self, v):
        self.time_window = float(v)

    def closeEvent(self, event):
        if self.serial_reader:
            self.serial_reader.stop()
            self.serial_reader.join(timeout=1)
        event.accept()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--serial', help='Serial port to read (e.g. /dev/tty.usbmodemXXXX)', default=None)
    return p.parse_args()


def main():
    args = parse_args()
    app = QtWidgets.QApplication(sys.argv)
    gw = BrainGrapher(use_serial_port=args.serial)
    gw.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
