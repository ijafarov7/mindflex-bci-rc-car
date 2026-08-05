"""
visualizer.py
==============

Grafische Oberflaeche (PyQt5 + pyqtgraph) fuer das MindFlex-BCI-Projekt.

Zeigt an:

* Verbindungsstatus (farbige "Connection Light") und Signalqualitaet
* Anzahl empfangener Pakete
* aktuelle EEG-Werte (Attention, Meditation, Delta, Theta, Alpha, Beta,
  Gamma)
* Live-Graphen der letzten 60 Sekunden
* aktuelle Vorhersage, Konfidenz und den daraus abgeleiteten Fahrbefehl
* Genauigkeit der trainierten Modelle
* Buttons fuer Trainingsmodus, Neu-Trainieren, Modell laden und
  CSV-Export

Alle rechenintensiven/blockierenden Vorgaenge (Trainingsaufnahme,
Modelltraining) laufen in eigenen ``QThread``-Instanzen, damit die
Benutzeroberflaeche waehrenddessen fluessig (nicht "eingefroren")
bleibt. Ein zentraler ``QTimer`` treibt die Kernschleife: Daten holen,
Anzeige aktualisieren, periodisch eine Vorhersage berechnen und -- bei
gueltigem Signal -- einen Fahrbefehl an das RC-Auto senden.
"""

from __future__ import annotations

import shutil
import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from brain_processor import BrainProcessor
from car_controller import CarController
from classifier import BCIClassifier, ModelNotTrainedError
from config import CONFIG
from trainer import TrainingSession
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

_SIGNAL_GOOD_COLOR = "#2ecc71"
_SIGNAL_BAD_COLOR = "#f1c40f"
_SIGNAL_NONE_COLOR = "#e74c3c"

_CURVE_COLORS = {
    "attention": "#3498db",
    "meditation": "#9b59b6",
    "delta": "#e67e22",
    "theta": "#e74c3c",
    "alpha": "#1abc9c",
    "beta": "#2c3e50",
    "gamma": "#7f8c8d",
}


class TrainingWorker(QtCore.QThread):
    """Fuehrt eine Trainingsaufnahme in einem eigenen Thread aus.

    Verhindert, dass die 10+ Sekunden dauernde Blockier-Schleife von
    ``TrainingSession.record_label`` die GUI einfrieren laesst.

    Signals:
        progress: Wird periodisch mit ``(elapsed_seconds,
            samples_recorded)`` gesendet.
        finished_recording: Wird am Ende mit der Gesamtzahl
            aufgezeichneter Samples gesendet.
        failed: Wird bei einer Ausnahme mit der Fehlermeldung gesendet.
    """

    progress = QtCore.pyqtSignal(float, int)
    finished_recording = QtCore.pyqtSignal(int)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, session: TrainingSession, label: str, duration: float) -> None:
        """Initialisiert den Worker.

        Args:
            session: ``TrainingSession``, die die Aufnahme durchfuehrt.
            label: Aufzuzeichnendes Fahrbefehl-Label.
            duration: Aufnahmedauer in Sekunden.
        """
        super().__init__()
        self.session = session
        self.label = label
        self.duration = duration

    def run(self) -> None:
        """Thread-Einstiegspunkt: fuehrt die Aufnahme aus und meldet das Ergebnis."""
        try:
            count = self.session.record_label(
                self.label, self.duration, on_tick=lambda e, c: self.progress.emit(e, c)
            )
            self.finished_recording.emit(count or 0)
        except Exception as exc:  # noqa: BLE001 - GUI darf nie abstuerzen
            logger.exception("Fehler im Trainings-Worker")
            self.failed.emit(str(exc))


class RetrainWorker(QtCore.QThread):
    """Trainiert alle Modelle in einem eigenen Thread neu.

    Signals:
        finished_training: Wird mit dem Genauigkeits-Dict gesendet.
        failed: Wird bei einer Ausnahme mit der Fehlermeldung gesendet.
    """

    finished_training = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, classifier: BCIClassifier) -> None:
        """Initialisiert den Worker.

        Args:
            classifier: ``BCIClassifier``, dessen ``train()`` aufgerufen wird.
        """
        super().__init__()
        self.classifier = classifier

    def run(self) -> None:
        """Thread-Einstiegspunkt: trainiert alle Modelle und meldet das Ergebnis."""
        try:
            accuracies = self.classifier.train()
            self.finished_training.emit(accuracies)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fehler im Retrain-Worker")
            self.failed.emit(str(exc))


class DirectionIndicator(QtWidgets.QWidget):
    """Zeigt die aktuelle Fahrtrichtung als grossen, live gedrehten Pfeil an.

    Dient dazu, den vom Klassifikator vorhergesagten Fahrbefehl auf einen
    Blick nachvollziehen zu koennen -- unabhaengig davon, ob gerade ein
    RC-Auto angeschlossen ist oder nicht. FORWARD/BACKWARD/LEFT/RIGHT
    werden als Pfeil in die jeweilige Richtung gezeichnet, STOP als
    rotes Stopp-Symbol. Die Pfeilfarbe zeigt zusaetzlich grob die
    Konfidenz der Vorhersage an (gruen = sicher, gelb = unsicher).
    """

    # Rotationswinkel (im Uhrzeigersinn, 0 Grad = oben) je Fahrbefehl.
    _ANGLES: dict[str, int] = {
        "FORWARD": 0,
        "RIGHT": 90,
        "BACKWARD": 180,
        "LEFT": 270,
    }

    _CONFIDENT_COLOR = QtGui.QColor("#2ecc71")
    _UNCERTAIN_COLOR = QtGui.QColor("#f1c40f")
    _CONFIDENCE_THRESHOLD = 0.6

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """Initialisiert die Anzeige im neutralen "kein Signal"-Zustand.

        Args:
            parent: Optionales Eltern-Widget (Qt-Konvention).
        """
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self._label: str | None = None
        self._confidence: float = 0.0

    def set_direction(self, label: str | None, confidence: float = 0.0) -> None:
        """Aktualisiert die angezeigte Richtung und stoesst ein Neuzeichnen an.

        Args:
            label: Aktuelles Fahrbefehl-Label (``CONFIG.labels``) oder
                ``None``, wenn keine Vorhersage vorliegt (kein Signal/
                kein Modell).
            confidence: Konfidenz der Vorhersage (0.0-1.0), beeinflusst
                nur die Farbe der Anzeige.
        """
        self._label = label
        self._confidence = confidence
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt-Konvention
        """Zeichnet Hintergrund-Kreis plus Pfeil oder Stopp-Symbol.

        Args:
            event: Von Qt uebergebenes Paint-Event (ungenutzt, aber von
                der Qt-API vorgeschrieben).
        """
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            side = min(self.width(), self.height())
            radius = side / 2 - 6
            painter.translate(self.width() / 2, self.height() / 2)

            painter.setPen(QtGui.QPen(QtGui.QColor("#bdc3c7"), 2))
            painter.setBrush(QtGui.QColor("#ecf0f1"))
            painter.drawEllipse(QtCore.QPointF(0, 0), radius, radius)

            if self._label is None:
                painter.setPen(QtGui.QColor("#7f8c8d"))
                font = painter.font()
                font.setPointSize(12)
                painter.setFont(font)
                painter.drawText(
                    QtCore.QRectF(-radius, -radius, radius * 2, radius * 2),
                    QtCore.Qt.AlignCenter,
                    "--",
                )
                return

            if self._label == "STOP":
                self._draw_stop(painter, radius)
            else:
                self._draw_arrow(painter, radius)
        finally:
            painter.end()

    def _draw_stop(self, painter: QtGui.QPainter, radius: float) -> None:
        """Zeichnet das rote Stopp-Symbol in der Mitte der Anzeige.

        Args:
            painter: Aktiver ``QPainter`` (bereits auf den Mittelpunkt
                der Anzeige verschoben).
            radius: Radius des Hintergrund-Kreises.
        """
        stop_size = radius * 1.1
        painter.setPen(QtGui.QPen(QtGui.QColor("#c0392b"), 3))
        painter.setBrush(QtGui.QColor("#e74c3c"))
        stop_rect = QtCore.QRectF(-stop_size / 2, -stop_size / 2, stop_size, stop_size)
        painter.drawRoundedRect(stop_rect, 10, 10)

        painter.setPen(QtGui.QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(13)
        painter.setFont(font)
        painter.drawText(stop_rect, QtCore.Qt.AlignCenter, "STOP")

    def _draw_arrow(self, painter: QtGui.QPainter, radius: float) -> None:
        """Zeichnet einen in die vorhergesagte Richtung rotierten Pfeil.

        Args:
            painter: Aktiver ``QPainter`` (bereits auf den Mittelpunkt
                der Anzeige verschoben).
            radius: Radius des Hintergrund-Kreises, bestimmt die
                Pfeilgroesse.
        """
        color = (
            self._CONFIDENT_COLOR
            if self._confidence >= self._CONFIDENCE_THRESHOLD
            else self._UNCERTAIN_COLOR
        )

        angle = self._ANGLES.get(self._label, 0)
        painter.save()
        painter.rotate(angle)

        arrow_len = radius * 0.85
        shaft_w = radius * 0.22
        head_w = radius * 0.5

        polygon = QtGui.QPolygonF(
            [
                QtCore.QPointF(0, -arrow_len),
                QtCore.QPointF(head_w, -arrow_len * 0.35),
                QtCore.QPointF(shaft_w, -arrow_len * 0.35),
                QtCore.QPointF(shaft_w, arrow_len * 0.6),
                QtCore.QPointF(-shaft_w, arrow_len * 0.6),
                QtCore.QPointF(-shaft_w, -arrow_len * 0.35),
                QtCore.QPointF(-head_w, -arrow_len * 0.35),
            ]
        )
        painter.setPen(QtGui.QPen(color.darker(130), 2))
        painter.setBrush(color)
        painter.drawPolygon(polygon)
        painter.restore()


class MainWindow(QtWidgets.QMainWindow):
    """Hauptfenster der MindFlex-BCI-Anwendung.

    Verdrahtet ``BrainProcessor`` (Datenquelle), ``BCIClassifier``
    (Vorhersage), ``CarController`` (Aktorik) und ``TrainingSession``
    (Datenaufzeichnung) mit einer PyQt5/pyqtgraph-Oberflaeche.
    """

    def __init__(
        self,
        processor: BrainProcessor,
        classifier: BCIClassifier,
        car_controller: CarController,
        training_session: TrainingSession,
    ) -> None:
        """Initialisiert das Hauptfenster und startet die Update-Schleife.

        Args:
            processor: Liefert validierte/gepufferte EEG-Samples.
            classifier: Fuehrt Training und Vorhersage der Fahrbefehle aus.
            car_controller: Sendet Fahrbefehle an das RC-Auto.
            training_session: Zeichnet gelabelte Trainingsdaten auf.
        """
        super().__init__()
        self.processor = processor
        self.classifier = classifier
        self.car_controller = car_controller
        self.training_session = training_session

        self._training_worker: TrainingWorker | None = None
        self._retrain_worker: RetrainWorker | None = None
        self._last_prediction_time = 0.0
        self.accuracy_labels: dict[str, QtWidgets.QLabel] = {}

        self.setWindowTitle("MindFlex BCI - RC-Auto-Steuerung")
        self.resize(1150, 750)

        self._build_ui()

        if self.classifier.last_accuracies:
            self._on_retrain_finished(self.classifier.last_accuracies)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(50)  # ~20 Hz GUI-Update

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Baut alle Widgets und Layouts des Hauptfensters auf."""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)

        root_layout.addLayout(self._build_status_bar())

        content_layout = QtWidgets.QHBoxLayout()
        content_layout.addWidget(self._build_plot_widget(), 3)
        content_layout.addLayout(self._build_side_panel(), 1)
        root_layout.addLayout(content_layout)

    def _build_status_bar(self) -> QtWidgets.QHBoxLayout:
        """Baut die obere Statuszeile (Verbindung, Signalqualitaet, Pakete)."""
        layout = QtWidgets.QHBoxLayout()

        self.connection_light = QtWidgets.QLabel()
        self.connection_light.setFixedSize(20, 20)
        self._set_connection_color(_SIGNAL_NONE_COLOR)
        layout.addWidget(QtWidgets.QLabel("Verbindung:"))
        layout.addWidget(self.connection_light)

        self.signal_quality_label = QtWidgets.QLabel("Signal Quality: --")
        layout.addWidget(self.signal_quality_label)

        self.packet_count_label = QtWidgets.QLabel("Pakete: 0")
        layout.addWidget(self.packet_count_label)

        layout.addStretch(1)
        return layout

    def _build_plot_widget(self) -> pg.PlotWidget:
        """Baut den Live-Graphen fuer die letzten 60 Sekunden EEG-Daten."""
        pg.setConfigOption("background", "w")
        pg.setConfigOption("foreground", "k")

        self.plot_widget = pg.PlotWidget(title="EEG-Werte (letzte 60 Sekunden)")
        self.plot_widget.addLegend()
        self.plot_widget.setLabel("bottom", "Sekunden seit jetzt")
        self.plot_widget.setLabel("left", "Wert")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        self._curves = {}
        for name, color in _CURVE_COLORS.items():
            self._curves[name] = self.plot_widget.plot(
                [], [], pen=pg.mkPen(color=color, width=2), name=name.capitalize()
            )

        return self.plot_widget

    def _build_side_panel(self) -> QtWidgets.QVBoxLayout:
        """Baut die rechte Seitenleiste (Werte, Vorhersage, Genauigkeit, Buttons)."""
        layout = QtWidgets.QVBoxLayout()

        values_group = QtWidgets.QGroupBox("Aktuelle Werte")
        values_form = QtWidgets.QFormLayout()
        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        for name in _CURVE_COLORS:
            label = QtWidgets.QLabel("--")
            self.value_labels[name] = label
            values_form.addRow(f"{name.capitalize()}:", label)
        values_group.setLayout(values_form)
        layout.addWidget(values_group)

        prediction_group = QtWidgets.QGroupBox("Vorhersage")
        prediction_layout = QtWidgets.QVBoxLayout()

        self.direction_indicator = DirectionIndicator()
        prediction_layout.addWidget(self.direction_indicator, alignment=QtCore.Qt.AlignHCenter)

        self.prediction_label = QtWidgets.QLabel("Prediction: --")
        font = self.prediction_label.font()
        font.setPointSize(16)
        font.setBold(True)
        self.prediction_label.setFont(font)
        self.prediction_label.setAlignment(QtCore.Qt.AlignHCenter)
        self.confidence_label = QtWidgets.QLabel("Confidence: --")
        self.confidence_label.setAlignment(QtCore.Qt.AlignHCenter)
        self.command_label = QtWidgets.QLabel("Fahrbefehl: --")
        self.command_label.setAlignment(QtCore.Qt.AlignHCenter)
        prediction_layout.addWidget(self.prediction_label)
        prediction_layout.addWidget(self.confidence_label)
        prediction_layout.addWidget(self.command_label)
        prediction_group.setLayout(prediction_layout)
        layout.addWidget(prediction_group)

        accuracy_group = QtWidgets.QGroupBox("Modell-Genauigkeit")
        self.accuracy_layout = QtWidgets.QVBoxLayout()
        accuracy_group.setLayout(self.accuracy_layout)
        layout.addWidget(accuracy_group)

        controls_group = QtWidgets.QGroupBox("Training & Modell")
        controls_layout = QtWidgets.QVBoxLayout()

        self.train_button = QtWidgets.QPushButton("Training starten")
        self.train_button.clicked.connect(self._on_start_training)
        controls_layout.addWidget(self.train_button)

        self.training_progress_label = QtWidgets.QLabel("")
        controls_layout.addWidget(self.training_progress_label)

        self.retrain_button = QtWidgets.QPushButton("Modell neu trainieren")
        self.retrain_button.clicked.connect(self._on_retrain)
        controls_layout.addWidget(self.retrain_button)

        self.load_model_button = QtWidgets.QPushButton("Letztes Modell laden")
        self.load_model_button.clicked.connect(self._on_load_model)
        controls_layout.addWidget(self.load_model_button)

        self.export_button = QtWidgets.QPushButton("Daten als CSV exportieren")
        self.export_button.clicked.connect(self._on_export_csv)
        controls_layout.addWidget(self.export_button)

        controls_group.setLayout(controls_layout)
        layout.addWidget(controls_group)

        layout.addStretch(1)
        return layout

    # ------------------------------------------------------------------
    # Kernschleife (QTimer)
    # ------------------------------------------------------------------
    @safe_execute(logger)
    def _on_tick(self) -> None:
        """Wird periodisch vom QTimer aufgerufen.

        Holt neue Samples ab, aktualisiert Statusanzeige und Graphen,
        und stoesst in festen Intervallen eine neue Vorhersage an.
        """
        self.processor.poll()
        self._update_status()
        self._update_plot()

        now = time.time()
        if now - self._last_prediction_time >= CONFIG.prediction_interval_seconds:
            self._last_prediction_time = now
            self._update_prediction()

    def _update_status(self) -> None:
        """Aktualisiert Verbindungslicht, Signalqualitaet, Paketzaehler und Werte."""
        sample = self.processor.latest_sample
        self.packet_count_label.setText(f"Pakete: {self.processor.receiver.packet_count}")

        if sample is None:
            self._set_connection_color(_SIGNAL_NONE_COLOR)
            self.signal_quality_label.setText("Signal Quality: --")
            return

        self.signal_quality_label.setText(f"Signal Quality: {sample.signal_quality}")

        if not sample.is_valid:
            self._set_connection_color(_SIGNAL_NONE_COLOR)
            # Kein gueltiges Signal: keine EEG-Werte/Graphen aktualisieren.
            return
        elif sample.signal_quality == 0:
            self._set_connection_color(_SIGNAL_GOOD_COLOR)
        else:
            self._set_connection_color(_SIGNAL_BAD_COLOR)

        values = {
            "attention": sample.attention,
            "meditation": sample.meditation,
            "delta": sample.delta,
            "theta": sample.theta,
            "alpha": sample.alpha,
            "beta": sample.beta,
            "gamma": sample.gamma,
        }
        for name, value in values.items():
            self.value_labels[name].setText(f"{value:.1f}")

    def _update_plot(self) -> None:
        """Aktualisiert die Live-Graphen mit den letzten 60 Sekunden gueltiger Daten."""
        history = self.processor.get_history()
        if not history:
            return

        now = history[-1].timestamp
        times = np.array([s.timestamp - now for s in history])

        series = {
            "attention": [s.attention for s in history],
            "meditation": [s.meditation for s in history],
            "delta": [s.delta for s in history],
            "theta": [s.theta for s in history],
            "alpha": [s.alpha for s in history],
            "beta": [s.beta for s in history],
            "gamma": [s.gamma for s in history],
        }
        for name, values in series.items():
            self._curves[name].setData(times, values)

    def _update_prediction(self) -> None:
        """Berechnet (falls moeglich) eine neue Vorhersage und sendet den Fahrbefehl."""
        if not self.processor.has_valid_signal():
            self.prediction_label.setText("Prediction: (kein Signal)")
            self.confidence_label.setText("Confidence: --")
            self.command_label.setText("Fahrbefehl: --")
            self.direction_indicator.set_direction(None)
            return

        if self.classifier.model is None:
            self.prediction_label.setText("Prediction: (kein Modell trainiert)")
            self.direction_indicator.set_direction(None)
            return

        if not self.processor.has_enough_samples_for_window():
            return

        window = self.processor.get_window()
        try:
            label, confidence = self.classifier.predict(window)
        except ModelNotTrainedError:
            return

        if label is None:
            return

        self.prediction_label.setText(f"Prediction: {label}")
        self.confidence_label.setText(f"Confidence: {confidence * 100:.0f} %")
        self.command_label.setText(f"Fahrbefehl: {label}")
        self.direction_indicator.set_direction(label, confidence)

        self.car_controller.send_label(label)

    def _set_connection_color(self, color_hex: str) -> None:
        """Setzt die Hintergrundfarbe der Verbindungsanzeige.

        Args:
            color_hex: Hex-Farbcode, z. B. ``"#2ecc71"``.
        """
        self.connection_light.setStyleSheet(f"background-color: {color_hex}; border-radius: 10px;")

    # ------------------------------------------------------------------
    # Button-Handler
    # ------------------------------------------------------------------
    def _on_start_training(self) -> None:
        """Oeffnet Dialoge zur Label-/Dauer-Auswahl und startet die Aufnahme."""
        label, ok = QtWidgets.QInputDialog.getItem(
            self, "Training starten", "Fahrbefehl:", list(CONFIG.labels), 0, False
        )
        if not ok:
            return

        duration, ok = QtWidgets.QInputDialog.getInt(
            self, "Aufnahmedauer", "Sekunden:", int(CONFIG.default_training_duration), 1, 120
        )
        if not ok:
            return

        self.train_button.setEnabled(False)
        self.training_progress_label.setText(f"Trainiere: {label} ...")

        self._training_worker = TrainingWorker(self.training_session, label, float(duration))
        self._training_worker.progress.connect(self._on_training_progress)
        self._training_worker.finished_recording.connect(self._on_training_finished)
        self._training_worker.failed.connect(self._on_training_failed)
        self._training_worker.start()

    def _on_training_progress(self, elapsed: float, count: int) -> None:
        """Aktualisiert die Fortschrittsanzeige waehrend der Aufnahme."""
        self.training_progress_label.setText(f"{elapsed:.0f} s, {count} Samples ...")

    def _on_training_finished(self, count: int) -> None:
        """Wird aufgerufen, wenn die Aufnahme erfolgreich abgeschlossen ist."""
        self.training_progress_label.setText(f"Fertig: {count} Samples gespeichert.")
        self.train_button.setEnabled(True)

    def _on_training_failed(self, message: str) -> None:
        """Wird aufgerufen, wenn waehrend der Aufnahme ein Fehler auftrat."""
        self.training_progress_label.setText(f"Fehler: {message}")
        self.train_button.setEnabled(True)

    def _on_retrain(self) -> None:
        """Startet das Neu-Trainieren aller Modelle im Hintergrund."""
        self.retrain_button.setEnabled(False)
        self.retrain_button.setText("Trainiere Modelle ...")

        self._retrain_worker = RetrainWorker(self.classifier)
        self._retrain_worker.finished_training.connect(self._on_retrain_finished)
        self._retrain_worker.failed.connect(self._on_retrain_failed)
        self._retrain_worker.start()

    def _on_retrain_finished(self, accuracies: dict) -> None:
        """Aktualisiert die Genauigkeitsanzeige mit dem Ergebnis des Trainings."""
        self.retrain_button.setEnabled(True)
        self.retrain_button.setText("Modell neu trainieren")

        for name in list(self.accuracy_labels.keys()):
            widget = self.accuracy_labels.pop(name)
            self.accuracy_layout.removeWidget(widget)
            widget.deleteLater()

        if not accuracies:
            placeholder = QtWidgets.QLabel("Nicht genug Trainingsdaten.")
            self.accuracy_layout.addWidget(placeholder)
            self.accuracy_labels["_placeholder"] = placeholder
            return

        for model_name, accuracy in sorted(accuracies.items(), key=lambda kv: -kv[1]):
            label_widget = QtWidgets.QLabel(f"{model_name}: {accuracy * 100:.0f} %")
            self.accuracy_labels[model_name] = label_widget
            self.accuracy_layout.addWidget(label_widget)

    def _on_retrain_failed(self, message: str) -> None:
        """Zeigt eine Fehlermeldung an, wenn das Neu-Trainieren fehlschlug."""
        self.retrain_button.setEnabled(True)
        self.retrain_button.setText("Modell neu trainieren")
        QtWidgets.QMessageBox.warning(self, "Training fehlgeschlagen", message)

    def _on_load_model(self) -> None:
        """Laedt das zuletzt gespeicherte Modell von der Festplatte."""
        loaded = self.classifier.load_model()
        if loaded:
            self._on_retrain_finished(self.classifier.last_accuracies)
            QtWidgets.QMessageBox.information(
                self, "Modell geladen", "Letztes Modell erfolgreich geladen."
            )
        else:
            QtWidgets.QMessageBox.warning(
                self, "Kein Modell", "Es wurde kein gespeichertes Modell gefunden."
            )

    def _on_export_csv(self) -> None:
        """Exportiert die Trainingsdaten-CSV an einen vom Benutzer gewaehlten Ort."""
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Trainingsdaten exportieren", "training_data_export.csv", "CSV-Dateien (*.csv)"
        )
        if not target:
            return
        try:
            shutil.copy(self.training_session.csv_path, target)
            QtWidgets.QMessageBox.information(self, "Export erfolgreich", f"Gespeichert unter:\n{target}")
        except OSError as exc:
            logger.exception("Fehler beim Exportieren der Trainingsdaten")
            QtWidgets.QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt-Konvention
        """Stoppt beim Schliessen des Fensters alle Hintergrunddienste sauber.

        Args:
            event: Das von Qt uebergebene Schliessen-Event.
        """
        try:
            self.car_controller.disconnect()
        except Exception:  # noqa: BLE001
            logger.exception("Fehler beim Trennen des RC-Autos")
        try:
            self.processor.receiver.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Fehler beim Stoppen des SerialReceiver")
        event.accept()
