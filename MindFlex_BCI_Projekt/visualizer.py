"""
visualizer.py
==============

Grafische Oberflaeche (PyQt5 + pyqtgraph) fuer das MindFlex-BCI-Projekt.

Zeigt an:

* Verbindungsstatus (farbige "Connection Light") und Signalqualitaet
* Anzahl empfangener Pakete
* aktuelle EEG-Werte (Attention, Meditation, Delta, Theta, Alpha, Beta,
  Gamma), beschriftet mit Frequenzbereich bzw. Wertebereich
* Live-Graphen des gesamten Verlaufs seit Messbeginn
* Umschalter fuer helle und dunkle Darstellung
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

import csv
import math
import shutil
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from brain_processor import BrainProcessor
from car_controller import CarController
from classifier import BCIClassifier, ModelNotTrainedError
from config import (
    CONFIG,
    PROFIL_BASIS,
    TRAINING_CSV_COLUMNS,
    saubere_profilbezeichnung,
    vorhandene_profile,
)
from trainer import TrainingSession
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


# Die Bausteine der Oberflaeche liegen in eigenen Dateien, damit diese
# hier ueberschaubar bleibt. Sie werden weiterhin von hier aus
# weitergereicht, damit bestehende Importe unveraendert funktionieren.
from gui_dialoge import (
    KalibrierungDialog,
    KonfusionsmatrixDialog,
    RohdatenDialog,
    TestaufnahmenDialog,
    TrainingsdatenDialog,
)
from gui_konstanten import *  # noqa: F403 - Farben und Kennzahlen
from gui_konstanten import (
    _BALKEN_GLAETTUNG,
    _CURVE_BESCHREIBUNG,
    _CURVE_COLORS,
    _CURVE_KURZ,
    _CURVE_LABELS,
    _KANTENGLAETTUNG,
    _LINIEN_BREITE,
    _LIVE_TOLERANZ,
    _LOCK_OFF_COLOR,
    _LOCK_ON_COLOR,
    _MANUELL_COLOR,
    _MANUELL_TASTEN,
    _PAUSE_AB_SEKUNDEN,
    _PAUSE_BALKEN_PX,
    _PAUSE_SCHRITT,
    _PAUSE_TEXT,
    _PLOT_TITEL,
    _SIGNAL_BAD_COLOR,
    _SIGNAL_GOOD_COLOR,
    _SIGNAL_NONE_COLOR,
    _THEMES,
    _VERBINDUNG_TIMEOUT,
    _X_ACHSE_NAME,
    _PLOT_TITEL_LINEAR,
    _PLOT_TITEL_LOG,
    _ESENSE_ACHSE_EINHEIT,
    _ESENSE_ACHSE_NAME,
    _ESENSE_KANAELE,
    _ESENSE_MAX,
    _ESENSE_MIN,
    _Y_LOG_STANDARD,
    _Y_SPIELRAUM,
    _LOG_BODEN,
    _Y_ACHSE_EINHEIT,
    _Y_ACHSE_NAME,
    _ZEITFENSTER,
)
from gui_widgets import (
    BandMonitor,
    DirectionIndicator,
    Hinweiszeile,
    ZahlenAchse,
    zahl_deutsch,
)
from gui_worker import (
    ReconnectWorker,
    RetrainWorker,
    TestaufnahmeWorker,
    TrainingWorker,
)


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

        # Vollstaendiger Messverlauf seit Programmstart. Der BrainProcessor
        # haelt nur ein gleitendes Fenster von CONFIG.history_seconds vor --
        # fuer die Anzeige "alles seit Beginn" wird hier zusaetzlich
        # mitgeschrieben. Ein Sample pro Sekunde, das bleibt auch nach
        # Stunden handhabbar.
        self._verlauf_zeit: list[float] = []
        self._verlauf_werte: dict[str, list[float]] = {name: [] for name in _CURVE_COLORS}
        self._letzter_zeitstempel: float | None = None

        # Zwei getrennte Uhren:
        #
        # * Laufzeit -- seit dem Programmstart, laeuft immer.
        # * Messungszeit -- nur die Zeit, in der tatsaechlich gemessen
        #   wurde. Sie ist zugleich die Zeitachse des Graphen und steht
        #   still, solange pausiert ist.
        self._programmstart = time.time()
        # Echter Empfangszeitpunkt des zuletzt eingezeichneten Messwerts.
        # Daraus ergibt sich, wie weit die Messungszeit beim naechsten Wert
        # vorrueckt -- und ob dazwischen eine Pause lag.
        self._letzte_probe_echt: float | None = None

        # Dunkle Darstellung ist der Standard -- die Oberflaeche laeuft
        # meist in abgedunkelten Raeumen, und der Graph ist auf dunklem
        # Grund kontrastreicher. Umschaltbar unter Ansicht.
        self._dunkelmodus = True

        # Skalierung der Kanal-Kacheln: False heisst, jeder Balken nutzt
        # seinen eigenen Wertebereich. Umschaltbar unter Ansicht.
        self._globale_skala = False

        # Handbetrieb ueber die Tastatur.
        self._manuell_aktiv = False
        self._gedrueckte_tasten: set[int] = set()

        # Verbindungsueberwachung: Woran erkennt man, dass nichts mehr
        # ankommt? Am Paketzaehler, der stehen bleibt.
        self._letzter_paketstand = -1
        self._letzte_paketzeit = time.time()

        # Schmale Balken mit der Aufschrift "Messung pausiert", je Pause
        # ein Eintrag mit allen zugehoerigen Qt-Elementen. Die offene Pause
        # ist bereits eingezeichnet, ihre Dauer steht aber noch nicht fest.
        self._pausen_marker: list[dict] = []
        self._offene_pause: dict | None = None

        # Groesster bisher gemessener Wert. Begrenzt den Ausschnitt nach
        # oben, damit man beim Herauszoomen die Kurven nicht aus den Augen
        # verliert.
        self._groesster_wert = 0.0

        # Logarithmische Y-Achse im Verlaufsgraphen. Muss vor dem Aufbau der
        # Menueleiste stehen: Das Ankreuzen des Menueeintrags loest schon
        # beim Aufbau das Signal aus.
        self._y_log = _Y_LOG_STANDARD

        # Aufzeichnung von Hand angehalten.
        self._messung_pausiert = False

        # Fenster mit den Rohdaten vom Headset. Wird beim ersten Oeffnen
        # angelegt und danach wiederverwendet, damit es seinen Inhalt behaelt.
        self._rohdaten_dialog: RohdatenDialog | None = None

        self.setWindowTitle("MindFlex BCI - RC-Auto-Steuerung")
        self._fenster_an_bildschirm_anpassen()

        self._build_ui()

        if self.classifier.last_accuracies:
            self._on_retrain_finished(self.classifier.last_accuracies)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(50)  # ~20 Hz GUI-Update

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------
    def _fenster_an_bildschirm_anpassen(self) -> None:
        """Waehlt eine Fenstergroesse, die auf den Bildschirm passt.

        Feste Groessen wie 1150x750 sind auf einem 13-Zoll-MacBook zu
        hoch: Nach Abzug von Menueleiste und Dock bleiben oft nur rund
        800 Punkte, und der untere Teil des Fensters liegt ausserhalb des
        sichtbaren Bereichs. Deshalb wird die Wunschgroesse an dem
        gemessen, was tatsaechlich zur Verfuegung steht.
        """
        wunsch_breite, wunsch_hoehe = 1150, 780

        bildschirm = QtWidgets.QApplication.primaryScreen()
        if bildschirm is None:
            self.resize(wunsch_breite, wunsch_hoehe)
            return

        # availableGeometry laesst Menueleiste und Dock bereits aussen vor.
        verfuegbar = bildschirm.availableGeometry()
        breite = min(wunsch_breite, verfuegbar.width() - 40)
        hoehe = min(wunsch_hoehe, verfuegbar.height() - 40)

        # Untergrenze, damit das Fenster bedienbar bleibt; darunter
        # uebernimmt der Scrollbereich der Seitenleiste.
        self.setMinimumSize(760, 480)
        self.resize(breite, hoehe)
        self.move(
            verfuegbar.left() + (verfuegbar.width() - breite) // 2,
            verfuegbar.top() + (verfuegbar.height() - hoehe) // 2,
        )
        logger.info(
            "Fenster %dx%d bei verfuegbarem Bildschirm %dx%d.",
            breite,
            hoehe,
            verfuegbar.width(),
            verfuegbar.height(),
        )

    def _build_ui(self) -> None:
        """Baut alle Widgets und Layouts des Hauptfensters auf."""
        self._build_menu_bar()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)

        root_layout.addLayout(self._build_status_bar())

        # Links der Verlauf, darunter die Kacheln je Kanal. Rechts die
        # Seitenleiste mit Profil, Vorhersage und Training.
        linke_seite = QtWidgets.QWidget()
        links = QtWidgets.QVBoxLayout(linke_seite)
        links.setContentsMargins(0, 0, 0, 0)
        links.addWidget(self._build_graph_bereich(), 1)
        links.addLayout(self._build_graph_leiste())
        links.addWidget(self._build_band_monitors())

        # Teiler statt festem Verhaeltnis: Auf einem kleinen Bildschirm
        # will man dem Graphen mehr Platz geben, auf einem grossen der
        # Seitenleiste. Die Aufteilung bleibt ueber Programmstarts hinweg
        # erhalten, siehe _zustand_laden und closeEvent.
        self.teiler = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.teiler.addWidget(linke_seite)
        self.teiler.addWidget(self._build_side_panel())
        self.teiler.setStretchFactor(0, 3)
        self.teiler.setStretchFactor(1, 1)
        self.teiler.setChildrenCollapsible(False)
        # Startaufteilung. Ohne feste Vorgabe bekommt die Seitenleiste zu
        # wenig Breite, und die laengeren Beschriftungen werden
        # abgeschnitten statt umgebrochen.
        self.teiler.setSizes([900, 330])
        root_layout.addWidget(self.teiler)

        # Beide Modi laufen ueber denselben Weg, damit die helle
        # Darstellung nicht von pyqtgraph-Voreinstellungen abhaengt.
        self._apply_theme()

        # Haken im Menue nachziehen, ohne dabei das Umschalten erneut
        # auszuloesen -- das Theme steht ja schon.
        self.dark_mode_action.blockSignals(True)
        self.dark_mode_action.setChecked(self._dunkelmodus)
        self.dark_mode_action.blockSignals(False)

        self._profil_menue_aktualisieren()

        # Der Tastaturfilter haengt an der ganzen Anwendung, damit die
        # Fahrtasten unabhaengig davon ankommen, welches Bedienelement
        # gerade den Fokus hat.
        anwendung = QtWidgets.QApplication.instance()
        if anwendung is not None:
            anwendung.installEventFilter(self)

        self._zustand_laden()

        # Anzeigen gleich beim Aufbau richtig setzen, nicht erst beim
        # ersten Timer-Tick.
        self._set_car_status()
        self._update_profil_info()

    def _zustand_laden(self) -> None:
        """Stellt Fenstergroesse und Aufteilung der letzten Sitzung wieder her.

        Ohne das startet das Fenster jedes Mal in der Vorgabegroesse, und
        wer sich die Aufteilung zwischen Graph und Seitenleiste einmal
        eingerichtet hat, macht es beim naechsten Start wieder.
        """
        einstellungen = QtCore.QSettings("MindFlex-BCI", "Oberflaeche")

        geometrie = einstellungen.value("fenster/geometrie")
        if geometrie is not None:
            self.restoreGeometry(geometrie)
            # Sicherheitsnetz: Wurde zuletzt an einem grossen Monitor
            # gearbeitet und haengt jetzt keiner dran, laege das Fenster
            # ausserhalb des sichtbaren Bereichs.
            bildschirm = QtWidgets.QApplication.primaryScreen()
            if bildschirm is not None:
                verfuegbar = bildschirm.availableGeometry()
                if not verfuegbar.intersects(self.geometry()):
                    logger.info("Gespeicherte Fensterlage liegt ausserhalb -- neu zentriert.")
                    self._fenster_an_bildschirm_anpassen()

        teiler = einstellungen.value("fenster/teiler")
        if teiler is not None:
            self.teiler.restoreState(teiler)

        graph_teiler = einstellungen.value("fenster/graphteiler")
        if graph_teiler is not None:
            self.graph_teiler.restoreState(graph_teiler)

        dunkel = einstellungen.value("ansicht/dunkelmodus")
        if dunkel is not None:
            # QSettings liefert je nach Plattform Text statt bool zurueck.
            gewuenscht = dunkel in (True, "true", "True", 1, "1")
            if gewuenscht != self._dunkelmodus:
                self.dark_mode_action.setChecked(gewuenscht)

        log = einstellungen.value("ansicht/y_log")
        if log is not None:
            gewuenscht = log in (True, "true", "True", 1, "1")
            if gewuenscht != self._y_log:
                self.y_log_action.setChecked(gewuenscht)
                self.y_linear_action.setChecked(not gewuenscht)

    def _zustand_speichern(self) -> None:
        """Merkt sich Fenstergroesse, Aufteilung und Darstellung."""
        einstellungen = QtCore.QSettings("MindFlex-BCI", "Oberflaeche")
        einstellungen.setValue("fenster/geometrie", self.saveGeometry())
        einstellungen.setValue("fenster/teiler", self.teiler.saveState())
        einstellungen.setValue("fenster/graphteiler", self.graph_teiler.saveState())
        einstellungen.setValue("ansicht/dunkelmodus", self._dunkelmodus)
        einstellungen.setValue("ansicht/y_log", self._y_log)

    def _build_menu_bar(self) -> None:
        """Baut die Menueleiste.

        Alles, was man waehrend einer Sitzung selten braucht, liegt hier
        statt als Knopf in der Seitenleiste -- die war mit acht Knoepfen
        untereinander unuebersichtlich geworden. In der Leiste bleiben nur
        die beiden Dinge, die man staendig anfasst: Training starten und
        die Fahrsperre.

        Unter macOS wandert die Leiste automatisch nach ganz oben in die
        Systemmenuezeile, das ist dort so ueblich.
        """
        leiste = self.menuBar()

        # --- Datei ---
        datei = leiste.addMenu("&Datei")

        self.export_action = QtWidgets.QAction("Trainingsdaten als CSV exportieren …", self)
        self.export_action.triggered.connect(self._on_export_csv)
        datei.addAction(self.export_action)

        datei.addSeparator()

        beenden = QtWidgets.QAction("Beenden", self)
        beenden.setShortcut(QtGui.QKeySequence.Quit)
        beenden.triggered.connect(self.close)
        datei.addAction(beenden)

        # --- Profil ---
        profil = leiste.addMenu("&Profil")
        self.profil_menue = profil

        # Oben die Liste der vorhandenen Profile zum Umschalten. Sie wird
        # in _profil_menue_aktualisieren() gefuellt und bei jedem neuen
        # Profil neu aufgebaut.
        self.profil_liste_aktionen: list[QtWidgets.QAction] = []
        self.profil_trenner = profil.addSeparator()

        self.neues_profil_action = QtWidgets.QAction("Neues Profil anlegen …", self)
        self.neues_profil_action.triggered.connect(self._on_neues_profil)
        profil.addAction(self.neues_profil_action)

        profil.addSeparator()

        # Wird beim Aufnehmen scharf geschaltet, solange es etwas zu
        # widerrufen gibt.
        self.undo_action = QtWidgets.QAction("Letzte Aufnahme löschen", self)
        self.undo_action.triggered.connect(self._on_letzte_aufnahme_loeschen)
        self.undo_action.setEnabled(False)
        profil.addAction(self.undo_action)

        # --- Modell ---
        modell = leiste.addMenu("&Modell")

        self.train_action = QtWidgets.QAction("Training starten …", self)
        self.train_action.triggered.connect(self._on_start_training)
        modell.addAction(self.train_action)

        self.kalibrierung_action = QtWidgets.QAction("Kalibrierung …", self)
        self.kalibrierung_action.setToolTip(
            "Nimmt jeden Befehl in jeder Reihenfolge auf, je 15 s, mit Ansage."
        )
        self.kalibrierung_action.triggered.connect(self._on_kalibrierung)
        modell.addAction(self.kalibrierung_action)

        self.testaufnahme_action = QtWidgets.QAction(
            "Einfache Aufnahme (ohne Training) …", self
        )
        self.testaufnahme_action.setToolTip(
            "Zeichnet einen Zeitraum nur zum Ansehen auf. Die Daten landen "
            "in einer eigenen Datei und fließen nie ins Modell ein."
        )
        self.testaufnahme_action.triggered.connect(self._on_testaufnahme)
        modell.addAction(self.testaufnahme_action)

        modell.addSeparator()

        self.retrain_action = QtWidgets.QAction("Modell neu trainieren", self)
        self.retrain_action.triggered.connect(self._on_retrain)
        modell.addAction(self.retrain_action)

        self.load_model_action = QtWidgets.QAction("Letztes Modell laden", self)
        self.load_model_action.triggered.connect(self._on_load_model)
        modell.addAction(self.load_model_action)

        modell.addSeparator()

        self.konfusion_action = QtWidgets.QAction("Verwechslungen ansehen …", self)
        self.konfusion_action.setToolTip(
            "Zeigt, welche Fahrbefehle das Modell miteinander verwechselt."
        )
        self.konfusion_action.triggered.connect(self._on_konfusion_ansehen)
        modell.addAction(self.konfusion_action)

        self.testdaten_action = QtWidgets.QAction("Einfache Aufnahmen ansehen …", self)
        self.testdaten_action.setToolTip(
            "Zeigt die Aufnahmen, die nur zum Ansehen gemacht wurden."
        )
        self.testdaten_action.triggered.connect(self._on_testaufnahmen_ansehen)
        modell.addAction(self.testdaten_action)

        self.daten_action = QtWidgets.QAction("Trainingsdaten ansehen …", self)
        self.daten_action.setToolTip(
            "Zeigt, wie viel je Fahrbefehl aufgezeichnet wurde und was fehlt."
        )
        self.daten_action.triggered.connect(self._on_trainingsdaten_ansehen)
        modell.addAction(self.daten_action)

        # --- Ansicht ---
        ansicht = leiste.addMenu("&Ansicht")

        self.dark_mode_action = QtWidgets.QAction("Dunkle Darstellung", self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.toggled.connect(self._on_dark_mode_toggled)
        ansicht.addAction(self.dark_mode_action)

        ansicht.addSeparator()

        self.jetzt_action = QtWidgets.QAction("Zur aktuellen Messung springen", self)
        self.jetzt_action.triggered.connect(self._on_zum_jetzt)
        ansicht.addAction(self.jetzt_action)

        self.pause_action = QtWidgets.QAction("Aufzeichnung anhalten", self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._on_pause_action)
        ansicht.addAction(self.pause_action)

        ansicht.addSeparator()

        self.monitore_action = QtWidgets.QAction("Kanal-Kacheln anzeigen", self)
        self.monitore_action.setCheckable(True)
        self.monitore_action.setChecked(True)
        self.monitore_action.toggled.connect(self._on_monitore_umgeschaltet)
        ansicht.addAction(self.monitore_action)

        # Skalierung der Balken. Als Gruppe, damit sich die beiden
        # Eintraege gegenseitig ausschliessen.
        skala = ansicht.addMenu("Skalierung der Kacheln")
        gruppe = QtWidgets.QActionGroup(self)
        gruppe.setExclusive(True)

        self.skala_einzeln_action = QtWidgets.QAction("Je Kanal eigener Bereich", self)
        self.skala_einzeln_action.setCheckable(True)
        self.skala_einzeln_action.setChecked(True)
        self.skala_einzeln_action.setToolTip(
            "Jeder Balken nutzt die volle Höhe für seinen eigenen Wertebereich."
        )
        gruppe.addAction(self.skala_einzeln_action)
        skala.addAction(self.skala_einzeln_action)

        self.skala_global_action = QtWidgets.QAction("Gemeinsame Skala", self)
        self.skala_global_action.setCheckable(True)
        self.skala_global_action.setToolTip(
            "Alle Balken teilen sich eine Skala — vergleichbar, aber "
            "kleine Bänder verschwinden fast."
        )
        gruppe.addAction(self.skala_global_action)
        skala.addAction(self.skala_global_action)

        self.skala_global_action.toggled.connect(self._on_skala_umgeschaltet)

        ansicht.addSeparator()

        # Skalierung der Y-Achse im Verlaufsgraphen. Siehe _Y_LOG_STANDARD
        # in gui_konstanten.py: Linear bestimmt der groesste Kanal allein
        # den Massstab und drueckt die anderen auf die Nulllinie.
        y_skala = ansicht.addMenu("Y-Achse des Graphen")
        y_gruppe = QtWidgets.QActionGroup(self)
        y_gruppe.setExclusive(True)

        self.y_log_action = QtWidgets.QAction("Logarithmisch", self)
        self.y_log_action.setCheckable(True)
        self.y_log_action.setChecked(_Y_LOG_STANDARD)
        self.y_log_action.setToolTip(
            "Jede Zehnerpotenz gleich hoch — alle sieben Kanäle sind "
            "gleichzeitig zu sehen, auch Attention und Meditation."
        )
        y_gruppe.addAction(self.y_log_action)
        y_skala.addAction(self.y_log_action)

        self.y_linear_action = QtWidgets.QAction("Linear", self)
        self.y_linear_action.setCheckable(True)
        self.y_linear_action.setChecked(not _Y_LOG_STANDARD)
        self.y_linear_action.setToolTip(
            "Echte Größenverhältnisse — dafür verschwinden die kleinen "
            "Kanäle neben Delta."
        )
        y_gruppe.addAction(self.y_linear_action)
        y_skala.addAction(self.y_linear_action)

        self.y_log_action.toggled.connect(self._on_y_skala_umgeschaltet)

        ansicht.addSeparator()

        alle_an = QtWidgets.QAction("Alle Kurven einblenden", self)
        alle_an.triggered.connect(lambda: self._alle_kurven_setzen(True))
        ansicht.addAction(alle_an)

        alle_aus = QtWidgets.QAction("Alle Kurven ausblenden", self)
        alle_aus.triggered.connect(lambda: self._alle_kurven_setzen(False))
        ansicht.addAction(alle_aus)

        ansicht.addSeparator()

        self.rohdaten_action = QtWidgets.QAction("Rohdaten vom Headset …", self)
        self.rohdaten_action.setToolTip(
            "Zeigt live jede Zeile, die vom Headset ankommt."
        )
        self.rohdaten_action.triggered.connect(self._on_rohdaten_ansehen)
        ansicht.addAction(self.rohdaten_action)

        # --- Fahren ---
        fahren = leiste.addMenu("&Fahren")

        self.drive_lock_action = QtWidgets.QAction("Fahrsperre", self)
        self.drive_lock_action.setCheckable(True)
        self.drive_lock_action.setChecked(True)
        self.drive_lock_action.setToolTip(
            "Vorhersagen laufen weiter, es geht nur kein Fahrbefehl hinaus."
        )
        self.drive_lock_action.toggled.connect(self._on_drive_lock_action)
        fahren.addAction(self.drive_lock_action)

        self.manuell_action = QtWidgets.QAction("Manuell fahren", self)
        self.manuell_action.setCheckable(True)
        self.manuell_action.setToolTip(
            "Mit W A S D oder den Pfeiltasten fahren. Das Auto fährt nur, "
            "solange eine Taste gedrückt bleibt."
        )
        self.manuell_action.toggled.connect(self._on_manuell_action)
        fahren.addAction(self.manuell_action)

        fahren.addSeparator()

        self.reconnect_action = QtWidgets.QAction("RC-Auto neu verbinden", self)
        self.reconnect_action.triggered.connect(self._on_auto_neu_verbinden)
        fahren.addAction(self.reconnect_action)

    @safe_execute(logger)
    def _profil_menue_aktualisieren(self) -> None:
        """Baut die Profilliste im Menue neu auf.

        Die Auswahl gibt es doppelt: als Klappliste in der Seitenleiste und
        hier im Menue. Beide zeigen denselben Zustand -- ein Klick im Menue
        stellt die Klappliste um, und deren Wechselsignal erledigt dann die
        eigentliche Umstellung.
        """
        for aktion in self.profil_liste_aktionen:
            self.profil_menue.removeAction(aktion)
        self.profil_liste_aktionen.clear()

        gruppe = QtWidgets.QActionGroup(self)
        gruppe.setExclusive(True)
        aktuell = self.profil_box.currentText()

        for name in vorhandene_profile():
            aktion = QtWidgets.QAction(name, self)
            aktion.setCheckable(True)
            aktion.setChecked(name == aktuell)
            aktion.triggered.connect(
                lambda _geklickt=False, gewaehlt=name: self.profil_box.setCurrentText(gewaehlt)
            )
            gruppe.addAction(aktion)
            self.profil_menue.insertAction(self.profil_trenner, aktion)
            self.profil_liste_aktionen.append(aktion)

    def _build_graph_leiste(self) -> QtWidgets.QHBoxLayout:
        """Baut die schmale Knopfreihe direkt unter dem Graphen."""
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 2, 0, 2)

        self.pause_button = QtWidgets.QPushButton()
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._on_messung_pausieren)
        layout.addWidget(self.pause_button)

        # Wer im Verlauf zurueckgescrollt hat, findet ohne diesen Knopf
        # nur muehsam zurueck ans aktuelle Ende.
        self.jetzt_button = QtWidgets.QPushButton("Zur aktuellen Messung")
        self.jetzt_button.setToolTip(
            "Springt zurück ans Ende des Verlaufs und läuft wieder mit."
        )
        self.jetzt_button.clicked.connect(self._on_zum_jetzt)
        layout.addWidget(self.jetzt_button)

        self.verlauf_status_label = QtWidgets.QLabel("")
        layout.addWidget(self.verlauf_status_label)

        layout.addStretch(1)

        # Die Messungszeit steht hier und nicht oben bei der Laufzeit: Sie
        # ist die Zeitachse des Graphen darueber und haengt am Knopf links
        # daneben -- angehalten steht sie still.
        self.messzeit_label = QtWidgets.QLabel("Messungszeit: --")
        self.messzeit_label.setToolTip(
            "Nur die Zeit, in der tatsächlich gemessen wurde — ohne Pausen.\n"
            "Entspricht der Zeitachse des Graphen."
        )
        layout.addWidget(self.messzeit_label)

        # Beschriftung gleich beim Aufbau setzen.
        self._on_messung_pausieren(False)
        return layout

    @safe_execute(logger)
    def _on_messung_pausieren(self, pausiert: bool) -> None:
        """Haelt die Aufzeichnung an oder setzt sie fort.

        Angehalten heisst: Der Graph waechst nicht weiter. Empfangen und
        angezeigt werden die Werte trotzdem -- die Kacheln und die
        Vorhersage laufen also weiter, nur der Verlauf steht still.

        Beim Fortsetzen werden die waehrend der Pause eingetroffenen
        Messwerte bewusst uebersprungen. Sonst wuerden sie beim Fortsetzen
        auf einen Schlag nachgetragen, und die Pause waere im Verlauf gar
        nicht zu sehen.

        Args:
            pausiert: True haelt die Aufzeichnung an.
        """
        war_pausiert = self._messung_pausiert
        self._messung_pausiert = pausiert

        aktion = getattr(self, "pause_action", None)
        if aktion is not None and aktion.isChecked() != pausiert:
            aktion.setChecked(pausiert)

        if pausiert:
            self.pause_button.setText("Aufzeichnung fortsetzen")
            self.verlauf_status_label.setText("Aufzeichnung angehalten")
            logger.info("Aufzeichnung von Hand angehalten.")
        else:
            self.pause_button.setText("Aufzeichnung anhalten")
            self.verlauf_status_label.setText("")

            # Nur beim echten Fortsetzen ueberspringen, was waehrend der
            # Pause kam. Dieselbe Methode wird beim Aufbau der Oberflaeche
            # einmal aufgerufen, um den Knopf zu beschriften -- dabei darf
            # der Zeitstempel nicht angefasst werden, sonst fielen bereits
            # gepufferte Messwerte unter den Tisch.
            if war_pausiert:
                self._letzter_zeitstempel = time.time()
                logger.info("Aufzeichnung fortgesetzt.")

        # Beim Aufbau steht die Anzeige noch nicht.
        if hasattr(self, "messzeit_label"):
            self._messzeit_anzeigen()

    @safe_execute(logger)
    def _on_pause_action(self, pausiert: bool) -> None:
        """Haelt Menueeintrag und Knopf der Aufzeichnungspause synchron.

        Args:
            pausiert: True haelt die Aufzeichnung an.
        """
        if self.pause_button.isChecked() != pausiert:
            self.pause_button.setChecked(pausiert)

    @safe_execute(logger)
    def _on_zum_jetzt(self, _geklickt: bool = False) -> None:
        """Springt im Verlauf zurueck ans aktuelle Ende.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        if not self._verlauf_zeit:
            return

        self._folgt_live = True
        self._ansicht_nachfuehren()
        logger.debug("Ansicht auf das aktuelle Ende gesetzt.")

    def _build_band_monitors(self) -> QtWidgets.QWidget:
        """Baut die Kachelreihe unter dem Graphen, eine Kachel je Kanal."""
        self.monitor_leiste = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(self.monitor_leiste)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.band_monitore: dict[str, BandMonitor] = {}
        for name, farbe in _CURVE_COLORS.items():
            monitor = BandMonitor(name, farbe)
            monitor.sichtbarkeit_geaendert.connect(self._on_kurve_umgeschaltet)
            self.band_monitore[name] = monitor
            layout.addWidget(monitor)

        # Keine feste Hoehe: Auf niedrigen Bildschirmen darf die Reihe
        # schrumpfen, statt dem Graphen den Platz wegzunehmen.
        self.monitor_leiste.setMinimumHeight(88)
        self.monitor_leiste.setMaximumHeight(130)
        return self.monitor_leiste

    @safe_execute(logger)
    def _on_kurve_umgeschaltet(self, name: str, sichtbar: bool) -> None:
        """Blendet eine einzelne Kurve im Verlaufsgraphen ein oder aus.

        Args:
            name: Kanalname.
            sichtbar: True blendet ein.
        """
        kurve = self._curves.get(name)
        if kurve is not None:
            kurve.setVisible(sichtbar)

    @safe_execute(logger)
    def _alle_kurven_setzen(self, sichtbar: bool) -> None:
        """Blendet alle Kurven gemeinsam ein oder aus.

        Args:
            sichtbar: True blendet alle ein.
        """
        for monitor in self.band_monitore.values():
            monitor.check.setChecked(sichtbar)

    @safe_execute(logger)
    def _on_monitore_umgeschaltet(self, sichtbar: bool) -> None:
        """Zeigt oder verbirgt die gesamte Kachelreihe.

        Args:
            sichtbar: True zeigt sie an.
        """
        self.monitor_leiste.setVisible(sichtbar)

    @safe_execute(logger)
    def _on_skala_umgeschaltet(self, global_skala: bool) -> None:
        """Merkt sich den Skalierungsmodus der Kacheln.

        Args:
            global_skala: True fuer eine gemeinsame Skala aller Kanaele.
        """
        self._globale_skala = global_skala

    @safe_execute(logger)
    def _on_testaufnahme(self, _geklickt: bool = False) -> None:
        """Startet eine reine Testaufnahme.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        dauer, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Einfache Aufnahme",
            "Diese Aufnahme dient nur zum Ansehen und Auswerten.\n"
            "Sie landet in einer eigenen Datei und fließt nie ins Modell ein.\n\n"
            "Dauer in Sekunden:",
            60,
            5,
            600,
        )
        if not ok:
            return

        # Wie beim Training: Waehrend der Aufnahme soll das Auto stehen.
        self._lock_vor_training = self.drive_lock_button.isChecked()
        self.drive_lock_button.setChecked(True)
        self.drive_lock_button.setEnabled(False)

        self.testaufnahme_button.setEnabled(False)
        self.testaufnahme_action.setEnabled(False)
        self.train_button.setEnabled(False)
        self.training_progress_label.setText("Testaufnahme läuft ...")

        self._testaufnahme_worker = TestaufnahmeWorker(self.training_session, float(dauer))
        self._testaufnahme_worker.progress.connect(self._on_testaufnahme_fortschritt)
        self._testaufnahme_worker.fertig.connect(self._on_testaufnahme_fertig)
        self._testaufnahme_worker.failed.connect(self._on_testaufnahme_fehler)
        self._testaufnahme_worker.start()

    def _on_testaufnahme_fortschritt(self, vergangen: float, anzahl: int) -> None:
        """Zeigt den Fortschritt der Testaufnahme.

        Args:
            vergangen: Vergangene Sekunden.
            anzahl: Bisher aufgezeichnete Samples.
        """
        self.training_progress_label.setText(
            f"Testaufnahme: {vergangen:.0f} s, {anzahl} Samples"
        )

    @safe_execute(logger)
    def _on_testaufnahme_fertig(self, pfad: str) -> None:
        """Meldet, wo die Testaufnahme gelandet ist.

        Args:
            pfad: Pfad der geschriebenen Datei.
        """
        self._testaufnahme_aufraeumen()
        self.training_progress_label.setText("Testaufnahme gespeichert.")

        datei = Path(pfad)
        QtWidgets.QMessageBox.information(
            self,
            "Testaufnahme gespeichert",
            f"Gespeichert als:\n{datei.name}\n\n"
            f"Ordner:\n{datei.parent}\n\n"
            "Die Datei enthält Zeitstempel, Signalqualität und alle "
            "Kanäle. Sie wird nicht trainiert.",
        )

    @safe_execute(logger)
    def _on_testaufnahme_fehler(self, meldung: str) -> None:
        """Meldet einen Fehler bei der Testaufnahme.

        Args:
            meldung: Fehlertext.
        """
        self._testaufnahme_aufraeumen()
        self.training_progress_label.setText("Testaufnahme fehlgeschlagen.")
        QtWidgets.QMessageBox.warning(self, "Testaufnahme", meldung)

    def _testaufnahme_aufraeumen(self) -> None:
        """Gibt die Bedienelemente nach der Testaufnahme wieder frei."""
        self.testaufnahme_button.setEnabled(True)
        self.testaufnahme_action.setEnabled(True)
        self.train_button.setEnabled(True)
        self.train_action.setEnabled(True)
        self.drive_lock_button.setEnabled(True)
        self.drive_lock_button.setChecked(getattr(self, "_lock_vor_training", True))

    @safe_execute(logger)
    def _on_konfusion_ansehen(self, _geklickt: bool = False) -> None:
        """Oeffnet die Ansicht der Verwechslungen.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        KonfusionsmatrixDialog(self.classifier, self).exec_()

    @safe_execute(logger)
    def _on_trainingsdaten_ansehen(self, _geklickt: bool = False) -> None:
        """Oeffnet die Ansicht der gesammelten Trainingsdaten.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        dialog = TrainingsdatenDialog(self._aktive_trainingsdatei(), self)
        dialog.exec_()

    @safe_execute(logger)
    def _on_rohdaten_ansehen(self, _geklickt: bool = False) -> None:
        """Oeffnet das Fenster mit den Rohdaten vom Headset.

        Nicht modal: Es bleibt neben dem Hauptfenster offen, und dort laeuft
        die Messung weiter. Ist es schon offen, wird es nur nach vorn geholt.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        if self._rohdaten_dialog is None:
            self._rohdaten_dialog = RohdatenDialog(self.processor.receiver, self)
        self._rohdaten_dialog.show()
        self._rohdaten_dialog.raise_()
        self._rohdaten_dialog.activateWindow()

    @safe_execute(logger)
    def _on_testaufnahmen_ansehen(self, _geklickt: bool = False) -> None:
        """Oeffnet die Ansicht der einfachen Aufnahmen.

        Eigene Ansicht statt eines Reiters in den Trainingsdaten: Diese
        Aufnahmen gehoeren nicht zum Modell, und was man ueber sie wissen
        will -- Zeitpunkt, Dauer, Signalqualitaet -- steht dort nicht.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        dialog = TestaufnahmenDialog(self._aktive_trainingsdatei().parent, self)
        dialog.exec_()

    @safe_execute(logger)
    def _on_auto_neu_verbinden(self, _geklickt: bool = False) -> None:
        """Startet den Neuaufbau der Funkverbindung zum RC-Auto.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        if getattr(self, "_reconnect_worker", None) is not None:
            if self._reconnect_worker.isRunning():
                return

        self.car_reconnect_button.setEnabled(False)
        self.car_reconnect_button.setText("Verbinde …")

        self._reconnect_worker = ReconnectWorker(self.car_controller)
        self._reconnect_worker.fertig.connect(self._on_auto_verbunden)
        self._reconnect_worker.start()
        logger.info("Neuverbindung zum RC-Auto gestartet.")

    @safe_execute(logger)
    def _on_auto_verbunden(self, erfolg: bool) -> None:
        """Nimmt das Ergebnis des Neuverbindens entgegen.

        Args:
            erfolg: True, wenn die Verbindung steht.
        """
        self.car_reconnect_button.setEnabled(True)
        self.car_reconnect_button.setText("Neu verbinden")
        self._set_car_status()

        if erfolg:
            logger.info("RC-Auto neu verbunden.")
        else:
            logger.warning("Neuverbindung zum RC-Auto fehlgeschlagen.")
            QtWidgets.QMessageBox.warning(
                self,
                "Keine Verbindung",
                "Das RC-Auto war nicht erreichbar.\n\n"
                "Prüfen: Ist der Arduino eingeschaltet? Hält ein anderes "
                "Programm die Verbindung? Das Funkmodul lässt immer nur "
                "eine gleichzeitig zu.",
            )

    def _retrain_bedienbar(self, bereit: bool) -> None:
        """Setzt Knopf und Menueeintrag fuers Neutrainieren gemeinsam.

        Args:
            bereit: False waehrend das Training laeuft.
        """
        text = "Modell neu trainieren" if bereit else "Trainiere Modelle …"
        for bedienelement in (
            getattr(self, "retrain_button", None),
            getattr(self, "retrain_action", None),
        ):
            if bedienelement is not None:
                bedienelement.setEnabled(bereit)
                bedienelement.setText(text)

    @safe_execute(logger)
    def _on_manuell_action(self, aktiv: bool) -> None:
        """Haelt Menueeintrag und Knopf des Handbetriebs synchron.

        Args:
            aktiv: True schaltet den Handbetrieb ein.
        """
        if self.manuell_button.isChecked() != aktiv:
            self.manuell_button.setChecked(aktiv)

    @safe_execute(logger)
    def _on_manuell_toggled(self, aktiv: bool) -> None:
        """Schaltet den Handbetrieb ein oder aus.

        Beim Einschalten uebernimmt die Tastatur. Die Fahrsperre wird dafuer
        geloest -- wer den Handbetrieb einschaltet, will fahren -- und der
        Knopf solange gesperrt, damit sich beide nicht widersprechen. Der
        Klassifikator schickt in dieser Zeit nichts mehr raus, seine
        Vorhersage bleibt aber sichtbar.

        Beim Ausschalten geht ein STOPP raus und der vorherige Zustand der
        Fahrsperre wird wiederhergestellt.

        Args:
            aktiv: True schaltet den Handbetrieb ein.
        """
        self._manuell_aktiv = aktiv

        aktion = getattr(self, "manuell_action", None)
        if aktion is not None and aktion.isChecked() != aktiv:
            aktion.setChecked(aktiv)

        self._gedrueckte_tasten.clear()

        if aktiv:
            # Kurz gehalten: Welche Tasten steuern, steht direkt darunter.
            self.manuell_button.setText("Handbetrieb aktiv")
            self.manuell_button.setStyleSheet(
                f"background-color: {_MANUELL_COLOR}; color: white; font-weight: bold;"
            )

            self._lock_vor_manuell = self.drive_lock_button.isChecked()
            self.drive_lock_button.setChecked(False)
            self.drive_lock_button.setEnabled(False)

            self.direction_indicator.set_manuell(True)
            self.direction_indicator.set_direction("STOP")

            # Ohne Fokus im Fenster kaemen die Tastendruecke nicht an.
            self.setFocus()
            logger.info("Handbetrieb eingeschaltet.")
        else:
            self.manuell_button.setText("Manuell fahren")
            self.manuell_button.setStyleSheet("")

            self.direction_indicator.set_manuell(False)

            # Erst anhalten, dann die Sperre zuruecksetzen.
            self._manuell_senden("STOP")
            # Den Zusatz "(Hand)" wieder loswerden -- sonst steht er dort,
            # bis die naechste Vorhersage die Zeile ueberschreibt.
            self.command_label.setText("Fahrbefehl: --")

            self.drive_lock_button.setEnabled(True)
            self.drive_lock_button.setChecked(getattr(self, "_lock_vor_manuell", True))
            logger.info("Handbetrieb ausgeschaltet.")

    @safe_execute(logger)
    def _manuell_senden(self, label: str) -> None:
        """Schickt einen von Hand ausgeloesten Fahrbefehl an das Auto.

        Args:
            label: Eines der Labels aus ``CONFIG.labels``.
        """
        self.direction_indicator.set_direction(label)
        self.command_label.setText(f"Fahrbefehl: {label} (Hand)")
        self.car_controller.send_label(label, force=True)

    def eventFilter(self, objekt, event) -> bool:  # noqa: N802 - Qt-Konvention
        """Faengt Tastendruecke fuer den Handbetrieb ab.

        Der Filter haengt an der gesamten Anwendung statt am Fenster, weil
        die Tasten sonst je nach Fokus im Knopf oder in der Klappliste
        haengen blieben -- Pfeiltasten verschieben dort den Fokus, statt zu
        fahren.

        Args:
            objekt: Das Objekt, an das das Ereignis ging.
            event: Das Ereignis.

        Returns:
            True, wenn das Ereignis verbraucht wurde.
        """
        if not self._manuell_aktiv:
            return super().eventFilter(objekt, event)

        # Waehrend eines Dialogs -- etwa der Eingabe eines Profilnamens --
        # darf Tippen nicht das Auto losfahren lassen.
        if QtWidgets.QApplication.activeModalWidget() is not None:
            return super().eventFilter(objekt, event)

        typ = event.type()

        # Sicherheitsnetz: Verliert das Fenster den Fokus, waehrend eine
        # Taste gedrueckt ist, kommt das Loslassen nie an -- und der
        # Heartbeat wuerde das Auto endlos weiterfahren lassen. Beim
        # Fokusverlust wird deshalb angehalten.
        if typ == QtCore.QEvent.WindowDeactivate and self._gedrueckte_tasten:
            self._gedrueckte_tasten.clear()
            self._manuell_senden("STOP")
            logger.info("Fenster verlor den Fokus -- Handbetrieb angehalten.")
            return super().eventFilter(objekt, event)

        if typ not in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
            return super().eventFilter(objekt, event)

        # Beim Halten einer Taste schickt Qt laufend weitere Druck- und
        # Loslass-Ereignisse. Die werden ignoriert, sonst wechselte der
        # Zustand staendig zwischen Fahren und Stopp.
        if event.isAutoRepeat():
            return True

        taste = event.key()
        if taste not in _MANUELL_TASTEN:
            return super().eventFilter(objekt, event)

        if typ == QtCore.QEvent.KeyPress:
            self._gedrueckte_tasten.add(taste)
            self._manuell_senden(_MANUELL_TASTEN[taste])
        else:
            self._gedrueckte_tasten.discard(taste)
            if self._gedrueckte_tasten:
                # Eine zweite Taste ist noch gedrueckt: deren Richtung gilt.
                letzte = next(iter(self._gedrueckte_tasten))
                self._manuell_senden(_MANUELL_TASTEN[letzte])
            else:
                self._manuell_senden("STOP")

        return True

    @safe_execute(logger)
    def _on_drive_lock_action(self, gesperrt: bool) -> None:
        """Haelt Menueeintrag und Knopf der Fahrsperre synchron.

        Args:
            gesperrt: True bedeutet gesperrt.
        """
        if self.drive_lock_button.isChecked() != gesperrt:
            self.drive_lock_button.setChecked(gesperrt)

    def _build_status_bar(self) -> QtWidgets.QHBoxLayout:
        """Baut die obere Statuszeile (Verbindung, Signalqualitaet, Pakete)."""
        layout = QtWidgets.QHBoxLayout()

        # Headset-Seite der Datenkette. Die Anzeige sagt nicht nur "geht"
        # oder "geht nicht", sondern an welchem Glied es haengt: Funkmodul
        # nicht gefunden, Funk steht aber keine Pakete, oder Pakete ohne
        # Hautkontakt. Vorher war die Lampe in allen drei Faellen rot, und
        # man konnte nur raten, was zu tun ist.
        self.connection_light = QtWidgets.QLabel()
        self.connection_light.setFixedSize(20, 20)
        self._set_connection_color(_SIGNAL_NONE_COLOR)
        layout.addWidget(QtWidgets.QLabel("Headset:"))
        layout.addWidget(self.connection_light)

        # Kurze Texte, die Erklaerung steht im Tooltip. Zusaetzlich darf die
        # Zeile schrumpfen: Ein langer Zustandstext zog sonst das ganze
        # Fenster breiter, statt sich einzupassen.
        self.signal_quality_label = QtWidgets.QLabel("--")
        self.signal_quality_label.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )
        self.signal_quality_label.setMinimumWidth(140)
        layout.addWidget(self.signal_quality_label, 1)

        self.packet_count_label = QtWidgets.QLabel("Pakete: 0")
        layout.addWidget(self.packet_count_label)

        # Direkt bei der Headset-Anzeige: Wer dort "keine Daten" oder "kein
        # Hautkontakt" liest, will als Naechstes sehen, was tatsaechlich
        # ankommt.
        self.rohdaten_button = QtWidgets.QPushButton("Rohdaten …")
        self.rohdaten_button.setToolTip(
            "Zeigt live jede Zeile, die vom Headset ankommt —\n"
            "auch die, die das Programm verwirft."
        )
        self.rohdaten_button.clicked.connect(self._on_rohdaten_ansehen)
        layout.addWidget(self.rohdaten_button)

        layout.addSpacing(12)

        # Eigene Anzeige fuer das Auto: Die Verbindung zum Headset sagt
        # nichts darueber, ob die Fahrbefehle auch ankommen.
        self.car_light = QtWidgets.QLabel()
        self.car_light.setFixedSize(20, 20)
        layout.addWidget(QtWidgets.QLabel("RC-Auto:"))
        layout.addWidget(self.car_light)

        self.car_status_label = QtWidgets.QLabel("--")
        layout.addWidget(self.car_status_label)

        # Das Headset verbindet sich nach einem Abriss von selbst neu, das
        # Auto nicht -- dort muss der Anstoss von Hand kommen.
        self.car_reconnect_button = QtWidgets.QPushButton("Neu verbinden")
        self.car_reconnect_button.setToolTip(
            "Baut die Funkverbindung zum RC-Auto neu auf."
        )
        self.car_reconnect_button.clicked.connect(self._on_auto_neu_verbinden)
        layout.addWidget(self.car_reconnect_button)

        # Kein eigener Dehnraum hier: Den freien Platz bekommt die
        # Headset-Zeile (Dehnfaktor 1 oben), damit lange Zustandstexte ganz
        # zu lesen sind, statt dass eine Luecke den Platz frisst.

        # Laufzeit: seit dem Programmstart. Gehoert weder zum Headset noch
        # zum Auto und steht deshalb fuer sich rechts. Die Messungszeit
        # steht getrennt davon unter dem Graphen.
        self.zeit_label = QtWidgets.QLabel("Laufzeit: 0:00 min")
        self.zeit_label.setToolTip("Zeit seit dem Start des Programms.")
        layout.addWidget(self.zeit_label)
        layout.addSpacing(12)

        # Umschalter fuer helle/dunkle Darstellung. Steht auch im Menue
        # "Ansicht", hier aber als Knopf, weil man ihn je nach Raumlicht
        # oefter braucht und nicht erst suchen will.
        self.theme_button = QtWidgets.QPushButton()
        self.theme_button.clicked.connect(self._on_theme_button)
        layout.addWidget(self.theme_button)

        return layout

    @safe_execute(logger)
    def _on_theme_button(self, _geklickt: bool = False) -> None:
        """Schaltet per Knopf zwischen heller und dunkler Darstellung um.

        Args:
            _geklickt: Zustandswert, den Qt beim Klick mitschickt. Wird
                nicht gebraucht, muss aber angenommen werden.
        """
        # Ueber den Menueeintrag, damit dessen Haken automatisch mitwandert
        # und beide Wege durch dieselbe Stelle laufen.
        self.dark_mode_action.setChecked(not self._dunkelmodus)

    def _theme_button_beschriften(self) -> None:
        """Beschriftet den Umschaltknopf mit dem jeweils anderen Modus."""
        knopf = getattr(self, "theme_button", None)
        if knopf is None:
            return
        # Kurz, weil die Statuszeile sonst ueber 1150 Punkte Fensterbreite
        # hinauswuchs. Die volle Bezeichnung steht im Tooltip und im Menue
        # "Ansicht".
        if self._dunkelmodus:
            knopf.setText("☀ Hell")
            knopf.setToolTip("Zur hellen Darstellung wechseln")
        else:
            knopf.setText("☾ Dunkel")
            knopf.setToolTip("Zur dunklen Darstellung wechseln")

    def _on_dark_mode_toggled(self, aktiv: bool) -> None:
        """Schaltet zwischen heller und dunkler Darstellung um.

        Args:
            aktiv: True fuer die dunkle Darstellung.
        """
        self._dunkelmodus = aktiv
        self._apply_theme()

    @safe_execute(logger)
    def _apply_theme(self) -> None:
        """Faerbt Fenster und Graph gemaess dem aktuellen Modus ein."""
        theme = _THEMES["dunkel" if self._dunkelmodus else "hell"]

        # Graph: Hintergrund, Achsen und Beschriftungen
        vordergrund = pg.mkColor(theme["vordergrund"])
        for graph in (self.plot_widget, self.esense_widget):
            graph.setBackground(theme["hintergrund"])
            for kante in ("left", "bottom"):
                achse = graph.getAxis(kante)
                achse.setPen(pg.mkPen(vordergrund))
                achse.setTextPen(pg.mkPen(vordergrund))

        self.esense_widget.setLabel("bottom", _X_ACHSE_NAME, units="s",
                                    color=theme["vordergrund"])
        self.esense_widget.setLabel("left", _ESENSE_ACHSE_NAME,
                                    units=_ESENSE_ACHSE_EINHEIT,
                                    color=theme["vordergrund"])
        self._y_achse_beschriften()
        self._pausen_einfaerben()
        self._seitenleiste_stil(theme)

        # Restliches Fenster ueber die Qt-Palette
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(theme["fenster"]))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(theme["text"]))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(theme["feld"]))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(theme["fenster"]))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(theme["text"]))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(theme["fenster"]))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(theme["text"]))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(theme["feld"]))
        palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(theme["text"]))

        # Ausgewaehlte Tabellenzeilen: weisse Schrift auf dem Akzent, 4,7 : 1.
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(theme["akzent"]))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))

        # Rahmen und Gitterlinien der Tabellen. Ohne diese Rollen blieben sie
        # auf den hellen Vorgaben von Qt und stuenden im dunklen Modus als
        # grelle Linien im Bild.
        for rolle in (QtGui.QPalette.Mid, QtGui.QPalette.Midlight, QtGui.QPalette.Dark):
            palette.setColor(rolle, QtGui.QColor(theme["rand"]))

        # setColor ohne Gruppe setzt auch die Farben fuer "gesperrt". Damit
        # saehen gesperrte Knoepfe aus wie bedienbare -- deshalb ausdruecklich
        # gedaempft.
        for rolle in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
            palette.setColor(QtGui.QPalette.Disabled, rolle, QtGui.QColor(theme["gedaempft"]))

        self.setPalette(palette)

        # Auch fuer die ganze Anwendung: Dialoge sind eigene Fenster und
        # erben die Palette des Hauptfensters nicht. Ohne diese Zeile
        # oeffneten sich Trainingsdaten, einfache Aufnahmen und
        # Rueckfragen hell, auch wenn das Hauptfenster dunkel war.
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setPalette(palette)

        for widget in self.findChildren(QtWidgets.QWidget):
            if widget is not self.connection_light:
                widget.setPalette(palette)

        # Die Kacheln zeichnen ihre Beschriftung selbst und brauchen die
        # Farben deshalb ausdruecklich.
        for monitor in getattr(self, "band_monitore", {}).values():
            monitor.setze_theme(theme)

        self._theme_button_beschriften()

    def _build_plot_widget(self) -> pg.PlotWidget:
        """Baut den Live-Graphen ueber den gesamten Messverlauf."""
        pg.setConfigOptions(antialias=_KANTENGLAETTUNG)

        # Eigene Y-Achse: Die Bandleistungen gehen in die Millionen, und
        # pyqtgraph schriebe dort sonst einen Faktor wie "1e+06" an den
        # Rand und beschriftete die Striche mit kleinen Zahlen. Die
        # ZahlenAchse schreibt die Werte stattdessen aus.
        self.plot_widget = pg.PlotWidget(
            title=_PLOT_TITEL, axisItems={"left": ZahlenAchse("left")}
        )
        # Keine Legende im Graphen: Die Zuordnung Farbe zu Kanal steht in
        # den Kacheln darunter, und der Kasten verdeckte einen Teil der
        # Kurven.
        self.plot_widget.setLabel("bottom", _X_ACHSE_NAME, units="s")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)

        # Damit die Anzeige unabhaengig von der Aufnahmedauer fluessig
        # bleibt: pyqtgraph zeichnet nur so viele Punkte, wie das Fenster
        # ueberhaupt aufloesen kann, und nur den sichtbaren Ausschnitt.
        # Beim Hineinzoomen kommen die Einzelwerte wieder zum Vorschein --
        # es gehen also keine Daten verloren, sie werden nur nicht alle
        # gleichzeitig gemalt.
        self.plot_widget.setDownsampling(auto=True, mode="peak")
        self.plot_widget.setClipToView(True)

        # Der Ausschnitt ist fest: feste Breite, feste Hoehe im Sinne von
        # "passt sich automatisch an die sichtbaren Werte an". Verschieben
        # geht nur waagerecht durch Ziehen, Zoomen gar nicht -- sonst
        # verliert man beim Arbeiten staendig die Ansicht.
        vb = self.plot_widget.getViewBox()
        vb.setMouseEnabled(x=True, y=False)      # nur waagerecht ziehen
        vb.setMouseMode(pg.ViewBox.PanMode)      # Ziehen verschiebt, statt aufzuziehen
        vb.setMenuEnabled(False)                 # Rechtsklick-Menue mit Zoomoptionen weg
        vb.wheelEvent = lambda ev, axis=None: ev.ignore()   # kein Zoom per Mausrad
        vb.enableAutoRange(axis=pg.ViewBox.YAxis)

        # Grenzen des Ausschnitts. Weder negative Zeiten noch negative
        # Bandleistungen gibt es -- ohne diese Schranken konnte man aber
        # nach links ins Nichts ziehen, und die automatische Y-Skalierung
        # legte unterhalb der Null noch Luft dazu. Beides zeigt Flaeche,
        # in der nie etwas stehen kann.
        #
        # Die obere Zeitgrenze zieht _update_plot bei jedem neuen Messwert
        # nach, damit man auch nicht in die Zukunft scrollen kann.
        vb.setLimits(xMin=0, yMin=0)

        # Merkt sich, ob die Anzeige dem Jetzt folgt. Sobald von Hand
        # gescrollt wird, bleibt der Ausschnitt stehen; zieht man wieder
        # ganz nach rechts, laeuft er weiter mit.
        self._folgt_live = True
        vb.sigRangeChangedManually.connect(self._on_bereich_verschoben)

        self._curves = {}
        for name, color in _CURVE_COLORS.items():
            if name in _ESENSE_KANAELE:
                continue   # gehoert in den unteren Graphen
            # Runde Enden und Ecken lassen die Linien weicher wirken, ohne
            # die Messwerte zu veraendern -- rein optisch.
            pen = pg.mkPen(color=color, width=_LINIEN_BREITE)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            # connect="finite" laesst die Linie an NaN-Werten abreissen --
            # so werden Messpausen als Luecke sichtbar statt ueberbrueckt.
            self._curves[name] = self.plot_widget.plot(
                [],
                [],
                pen=pen,
                name=_CURVE_LABELS[name],
                antialias=_KANTENGLAETTUNG,
                connect="finite",
            )

        return self.plot_widget

    def _build_esense_widget(self) -> pg.PlotWidget:
        """Baut den unteren Graphen fuer Attention und Meditation.

        Warum ein eigener Graph und keine zweite Achse im selben Bild:
        siehe ``_ESENSE_KANAELE`` in gui_konstanten.py.

        Die Zeitachse ist mit dem oberen Graphen gekoppelt. Verschieben,
        Mitlaufen und das Zurueckspringen ans aktuelle Ende wirken deshalb
        auf beide zugleich -- wer einen Ausschlag im Alpha-Band ansieht,
        sieht denselben Zeitpunkt darunter in der Aufmerksamkeit.
        """
        self.esense_widget = pg.PlotWidget(axisItems={"left": ZahlenAchse("left")})

        # Die Zeitachse steht nur unter dem unteren Graphen -- zweimal
        # dieselbe Beschriftung waere doppelt und kostet Hoehe.
        self.esense_widget.setLabel("bottom", _X_ACHSE_NAME, units="s")
        self.esense_widget.setLabel(
            "left", _ESENSE_ACHSE_NAME, units=_ESENSE_ACHSE_EINHEIT
        )
        self.esense_widget.showGrid(x=True, y=True, alpha=0.2)
        self.esense_widget.setDownsampling(auto=True, mode="peak")
        self.esense_widget.setClipToView(True)

        vb = self.esense_widget.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.setMouseMode(pg.ViewBox.PanMode)
        vb.setMenuEnabled(False)
        vb.wheelEvent = lambda ev, axis=None: ev.ignore()

        # Feste Skala von 0 bis 100, siehe _ESENSE_MIN/_ESENSE_MAX.
        vb.setYRange(_ESENSE_MIN, _ESENSE_MAX, padding=0)
        vb.setLimits(xMin=0, yMin=_ESENSE_MIN, yMax=_ESENSE_MAX)
        vb.disableAutoRange(axis=pg.ViewBox.YAxis)

        # Gemeinsame Zeitachse. Der obere Graph fuehrt, dieser folgt.
        self.esense_widget.setXLink(self.plot_widget)
        vb.sigRangeChangedManually.connect(self._on_bereich_verschoben)

        for name in _ESENSE_KANAELE:
            pen = pg.mkPen(color=_CURVE_COLORS[name], width=_LINIEN_BREITE)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            self._curves[name] = self.esense_widget.plot(
                [],
                [],
                pen=pen,
                name=_CURVE_LABELS[name],
                antialias=_KANTENGLAETTUNG,
                connect="finite",
            )

        return self.esense_widget

    def _build_graph_bereich(self) -> QtWidgets.QSplitter:
        """Stellt beide Graphen uebereinander, mit verschiebbarer Trennlinie.

        Wie viel Platz die eSense-Werte verdienen, haengt davon ab, woran
        man gerade arbeitet -- deshalb ein Teiler statt eines festen
        Verhaeltnisses. Die Aufteilung ueberlebt den Programmstart.
        """
        self.graph_teiler = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.graph_teiler.addWidget(self._build_plot_widget())
        self.graph_teiler.addWidget(self._build_esense_widget())
        self.graph_teiler.setStretchFactor(0, 3)
        self.graph_teiler.setStretchFactor(1, 1)
        self.graph_teiler.setChildrenCollapsible(False)
        self.graph_teiler.setSizes([420, 170])

        # Der Griff der Trennlinie wurde als grauer Punkt mitten zwischen
        # den Graphen gezeichnet und sah aus wie ein Darstellungsfehler.
        # Die Trennlinie bleibt verschiebbar -- der Mauszeiger zeigt es beim
        # Darueberfahren --, nur der Griff wird nicht mehr gemalt.
        self.graph_teiler.setHandleWidth(5)
        self.graph_teiler.setStyleSheet("QSplitter::handle { background: transparent; }")

        # Die Zeitachse steht unter dem eSense-Graphen. Oben waere sie
        # dieselbe Achse ein zweites Mal.
        self.plot_widget.getPlotItem().hideAxis("bottom")

        # Erst jetzt anwenden: Die Log-Skalierung betrifft die Bandkurven,
        # und die stehen erst nach dem Aufbau beider Graphen vollzaehlig.
        self._y_skala_anwenden()

        return self.graph_teiler

    def _y_skala_anwenden(self) -> None:
        """Stellt Achse und Kurven auf die gewaehlte Y-Skalierung ein.

        pyqtgraph logarithmiert die Werte selbst, sobald der Log-Modus
        gesetzt ist -- die gespeicherten Messwerte bleiben also unberuehrt,
        und ein Umschalten geht ohne Datenverlust in beide Richtungen.
        """
        self.plot_widget.setLogMode(y=self._y_log)
        for name, kurve in self._curves.items():
            # Attention und Meditation stehen im unteren Graphen auf ihrer
            # festen Skala von 0 bis 100 -- dort gibt es nichts zu spreizen.
            if name not in _ESENSE_KANAELE:
                kurve.setLogMode(False, self._y_log)

        self._y_achse_beschriften()

    def _y_achse_beschriften(self) -> None:
        """Beschriftet Titel und Y-Achse des oberen Graphen.

        Beides steht hier zusammen, weil ``setLabel`` und ``setTitle`` Text
        und Farbe in einem Aufruf setzen: Wer nur das eine schreibt,
        verliert das andere. Genau das passierte, als Skalenumschaltung und
        Themawechsel jede fuer sich beschrifteten.
        """
        theme = _THEMES["dunkel" if self._dunkelmodus else "hell"]
        self.plot_widget.setLabel(
            "left",
            _Y_ACHSE_NAME,
            units=_Y_ACHSE_EINHEIT,
            color=theme["vordergrund"],
        )
        zusatz = _PLOT_TITEL_LOG if self._y_log else _PLOT_TITEL_LINEAR
        self.plot_widget.setTitle(_PLOT_TITEL + zusatz, color=theme["vordergrund"])

    def _karte(self, eltern: QtWidgets.QVBoxLayout, titel: str) -> QtWidgets.QVBoxLayout:
        """Legt in der Seitenleiste eine Karte mit Ueberschrift an.

        Eine Karte ist eine leicht abgehobene Flaeche mit abgerundeten
        Ecken. Sie ersetzt die frueheren Rahmen der QGroupBox: Die zeichnen
        auf macOS eine harte Linie mit dem Titel darin, und bei fuenf
        Bereichen untereinander bestand die Leiste fast nur noch aus Linien.
        Aussehen siehe ``_seitenleiste_stil``.

        Args:
            eltern: Layout der Seitenleiste.
            titel: Ueberschrift der Karte.

        Returns:
            Das innere Layout, in das der Inhalt kommt.
        """
        karte = QtWidgets.QFrame()
        karte.setObjectName("karte")
        innen = QtWidgets.QVBoxLayout(karte)
        innen.setContentsMargins(10, 10, 10, 12)
        innen.setSpacing(6)

        kopf = QtWidgets.QLabel(titel.upper())
        kopf.setObjectName("kartenkopf")
        innen.addWidget(kopf)

        eltern.addWidget(karte)
        return innen

    @staticmethod
    def _nebeninfo(text: str = "") -> QtWidgets.QLabel:
        """Kleine, gedaempfte Beschriftung fuer Erlaeuterungen und Zusatzangaben."""
        label = QtWidgets.QLabel(text)
        label.setObjectName("nebeninfo")
        label.setWordWrap(True)
        return label

    def _build_side_panel(self) -> QtWidgets.QScrollArea:
        """Baut die rechte Seitenleiste.

        Reihenfolge von oben nach unten entlang der Frage, die man gerade
        hat:

        1. **Profil** -- wessen Daten und welches Modell gerade gelten.
           Steht oben, weil alles darunter davon abhaengt.
        2. **Fahren** -- was das Programm gerade erkennt und ob das Auto
           darauf reagiert. Waehrend einer Sitzung der meistgebrauchte
           Bereich, deshalb gleich darunter und ohne Scrollen sichtbar.
        3. **Aufnehmen** -- neue Daten sammeln und die gesammelten ansehen.
        4. **Modell** -- wie gut das Modell ist, neu trainieren, was es
           verwechselt.
        5. **Aktuelle Werte** -- die Zahlen zum Nachlesen. Dieselben Werte
           zeigen die Kacheln unter dem Graphen, deshalb ganz unten.

        Alle Knoepfe der frueheren Leiste sind geblieben, sie stehen nur
        dort, wo sie inhaltlich hingehoeren: "Modell neu trainieren" etwa
        stand vorher zwischen den Aufnahmeknoepfen, "Verwechslungen ansehen"
        ausserhalb jedes Bereichs.

        Die Leiste steckt in einem scrollbaren Bereich. Sonst waechst sie
        mit jeder Ergaenzung ueber die Bildschirmhoehe hinaus, und die
        unteren Knoepfe sind nicht mehr erreichbar.
        """
        self.seitenleiste = QtWidgets.QWidget()
        self.seitenleiste.setObjectName("seitenleiste")
        layout = QtWidgets.QVBoxLayout(self.seitenleiste)
        layout.setContentsMargins(2, 0, 4, 8)
        layout.setSpacing(10)

        # --- 1. Profil ------------------------------------------------
        # EEG-Muster sind von Person zu Person verschieden. Jede Person
        # bekommt deshalb ihr eigenes Profil mit eigenen Trainingsdaten
        # und eigenem Modell -- sonst mischen sich fremde Aufnahmen.
        profil = self._karte(layout, "Profil")

        profil_reihe = QtWidgets.QHBoxLayout()
        profil_reihe.setSpacing(6)
        self.profil_box = QtWidgets.QComboBox()
        # Ohne das richtet sich die Auswahl nach dem laengsten Profilnamen
        # ("augen-zu-und-augen-auf") und drueckt die Leiste ueber den Rand.
        # So darf sie schmaler werden und kuerzt zu lange Namen ab.
        self.profil_box.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.profil_box.setMinimumContentsLength(5)
        self.profil_box.addItems(vorhandene_profile())
        self.profil_box.setCurrentText(CONFIG.paths.profil)
        self.profil_box.currentTextChanged.connect(self._on_profil_gewechselt)
        profil_reihe.addWidget(self.profil_box, 1)

        # Der Knopf steht zusaetzlich im Menue "Profil". Hier bleibt er
        # trotzdem: Ohne ihn ist der Weg zum Anlegen nicht zu erraten.
        self.neues_profil_button = QtWidgets.QPushButton("Neues Profil …")
        self.neues_profil_button.setToolTip("Neues Profil anlegen …")
        self.neues_profil_button.clicked.connect(self._on_neues_profil)
        profil_reihe.addWidget(self.neues_profil_button)
        profil.addLayout(profil_reihe)

        self.profil_info_label = self._nebeninfo("--")
        profil.addWidget(self.profil_info_label)

        # --- 2. Fahren ------------------------------------------------
        fahren = self._karte(layout, "Fahren")

        # Richtungsanzeige und Vorhersage nebeneinander statt untereinander:
        # spart gut 100 Punkte Hoehe, und beides gehoert zusammen gelesen.
        vorhersage_reihe = QtWidgets.QHBoxLayout()
        vorhersage_reihe.setSpacing(12)

        self.direction_indicator = DirectionIndicator()
        self.direction_indicator.setMinimumSize(0, 0)
        self.direction_indicator.setFixedSize(104, 104)
        vorhersage_reihe.addWidget(self.direction_indicator, 0, QtCore.Qt.AlignVCenter)

        vorhersage_spalte = QtWidgets.QVBoxLayout()
        vorhersage_spalte.setSpacing(2)
        vorhersage_spalte.addStretch(1)
        vorhersage_spalte.addWidget(self._nebeninfo("Vorhersage"))

        self.prediction_label = QtWidgets.QLabel("--")
        schrift = self.prediction_label.font()
        schrift.setPointSize(17)
        schrift.setBold(True)
        self.prediction_label.setFont(schrift)
        self.prediction_label.setWordWrap(True)
        vorhersage_spalte.addWidget(self.prediction_label)

        self.confidence_label = QtWidgets.QLabel("Konfidenz: --")
        vorhersage_spalte.addWidget(self.confidence_label)
        self.command_label = QtWidgets.QLabel("Fahrbefehl: --")
        self.command_label.setWordWrap(True)
        vorhersage_spalte.addWidget(self.command_label)
        vorhersage_spalte.addStretch(1)

        vorhersage_reihe.addLayout(vorhersage_spalte, 1)
        fahren.addLayout(vorhersage_reihe)

        # Fahrsperre: Vorhersagen laufen weiter und werden angezeigt, es
        # geht nur kein Fahrbefehl mehr raus. Beim Aufnehmen und Auswerten
        # soll das Auto stehen bleiben, statt durchs Zimmer zu fahren.
        self.drive_lock_button = QtWidgets.QPushButton()
        self.drive_lock_button.setCheckable(True)
        self.drive_lock_button.toggled.connect(self._on_drive_lock_toggled)
        # Erst verbinden, dann setzen: So beschriftet und faerbt der
        # Handler den Knopf gleich beim Aufbau. Beim Start ist gesperrt,
        # damit nichts losfaehrt, bevor man es will.
        self.drive_lock_button.setChecked(True)
        fahren.addWidget(self.drive_lock_button)

        # Handbetrieb: Statt des Klassifikators steuert die Tastatur.
        # Nuetzlich zum Pruefen der Mechanik und als Rueckfallebene bei
        # einer Vorfuehrung, wenn das Signal gerade nicht mitspielt.
        self.manuell_button = QtWidgets.QPushButton()
        self.manuell_button.setCheckable(True)
        self.manuell_button.toggled.connect(self._on_manuell_toggled)
        fahren.addWidget(self.manuell_button)

        self.manuell_hinweis = self._nebeninfo(
            "W A S D oder Pfeiltasten — fährt nur, solange gedrückt"
        )
        self.manuell_hinweis.setAlignment(QtCore.Qt.AlignHCenter)
        fahren.addWidget(self.manuell_hinweis)

        # Beschriftung und Farbe gleich beim Aufbau setzen.
        self._on_manuell_toggled(False)

        # --- 3. Aufnehmen ---------------------------------------------
        aufnehmen = self._karte(layout, "Aufnehmen")

        # Die eine Hauptaktion der Leiste, deshalb farbig hervorgehoben.
        self.train_button = QtWidgets.QPushButton("Training starten")
        self.train_button.setObjectName("primaer")
        self.train_button.setToolTip(
            "Nimmt für einen Fahrbefehl auf und trainiert danach das Modell."
        )
        self.train_button.clicked.connect(self._on_start_training)
        aufnehmen.addWidget(self.train_button)

        # Fortschritt der laufenden Aufnahme. Belegt nur Platz, solange
        # eine laeuft.
        self.training_progress_label = Hinweiszeile()
        self.training_progress_label.setObjectName("nebeninfo")
        aufnehmen.addWidget(self.training_progress_label)

        # Gefuehrte Aufnahme aller Befehle in allen Reihenfolgen. Steht
        # direkt unter "Training starten", weil sie dessen Aufgabe in einem
        # Durchgang fuer alle Befehle erledigt.
        self.kalibrierung_button = QtWidgets.QPushButton("Kalibrierung …")
        self.kalibrierung_button.setToolTip(
            "Nimmt jeden Befehl in jeder Reihenfolge auf (je 15 s) und sagt\n"
            "die Wechsel an — auch mit geschlossenen Augen zu befolgen."
        )
        self.kalibrierung_button.clicked.connect(self._on_kalibrierung)
        aufnehmen.addWidget(self.kalibrierung_button)

        # Reine Testaufnahme: zum Nachschauen, nicht zum Trainieren.
        self.testaufnahme_button = QtWidgets.QPushButton("Einfache Aufnahme …")
        self.testaufnahme_button.setToolTip(
            "Nur zum Ansehen. Fließt nicht ins Modell ein."
        )
        self.testaufnahme_button.clicked.connect(self._on_testaufnahme)
        aufnehmen.addWidget(self.testaufnahme_button)

        trenner = QtWidgets.QFrame()
        trenner.setObjectName("trenner")
        trenner.setFixedHeight(1)
        aufnehmen.addSpacing(2)
        aufnehmen.addWidget(trenner)
        aufnehmen.addSpacing(2)

        self.testdaten_button = QtWidgets.QPushButton("Einfache Aufnahmen ansehen …")
        self.testdaten_button.setToolTip(
            "Zeigt die Aufnahmen, die nur zum Ansehen gemacht wurden."
        )
        self.testdaten_button.clicked.connect(self._on_testaufnahmen_ansehen)
        aufnehmen.addWidget(self.testdaten_button)

        self.daten_button = QtWidgets.QPushButton("Trainingsdaten ansehen …")
        self.daten_button.setToolTip(
            "Zeigt, wie viel je Fahrbefehl aufgezeichnet wurde und was fehlt."
        )
        self.daten_button.clicked.connect(self._on_trainingsdaten_ansehen)
        aufnehmen.addWidget(self.daten_button)

        # --- 4. Modell ------------------------------------------------
        modell = self._karte(layout, "Modell")

        # Wird von _on_retrain_finished mit einer Zeile je Verfahren
        # gefuellt, dazu Basisrate und Bewertungsverfahren.
        self.accuracy_layout = QtWidgets.QVBoxLayout()
        self.accuracy_layout.setSpacing(3)
        modell.addLayout(self.accuracy_layout)

        self.retrain_button = QtWidgets.QPushButton("Modell neu trainieren")
        self.retrain_button.clicked.connect(self._on_retrain)
        modell.addWidget(self.retrain_button)

        # Die Genauigkeit allein sagt nur, wie oft das Modell richtig
        # liegt. Was es verwechselt, steht in der Matrix.
        self.konfusion_button = QtWidgets.QPushButton("Verwechslungen ansehen …")
        self.konfusion_button.clicked.connect(self._on_konfusion_ansehen)
        modell.addWidget(self.konfusion_button)

        # --- 5. Aktuelle Werte ----------------------------------------
        werte = self._karte(layout, "Aktuelle Werte")

        # Tabelle mit Farbpunkt, Name, Frequenzbereich und Wert. Der Punkt
        # traegt die Kurvenfarbe, damit die Zeile ohne Nachdenken der Kurve
        # im Graphen zuzuordnen ist.
        raster = QtWidgets.QGridLayout()
        raster.setHorizontalSpacing(8)
        raster.setVerticalSpacing(3)
        raster.setColumnStretch(2, 1)

        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        for reihe, name in enumerate(_CURVE_COLORS):
            punkt = QtWidgets.QLabel()
            punkt.setFixedSize(9, 9)
            # Feiner heller Rand: Beta (#2c3e50) und Gamma sind so dunkel,
            # dass der Punkt auf der dunklen Karte sonst verschwindet.
            punkt.setStyleSheet(
                f"background-color: {_CURVE_COLORS[name]}; border-radius: 4px;"
                " border: 1px solid rgba(160, 160, 160, 150);"
            )
            raster.addWidget(punkt, reihe, 0, QtCore.Qt.AlignVCenter)

            # "Delta (0,5–2,75 Hz)" -> Name und Bereich getrennt setzen.
            bezeichnung, _, bereich = _CURVE_LABELS[name].partition(" (")
            raster.addWidget(QtWidgets.QLabel(bezeichnung), reihe, 1)
            bereich_label = self._nebeninfo(bereich.rstrip(")"))
            bereich_label.setWordWrap(False)
            raster.addWidget(bereich_label, reihe, 2)

            wert = QtWidgets.QLabel("--")
            wert.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            wert.setMinimumWidth(72)
            self.value_labels[name] = wert
            raster.addWidget(wert, reihe, 3)

        werte.addLayout(raster)

        layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.seitenleiste)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # Waagerecht soll nichts scrollen -- die Leiste passt sich in der
        # Breite an, gescrollt wird nur senkrecht, wenn es eng wird.
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # Breit genug fuer den laengsten Knopf samt senkrechter Bildlaufleiste.
        # Darunter wuerden Knopftexte rechts abgeschnitten, weil waagerecht
        # nicht gescrollt wird.
        scroll.setMinimumWidth(320)
        return scroll

    def _seitenleiste_stil(self, theme: dict) -> None:
        """Faerbt die Karten der Seitenleiste passend zur Darstellung.

        Ein Stylesheet fuer die ganze Leiste statt einzelner Farben je
        Widget: So gelten Abstaende, Ecken und Knopfhoehen ueberall gleich.
        Fahrsperre und Handbetrieb setzen ihre Signalfarbe weiterhin selbst
        -- ein Stil direkt am Widget hat Vorrang vor diesem hier, Ecken und
        Innenabstand erben sie trotzdem.

        Args:
            theme: Farbsatz aus ``_THEMES``.
        """
        akzent_hover = pg.mkColor(theme["akzent"]).lighter(112).name()
        self.seitenleiste.setStyleSheet(
            f"""
            QWidget#seitenleiste {{ background: {theme["fenster"]}; }}
            QFrame#karte {{
                background: {theme["karte"]};
                border: 1px solid {theme["rand"]};
                border-radius: 10px;
            }}
            QFrame#karte QLabel {{ background: transparent; border: none; }}
            QLabel#kartenkopf {{
                color: {theme["gedaempft"]};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QLabel#nebeninfo {{ color: {theme["gedaempft"]}; font-size: 12px; }}
            QFrame#trenner {{ background: {theme["rand"]}; border: none; }}
            QFrame#karte QPushButton {{
                background: {theme["knopf"]};
                color: {theme["text"]};
                border: 1px solid {theme["rand"]};
                border-radius: 6px;
                padding: 6px 8px;
            }}
            QFrame#karte QPushButton:hover {{ background: {theme["knopf_hover"]}; }}
            QFrame#karte QPushButton:pressed {{ background: {theme["rand"]}; }}
            QFrame#karte QPushButton:disabled {{ color: {theme["gedaempft"]}; }}
            QFrame#karte QPushButton#primaer {{
                background: {theme["akzent"]};
                border-color: {theme["akzent"]};
                color: #ffffff;
                font-weight: 600;
            }}
            QFrame#karte QPushButton#primaer:hover {{ background: {akzent_hover}; }}
            QFrame#karte QPushButton#primaer:disabled {{
                background: {theme["knopf"]};
                border-color: {theme["rand"]};
                color: {theme["gedaempft"]};
            }}
            """
        )

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
        self._laufzeit_anzeigen()
        self._update_status()
        self._update_plot()

        # Balken der Kacheln ein Stueck weiterbewegen. Laeuft bei jedem
        # Tick, unabhaengig davon, ob neue Messwerte da sind -- sonst
        # springen sie im Sekundentakt, statt weich nachzuziehen.
        for monitor in self.band_monitore.values():
            monitor.tick()

        now = time.time()
        if now - self._last_prediction_time >= CONFIG.prediction_interval_seconds:
            self._last_prediction_time = now
            self._update_prediction()

    def _update_status(self) -> None:
        """Aktualisiert Verbindungslicht, Signalqualitaet, Paketzaehler und Werte."""
        sample = self.processor.latest_sample
        pakete = self.processor.receiver.packet_count
        self.packet_count_label.setText(f"Pakete: {pakete}")
        self._set_car_status()

        # Verbindungsueberwachung: Kommt nichts mehr, duerfen die alten
        # Werte nicht stehen bleiben. Sie saehen aus wie eine laufende
        # Messung, sind aber nur der Stand vor dem Abriss.
        jetzt = time.time()
        if pakete != self._letzter_paketstand:
            self._letzter_paketstand = pakete
            self._letzte_paketzeit = jetzt

        # Die Datenkette Glied fuer Glied pruefen, vom Funk bis zum
        # Hautkontakt. Der erste Bruch bestimmt, was angezeigt wird.
        funk = getattr(self.processor.receiver, "connected", True)
        still = jetzt - self._letzte_paketzeit > _VERBINDUNG_TIMEOUT

        if not funk:
            self._kette_melden("sucht", sample, pakete)
            self._daten_stehen_still()
            return

        if sample is None:
            # Gerade verbunden, das erste Paket steht noch aus. Erst nach
            # der Wartezeit ist "keine Daten" eine Stoerung.
            self._kette_melden("still" if still else "wartet", sample, pakete)
            if still:
                self._daten_stehen_still()
            return

        if still:
            self._kette_melden("still", sample, pakete)
            self._daten_stehen_still()
            return

        if not sample.is_valid:
            # 0 Prozent bedeutet kein Hautkontakt. Dann wird die Messung
            # ausgesetzt: keine Werte, keine Graphen, keine Vorhersage.
            self._kette_melden("kontakt", sample, pakete)
            return

        self._kette_melden("messung", sample, pakete)

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
            self.value_labels[name].setText(zahl_deutsch(value, 0))

        # Kanal-Kacheln versorgen. Bei gemeinsamer Skala bezieht sich jeder
        # Balken auf den groessten Wert aller Baender -- Attention und
        # Meditation bleiben davon ausgenommen, weil sie mit ihrer festen
        # Skala von 0 bis 100 neben Bandleistungen in Millionenhoehe sonst
        # unsichtbar waeren.
        baender = ("delta", "theta", "alpha", "beta", "gamma")
        global_max = max(values[n] for n in baender) if self._globale_skala else None

        for name, value in values.items():
            bezug = global_max if (global_max and name in baender) else None
            self.band_monitore[name].setze_wert(value, bezug)

    def _kette_melden(self, zustand: str, sample, pakete: int) -> None:
        """Zeigt an, an welchem Glied der Datenkette es gerade haengt.

        Die Kette: Uno mit Funkmodul -> Funkstrecke -> Headset sendet ->
        Hautkontakt. Jeder Bruch hat eine andere Ursache und verlangt einen
        anderen Handgriff. Die Anzeige nennt deshalb das Glied und im
        Tooltip, was zu tun ist.

        Jeder Wechsel landet im Protokoll, samt Paketzaehler und rohem
        Signalwert. Am 2026-09-25 liess sich hinterher nicht mehr klaeren,
        ob damals Pakete ohne Hautkontakt kamen oder gar keine -- ungueltige
        Pakete wurden nirgends vermerkt. Das ist damit behoben.

        Args:
            zustand: ``sucht``, ``wartet``, ``still``, ``kontakt`` oder
                ``messung``.
            sample: Das zuletzt empfangene Sample oder ``None``.
            pakete: Stand des Paketzaehlers.
        """
        ble = type(self.processor.receiver).__name__ == "BleEegReceiver"

        if zustand == "sucht":
            farbe = _SIGNAL_NONE_COLOR
            text = "Funkmodul wird gesucht …" if ble else "USB nicht geöffnet"
            tipp = (
                "Das Funkmodul am Uno ist nicht zu finden.\n"
                "• Ist der Uno eingeschaltet (9-V-Block)?\n"
                "• Hält ein anderes Programm die Verbindung\n"
                "  (ble_bridge.py, ble_remote.py, ein zweites main.py)?\n"
                "Das Programm sucht von selbst weiter."
                if ble
                else "Der serielle Anschluss zum Uno ist nicht offen. Steckt das USB-Kabel?"
            )
        elif zustand == "wartet":
            farbe = _SIGNAL_BAD_COLOR
            text = "warte auf Daten …"
            tipp = "Die Verbindung steht, das erste Paket ist unterwegs."
        elif zustand == "still":
            farbe = _SIGNAL_NONE_COLOR
            text = "keine Daten — Headset an?"
            tipp = (
                "Die Verbindung zum Uno steht, aber von ihm kommt nichts.\n"
                "• Ist das Headset eingeschaltet?\n"
                "• Steckt die Leitung vom Headset zum Uno (Pin 0)?"
            )
        elif zustand == "kontakt":
            farbe = _SIGNAL_BAD_COLOR
            text = "kein Kontakt — Sitz prüfen"
            tipp = (
                "Das Headset sendet, meldet aber keinen Kontakt zur Haut\n"
                "(Rohwert 200). Die Messung ist so lange ausgesetzt.\n"
                "• Sitzt der Stirnsensor auf der Haut, der Ohrclip am Ohrläppchen?\n"
                "• Bleibt es dabei: Headset aus- und wieder einschalten."
            )
        else:
            prozent = sample.signal_percent
            farbe = _SIGNAL_GOOD_COLOR if prozent >= 100.0 else _SIGNAL_BAD_COLOR
            text = f"Signal {prozent:.0f} %"
            tipp = "Signalqualität des Headsets. 100 % ist bestmöglicher Kontakt."

        self._set_connection_color(farbe)
        if text != self.signal_quality_label.text():
            self.signal_quality_label.setText(text)
            self.signal_quality_label.setToolTip(tipp)
            self.connection_light.setToolTip(tipp)

        vorher = getattr(self, "_kette_zustand", None)
        if zustand != vorher:
            self._kette_zustand = zustand
            logger.info(
                "Datenkette Headset: %s -> %s (Pakete bisher %d, Rohwert Signal %s).",
                vorher or "Start",
                zustand,
                pakete,
                sample.signal_quality if sample is not None else "-",
            )

    def _daten_stehen_still(self) -> None:
        """Setzt alle Messanzeigen auf leer, wenn keine Pakete mehr kommen.

        Haelt dabei auch das Auto an. Das ist die zweite Sicherung neben
        der Aktualitaetspruefung in ``BrainProcessor.has_valid_signal``:
        Diese Methode laeuft im Takt der Oberflaeche, die Vorhersage nur
        einmal je Sekunde -- ohne sie fuehre das Auto nach einem Abriss bis
        zu eine Sekunde laenger weiter als noetig.
        """
        # Im Handbetrieb faehrt die Tastatur, nicht das EEG. Fehlende
        # Messpakete duerfen dann nicht dazwischenfunken.
        if not self._manuell_aktiv and self.car_controller.last_command not in (
            None,
            "STOP",
        ):
            self.car_controller.send_label("STOP", force=True)
            logger.warning(
                "Keine Messpakete mehr -- STOPP an das RC-Auto gesendet."
            )

        for label in self.value_labels.values():
            label.setText("--")
        for monitor in self.band_monitore.values():
            monitor.kein_wert()

    @safe_execute(logger)
    def _pause_oeffnen(self, x: float) -> None:
        """Zeichnet einen Pausenbalken ans Ende des Verlaufs.

        Der Balken erscheint sofort, wenn die Pause beginnt -- nicht erst,
        wenn danach wieder ein Wert kommt. Sonst saehe man waehrend einer
        Pause schlicht einen Verlauf, der aufhoert, ohne zu erfahren warum.

        Aufbau je Pause:

        * je Graph ein **Balken**: eine senkrechte Linie mit breitem Stift.
          Die Breite steht in Bildpunkten (``_PAUSE_BALKEN_PX``), nicht in
          Sekunden -- so bleibt der Balken schmal, egal wie lang die Pause
          war. Er liegt hinter den Kurven.
        * im oberen Graphen die **Aufschrift**, senkrecht, auf einer
          zweiten, unsichtbaren Linie *vor* den Kurven. Die Aufschrift
          haengt am sichtbaren Ausschnitt und bleibt deshalb auch beim
          Verschieben mittig.

        Args:
            x: Position auf der Messungszeit-Achse, mittig zwischen dem
                letzten Wert vor der Pause und dem ersten danach.
        """
        theme = _THEMES["dunkel" if self._dunkelmodus else "hell"]
        marker: dict = {"x": x, "balken": [], "schrift": None, "graphen": []}

        for graph in (self.plot_widget, self.esense_widget):
            balken = pg.InfiniteLine(
                pos=x,
                angle=90,
                movable=False,
                pen=pg.mkPen(pg.mkColor(*theme["pause"]), width=_PAUSE_BALKEN_PX),
            )
            balken.setZValue(-10)
            # ignoreBounds: Der Balken ist kein Messwert und darf die
            # automatische Hoehe der Achse nicht beeinflussen.
            graph.addItem(balken, ignoreBounds=True)
            marker["balken"].append(balken)
            marker["graphen"].append((graph, balken))

        schrift = pg.InfiniteLine(
            pos=x,
            angle=90,
            movable=False,
            pen=pg.mkPen(None),
            label=_PAUSE_TEXT,
            labelOpts={
                "position": 0.5,
                # Schrift parallel zur Linie, also senkrecht.
                "rotateAxis": (1, 0),
                "anchors": [(0.5, 0.5), (0.5, 0.5)],
                "color": theme["pause_text"],
                # Gedeckter Grund unter der Schrift, damit sie ueber den
                # Kurven lesbar bleibt. Schmaler als der Balken.
                "fill": pg.mkBrush(pg.mkColor(theme["hintergrund"]).lighter(115)),
            },
        )
        schrift.setZValue(10)
        self.plot_widget.addItem(schrift, ignoreBounds=True)
        marker["schrift"] = schrift
        marker["graphen"].append((self.plot_widget, schrift))

        self._pausen_marker.append(marker)
        self._offene_pause = marker

        # Den Balken ins Bild holen, wenn die Anzeige gerade mitlaeuft.
        self._grenzen_nachziehen()
        self._ansicht_nachfuehren()
        self._messzeit_anzeigen()

        logger.info("Messung pausiert bei %.0f s Messungszeit.", x)

    def _pause_abschliessen(self, x: float, dauer: float) -> None:
        """Traegt die Dauer in den Pausenbalken ein, sobald wieder gemessen wird.

        Wurde die Pause nicht schon beim Beginnen eingezeichnet -- etwa weil
        die Oberflaeche in der Zeit beschaeftigt war --, holt das der Aufruf
        hier nach.

        Args:
            x: Position des Balkens auf der Messungszeit-Achse.
            dauer: Echte Dauer der Unterbrechung in Sekunden.
        """
        if self._offene_pause is None:
            self._pause_oeffnen(x)

        marker = self._offene_pause
        if marker is not None and marker["schrift"] is not None:
            # Die echte Dauer steht im Balken, weil sie auf der Achse nicht
            # mehr abzulesen ist -- die Pause belegt dort nur einen Schritt.
            marker["schrift"].label.setText(f"{_PAUSE_TEXT} · {self._dauer_kurz(dauer)}")
        self._offene_pause = None

        logger.info(
            "Messpause bei %.0f s Messungszeit abgeschlossen, Dauer %.0f s.", x, dauer
        )

    def _pausen_einfaerben(self) -> None:
        """Passt vorhandene Pausenbalken an die aktuelle Darstellung an."""
        theme = _THEMES["dunkel" if self._dunkelmodus else "hell"]
        stift = pg.mkPen(pg.mkColor(*theme["pause"]), width=_PAUSE_BALKEN_PX)
        for marker in self._pausen_marker:
            for balken in marker["balken"]:
                balken.setPen(stift)
            if marker["schrift"] is not None:
                marker["schrift"].label.setColor(theme["pause_text"])
                marker["schrift"].label.fill = pg.mkBrush(
                    pg.mkColor(theme["hintergrund"]).lighter(115)
                )
                marker["schrift"].label.update()

    @staticmethod
    def _dauer_kurz(sekunden: float) -> str:
        """Kurze Dauerangabe fuer den Pausenbalken: ``"28 s"`` oder ``"2:05 min"``."""
        sekunden = int(round(sekunden))
        if sekunden < 60:
            return f"{sekunden} s"
        minuten, rest = divmod(sekunden, 60)
        return f"{minuten}:{rest:02d} min"

    def _laufzeit_text(self, sekunden: float) -> str:
        """Formatiert eine Zeitspanne fuer die Statuszeile.

        Args:
            sekunden: Die Zeitspanne.

        Returns:
            ``"12:04 min"``, ab einer Stunde ``"1:02:04 h"``.
        """
        stunden, rest = divmod(int(sekunden), 3600)
        minuten, sek = divmod(rest, 60)
        if stunden:
            return f"{stunden}:{minuten:02d}:{sek:02d} h"
        return f"{minuten}:{sek:02d} min"

    def _laufzeit_anzeigen(self) -> None:
        """Schreibt die Zeit seit Programmstart in die Statuszeile.

        Laeuft bei jedem Tick, aber gesetzt wird nur bei einer Aenderung --
        sonst rechnete Qt die Beschriftung zwanzigmal je Sekunde neu.
        """
        text = f"Laufzeit: {self._laufzeit_text(time.time() - self._programmstart)}"
        if text != self.zeit_label.text():
            self.zeit_label.setText(text)

    def _messzeit_anzeigen(self) -> None:
        """Schreibt die Messungszeit unter den Graphen.

        Die Messungszeit ist die Zeitachse selbst: Sie rueckt nur vor,
        wenn ein Messwert eingezeichnet wird, und steht in Pausen still.
        """
        if not self._verlauf_zeit:
            text = "Messungszeit: --"
        else:
            text = f"Messungszeit: {self._laufzeit_text(self._verlauf_zeit[-1])}"
            if self._offene_pause is not None or self._messung_pausiert:
                text += " (pausiert)"
        if text != self.messzeit_label.text():
            self.messzeit_label.setText(text)

    def _pause_pruefen(self) -> None:
        """Erkennt den Beginn einer Pause, waehrend sie laeuft.

        Zwei Ausloeser:

        * Von Hand angehalten.
        * Seit ``_PAUSE_AB_SEKUNDEN`` kein gueltiger Messwert -- Headset
          aus, kein Hautkontakt oder Funk abgerissen.

        Das Ende der Pause stellt ``_update_plot`` fest, sobald der naechste
        Messwert eintrifft.
        """
        if self._offene_pause is not None or self._letzte_probe_echt is None:
            return
        if not self._verlauf_zeit:
            return

        still = time.time() - self._letzte_probe_echt
        if self._messung_pausiert or still > _PAUSE_AB_SEKUNDEN:
            self._pause_oeffnen(self._verlauf_zeit[-1] + _PAUSE_SCHRITT / 2)

    def _rechter_rand(self) -> float:
        """Rechtes Ende des Verlaufs auf der Messungszeit-Achse.

        Laeuft gerade eine Pause, reicht der Verlauf einen Schritt weiter:
        Dort steht der Pausenbalken, und der soll nicht am Rand kleben.
        """
        if not self._verlauf_zeit:
            return 0.0
        zusatz = _PAUSE_SCHRITT if self._offene_pause is not None else 0.0
        return self._verlauf_zeit[-1] + zusatz

    def _ansicht_nachfuehren(self) -> None:
        """Fuehrt den Ausschnitt dem rechten Rand nach, solange er mitlaeuft."""
        if not self._folgt_live or not self._verlauf_zeit:
            return
        rechts = self._rechter_rand()
        self.plot_widget.setXRange(max(0.0, rechts - _ZEITFENSTER), rechts, padding=0)

    def _update_plot(self) -> None:
        """Schreibt neue Messwerte fort und zeichnet den gesamten Verlauf.

        Der ``BrainProcessor`` haelt nur ein gleitendes Fenster von
        ``CONFIG.history_seconds`` vor. Damit die Kurve trotzdem alles seit
        Messbeginn zeigt, werden hier nur die seit dem letzten Aufruf
        hinzugekommenen Samples angehaengt -- erkennbar am Zeitstempel.

        Die Zeitachse ist die **Messungszeit**: Jeder Wert rueckt um den
        echten Abstand zu seinem Vorgaenger vor, ueber eine Pause hinweg aber
        nur um ``_PAUSE_SCHRITT``. Eine lange Pause frisst so keinen Platz
        mehr, sondern steht als schmaler Balken im Verlauf.

        Nebeneffekt: Es wird nur neu gezeichnet, wenn tatsaechlich Daten
        dazugekommen sind, statt bei jedem GUI-Tick.
        """
        # Der Beginn einer Pause wird auch dann erkannt, wenn gerade nichts
        # eingezeichnet wird -- deshalb vor dem Abbruch unten.
        self._pause_pruefen()

        if self._messung_pausiert:
            return

        history = self.processor.get_history()
        if not history:
            return

        neue = [
            sample
            for sample in history
            if self._letzter_zeitstempel is None
            or sample.timestamp > self._letzter_zeitstempel
        ]
        if not neue:
            return

        for sample in neue:
            if self._letzte_probe_echt is None:
                zeitpunkt = 0.0
            else:
                vorher = self._verlauf_zeit[-1]
                luecke = sample.timestamp - self._letzte_probe_echt

                if luecke > _PAUSE_AB_SEKUNDEN or self._offene_pause is not None:
                    # Pause: Die Linie reisst auf, und die Messungszeit
                    # rueckt nur um einen Schritt vor statt um die Dauer.
                    #
                    # Der Abriss selbst steht als NaN in der Mitte des
                    # Schritts. Die Zeitachse bekommt dort einen echten,
                    # aufsteigenden Wert: pyqtgraph setzt fuer setClipToView
                    # und das Ausduennen streng aufsteigende X-Werte voraus.
                    # Ein NaN auf der Zeitachse liess frueher die Kurven kurz
                    # verschwinden.
                    mitte = vorher + _PAUSE_SCHRITT / 2
                    self._pause_abschliessen(mitte, luecke)
                    self._verlauf_zeit.append(mitte)
                    for werte in self._verlauf_werte.values():
                        werte.append(float("nan"))
                    zeitpunkt = vorher + _PAUSE_SCHRITT
                else:
                    zeitpunkt = vorher + luecke

            self._letzte_probe_echt = sample.timestamp
            self._verlauf_zeit.append(zeitpunkt)
            self._verlauf_werte["attention"].append(sample.attention)
            self._verlauf_werte["meditation"].append(sample.meditation)
            self._verlauf_werte["delta"].append(sample.delta)
            self._verlauf_werte["theta"].append(sample.theta)
            self._verlauf_werte["alpha"].append(sample.alpha)
            self._verlauf_werte["beta"].append(sample.beta)
            self._verlauf_werte["gamma"].append(sample.gamma)

            self._groesster_wert = max(
                self._groesster_wert,
                sample.attention,
                sample.meditation,
                sample.delta,
                sample.theta,
                sample.alpha,
                sample.beta,
                sample.gamma,
            )

        self._letzter_zeitstempel = neue[-1].timestamp

        self._kurven_zeichnen()
        self._grenzen_nachziehen()
        self._messzeit_anzeigen()

        # Ausschnitt dem neuesten Wert nachfuehren, solange nicht von Hand
        # gescrollt wurde. Die Breite bleibt dabei immer gleich.
        self._ansicht_nachfuehren()

    def _kurven_zeichnen(self) -> None:
        """Schreibt den gespeicherten Verlauf in die sieben Kurven.

        Gespeichert werden immer die unveraenderten Messwerte. Fuer die
        logarithmische Achse werden Nullen auf ``_LOG_BODEN`` gehoben --
        der Logarithmus von null ist nicht definiert, und ohne diesen
        Boden verschwaende pyqtgraph solche Punkte als Luecke. Eine Null
        bedeutet aber "kein Signal" oder tatsaechlich null, nicht "keine
        Messung"; sie gehoert also an den unteren Rand, nicht ins Nichts.

        NaN-Werte bleiben NaN: ``np.maximum`` gibt sie unveraendert
        zurueck, und genau daran brechen die Linien an Messpausen auf.
        """
        if not self._verlauf_zeit:
            return

        zeiten = np.array(self._verlauf_zeit)
        for name, werte in self._verlauf_werte.items():
            reihe = np.array(werte, dtype=float)
            if self._y_log and name not in _ESENSE_KANAELE:
                reihe = np.maximum(reihe, _LOG_BODEN)
            self._curves[name].setData(zeiten, reihe)

    def _grenzen_nachziehen(self) -> None:
        """Begrenzt den verschiebbaren Ausschnitt auf den Bereich mit Daten.

        Rechts: Ueber den neuesten Messwert hinaus gibt es nichts zu sehen.
        Oben: etwas Luft ueber dem groessten je gemessenen Wert -- so bleibt
        beim Herauszoomen immer die ganze Kurve im Bild, statt dass man in
        leerer Flaeche landet und die Messung aus den Augen verliert.

        Die Untergrenzen von einer Sekunde und einer Dekade verhindern, dass
        die Grenze zu Beginn mit dem Ursprung zusammenfaellt und der
        Ausschnitt in sich zusammenklappt.

        Im Log-Modus rechnet der ViewBox in logarithmierten Koordinaten --
        die Obergrenze muss also ebenfalls logarithmiert werden.
        """
        if not self._verlauf_zeit:
            return

        rechts = max(self._rechter_rand(), 1.0)
        oben = max(self._groesster_wert * _Y_SPIELRAUM, 10.0)
        self.plot_widget.getViewBox().setLimits(
            xMax=rechts,
            yMax=math.log10(oben) if self._y_log else oben,
        )
        # Dieselbe Zeitgrenze unten. Die Achsen sind gekoppelt, die Grenzen
        # sind es nicht -- ohne diese Zeile liesse sich der untere Graph
        # ueber das Messende hinausziehen und beide liefen auseinander.
        self.esense_widget.getViewBox().setLimits(xMax=rechts)

    @safe_execute(logger)
    def _on_y_skala_umgeschaltet(self, logarithmisch: bool) -> None:
        """Schaltet die Y-Achse zwischen linear und logarithmisch um.

        Args:
            logarithmisch: True fuer die logarithmische Achse.
        """
        # Beim Aufbau der Menueleiste steht der Graph noch nicht. Das
        # Ankreuzen loest das Signal aber schon dort aus.
        if not hasattr(self, "_curves"):
            self._y_log = logarithmisch
            return

        self._y_log = logarithmisch
        self._y_skala_anwenden()

        # Der Ausschnitt wird neu bestimmt: Die alten Grenzen stehen in der
        # jeweils anderen Rechnung und passten nach dem Umschalten nicht mehr.
        self._kurven_zeichnen()
        self._grenzen_nachziehen()
        self.plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)

    def _update_prediction(self) -> None:
        """Berechnet (falls moeglich) eine neue Vorhersage und sendet den Fahrbefehl."""
        # Im Handbetrieb gehoert das Auto der Tastatur. Die Anzeige wird
        # dann nicht angefasst -- sonst ueberschriebe die Vorhersage im
        # Sekundentakt den gerade gedrueckten Fahrbefehl.
        if self._manuell_aktiv:
            return

        if not self.processor.has_valid_signal():
            self.prediction_label.setText("Messung pausiert")
            self.confidence_label.setText("Konfidenz: --")
            self.direction_indicator.set_direction(None)

            # Bei 0 Prozent Signal wird nicht nur die Auswertung ausgesetzt,
            # sondern das Auto auch angehalten. Ohne das wuerde es mit dem
            # zuletzt gesendeten Befehl weiterfahren, weil der Heartbeat ihn
            # regelmaessig wiederholt -- rutscht das Headset ab, faehrt das
            # Auto sonst unkontrolliert weiter.
            if self.car_controller.last_command not in (None, "STOP"):
                self.car_controller.send_label("STOP")
                logger.info("Signal verloren -- STOPP an das RC-Auto gesendet.")
            self.command_label.setText("Fahrbefehl: STOPP (kein Signal)")
            return

        if self.classifier.model is None:
            self.prediction_label.setText("kein Modell trainiert")
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

        self.prediction_label.setText(label)
        self.confidence_label.setText(f"Konfidenz: {confidence * 100:.0f} %")
        self.direction_indicator.set_direction(label, confidence)

        # Bei aktiver Fahrsperre wird weiter vorhergesagt und angezeigt --
        # man sieht also, was das Modell erkennt --, es geht nur kein
        # Fahrbefehl an das Auto.
        if self.drive_lock_button.isChecked():
            self.command_label.setText(f"Fahrbefehl: {label} (gesperrt)")
            return

        self.command_label.setText(f"Fahrbefehl: {label}")
        self.car_controller.send_label(label)

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    def _update_profil_info(self) -> None:
        """Zeigt an, wie viele Daten das aktive Profil enthaelt."""
        anzahl = self.training_session.zeilen_zaehlen()
        modell = "Modell vorhanden" if self.classifier.model is not None else "kein Modell"
        self.profil_info_label.setText(f"{anzahl} Samples aufgezeichnet, {modell}")

        self.undo_action.setEnabled(self.training_session.letzte_aufnahme is not None)

    def _aktive_trainingsdatei(self) -> Path:
        """Trainingsdatei des Profils, das gerade in der Seitenleiste gewaehlt ist.

        Nicht ``CONFIG.paths.training_csv``: ``CONFIG`` ist unveraenderlich
        (``frozen=True``) und zeigt deshalb fuer die ganze Laufzeit auf das
        Profil, mit dem das Programm gestartet wurde. Ein Profilwechsel
        stellt stattdessen die Aufnahme (``training_session``) und das Modell
        (``classifier``) einzeln um -- die Aufnahme ist damit die verlaessliche
        Quelle fuer "welches Profil ist gerade aktiv".

        Bis 2026-09-27 lasen die Fenster "Trainingsdaten ansehen" und
        "Einfache Aufnahmen ansehen" aus ``CONFIG``. Nach einem Profilwechsel
        zeigten sie deshalb weiter die Daten des Startprofils -- und
        Umbenennen oder Loeschen darin haette die Datei des Startprofils
        veraendert.
        """
        return self.training_session.csv_path

    @safe_execute(logger)
    def _on_profil_gewechselt(self, name: str) -> None:
        """Stellt Trainingsdaten und Modell auf ein anderes Profil um.

        Args:
            name: Name des gewaehlten Profils.
        """
        ordner = PROFIL_BASIS / saubere_profilbezeichnung(name)

        self.training_session.wechsle_profil(ordner / "training_data.csv")

        # Erst leeren, dann laden: Sonst bliebe bei einem Profil ohne
        # eigenes Modell das des vorherigen aktiv.
        self.classifier.model = None
        self.classifier.last_accuracies = {}
        # Beide Pfade umstellen. Nur den Modellpfad zu wechseln reicht
        # nicht -- dann liest train() weiter die Daten des alten Profils.
        self.classifier.model_path = ordner / "trained_model.pkl"
        self.classifier.csv_path = ordner / "training_data.csv"
        self.classifier.load_model()

        self._on_retrain_finished(self.classifier.last_accuracies)
        self._update_profil_info()

        # Haken im Menue auf das jetzt aktive Profil setzen.
        for aktion in getattr(self, "profil_liste_aktionen", []):
            aktion.setChecked(aktion.text() == self.profil_box.currentText())

        logger.info("Profil gewechselt: %s", name)

    @safe_execute(logger)
    def _on_neues_profil(self, _geklickt: bool = False) -> None:
        """Fragt nach einem Namen und legt ein neues, leeres Profil an.

        Args:
            _geklickt: Zustandswert, den Qt beim Klick mitschickt. Wird
                nicht gebraucht, muss aber angenommen werden -- der
                Fehlerabfang-Decorator reicht alle Argumente durch.
        """
        name, ok = QtWidgets.QInputDialog.getText(
            self,
            "Neues Profil",
            "Name der Person:\n\n"
            "Jede Person braucht eigene Trainingsdaten — EEG-Muster sind\n"
            "individuell, ein fremdes Modell funktioniert nicht.",
        )
        if not ok or not name.strip():
            return

        sauber = saubere_profilbezeichnung(name)
        (PROFIL_BASIS / sauber).mkdir(parents=True, exist_ok=True)

        # Liste neu aufbauen. Die Signale bleiben dabei durchgehend
        # blockiert: Nach dem Neuaufbau steht unter Umstaenden bereits der
        # neue Name oben, dann wuerde setCurrentText nichts aendern und
        # kein Wechselsignal ausloesen -- die Pfade blieben beim alten
        # Profil. Deshalb wird der Wechsel danach ausdruecklich angestossen.
        self.profil_box.blockSignals(True)
        self.profil_box.clear()
        self.profil_box.addItems(vorhandene_profile())
        self.profil_box.setCurrentText(sauber)
        self.profil_box.blockSignals(False)

        self._profil_menue_aktualisieren()

        logger.info("Neues Profil angelegt: %s", sauber)
        self._on_profil_gewechselt(sauber)

    @safe_execute(logger)
    def _on_letzte_aufnahme_loeschen(self, _geklickt: bool = False) -> None:
        """Macht die zuletzt aufgezeichnete Aufnahme rueckgaengig.

        Args:
            _geklickt: Zustandswert von Qt, siehe ``_on_neues_profil``.
        """
        letzte = self.training_session.letzte_aufnahme
        if letzte is None:
            QtWidgets.QMessageBox.information(
                self, "Nichts zu löschen", "Seit dem Start wurde nichts aufgezeichnet."
            )
            return

        label, anzahl = letzte
        antwort = QtWidgets.QMessageBox.question(
            self,
            "Letzte Aufnahme löschen",
            f"Die letzte Aufnahme entfernen?\n\n"
            f"Fahrbefehl: {label}\n"
            f"Samples: {anzahl}\n\n"
            f"Das lässt sich nicht rückgängig machen.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if antwort != QtWidgets.QMessageBox.Yes:
            return

        entfernt = self.training_session.letzte_aufnahme_loeschen()
        self.training_progress_label.setText(
            f"{entfernt} Samples für '{label}' gelöscht."
        )
        self._update_profil_info()

    def _on_bereich_verschoben(self) -> None:
        """Reagiert darauf, dass der Ausschnitt von Hand gezogen wurde.

        Solange man in der Vergangenheit steht, bleibt die Anzeige dort
        stehen. Zieht man wieder ganz nach rechts ans aktuelle Ende,
        laeuft sie automatisch weiter mit -- ohne dass man dafuer einen
        Knopf suchen muss.
        """
        if not self._verlauf_zeit:
            return

        (_, rechts), _ = self.plot_widget.getViewBox().viewRange()
        jetzt = self._verlauf_zeit[-1]

        folgt = rechts >= jetzt - _LIVE_TOLERANZ
        if folgt != self._folgt_live:
            self._folgt_live = folgt
            logger.debug(
                "Anzeige %s.", "laeuft wieder mit" if folgt else "steht still"
            )

        # Die Breite des Fensters bleibt fest, auch beim Scrollen.
        if not folgt:
            self.plot_widget.setXRange(rechts - _ZEITFENSTER, rechts, padding=0)

    def _on_drive_lock_toggled(self, gesperrt: bool) -> None:
        """Schaltet die Fahrsperre um und haelt das Auto beim Sperren an.

        Args:
            gesperrt: True, wenn keine Fahrbefehle mehr rausgehen sollen.
        """
        # Knopf und Menueeintrag zeigen denselben Zustand. Die Pruefung
        # verhindert, dass sich die beiden gegenseitig im Kreis aufrufen.
        aktion = getattr(self, "drive_lock_action", None)
        if aktion is not None and aktion.isChecked() != gesperrt:
            aktion.setChecked(gesperrt)

        if gesperrt:
            self.drive_lock_button.setText("Fahrsperre aktiv — Auto steht")
            self.drive_lock_button.setStyleSheet(
                f"background-color: {_LOCK_ON_COLOR}; color: white; font-weight: bold;"
            )
            # Beim Einschalten sofort anhalten, nicht erst beim naechsten Tick.
            if self.car_controller.connected:
                self.car_controller.send_label("STOP", force=True)
            logger.info("Fahrsperre eingeschaltet -- STOPP an das RC-Auto.")
        else:
            self.drive_lock_button.setText("Fahrsperre aus — Auto fährt")
            self.drive_lock_button.setStyleSheet(
                f"background-color: {_LOCK_OFF_COLOR}; color: white; font-weight: bold;"
            )
            logger.info("Fahrsperre ausgeschaltet -- Fahrbefehle gehen wieder raus.")

    def _set_car_status(self) -> None:
        """Aktualisiert Lampe und Text der RC-Auto-Verbindung.

        Wird zwanzigmal pro Sekunde aufgerufen. ``setStyleSheet`` stoesst
        jedes Mal eine Neuberechnung des Widget-Stils an, deshalb wird nur
        bei einer echten Aenderung angefasst.
        """
        verbunden = getattr(self.car_controller, "connected", False)
        farbe = _SIGNAL_GOOD_COLOR if verbunden else _SIGNAL_NONE_COLOR

        if farbe != getattr(self, "_car_light_farbe", None):
            self._car_light_farbe = farbe
            self.car_light.setStyleSheet(
                f"background-color: {farbe}; border-radius: 10px;"
            )

        if not verbunden:
            text = "nicht verbunden"
        elif self.drive_lock_button.isChecked():
            text = "verbunden (gesperrt)"
        else:
            text = "verbunden"

        if text != self.car_status_label.text():
            self.car_status_label.setText(text)

    def _set_connection_color(self, color_hex: str) -> None:
        """Setzt die Hintergrundfarbe der Verbindungsanzeige.

        Wird bei jedem Tick aufgerufen, deshalb nur bei echter Aenderung
        anfassen -- siehe ``_set_car_status``.

        Args:
            color_hex: Hex-Farbcode, z. B. ``"#2ecc71"``.
        """
        if color_hex == getattr(self, "_connection_farbe", None):
            return
        self._connection_farbe = color_hex
        self.connection_light.setStyleSheet(f"background-color: {color_hex}; border-radius: 10px;")

    # ------------------------------------------------------------------
    # Button-Handler
    # ------------------------------------------------------------------
    def _on_start_training(self) -> None:
        """Oeffnet Dialoge zur Label-/Dauer-Auswahl und startet die Aufnahme."""
        label, ok = QtWidgets.QInputDialog.getItem(
            self, "Training starten", "Fahrbefehl:", list(CONFIG.active_labels), 0, False
        )
        if not ok:
            return

        duration, ok = QtWidgets.QInputDialog.getInt(
            self, "Aufnahmedauer", "Sekunden:", int(CONFIG.default_training_duration), 1, 120
        )
        if not ok:
            return

        # Waehrend der Aufnahme soll das Auto auf keinen Fall losfahren.
        # Der vorherige Zustand wird gemerkt und danach wiederhergestellt.
        self._lock_vor_training = self.drive_lock_button.isChecked()
        self.drive_lock_button.setChecked(True)
        self.drive_lock_button.setEnabled(False)

        self.train_button.setEnabled(False)
        self.train_action.setEnabled(False)
        self.training_progress_label.setText(f"Trainiere: {label} ...")

        self._training_worker = TrainingWorker(self.training_session, label, float(duration))
        self._training_worker.progress.connect(self._on_training_progress)
        self._training_worker.finished_recording.connect(self._on_training_finished)
        self._training_worker.failed.connect(self._on_training_failed)
        self._training_worker.start()

    @safe_execute(logger)
    def _on_kalibrierung(self, _geklickt: bool = False) -> None:
        """Fuehrt die gefuehrte Kalibrierung aus.

        Waehrend sie laeuft, ist das Auto gesperrt und die uebrigen
        Aufnahmeknoepfe sind aus -- zwei Aufnahmen gleichzeitig schrieben
        durcheinander in dieselbe Datei.

        Args:
            _geklickt: Zustandswert von Qt, ungenutzt.
        """
        self._lock_vor_training = self.drive_lock_button.isChecked()
        self.drive_lock_button.setChecked(True)
        self.drive_lock_button.setEnabled(False)
        bedienelemente = (
            self.train_button, self.train_action, self.kalibrierung_button,
            self.kalibrierung_action, self.testaufnahme_button, self.testaufnahme_action,
        )
        for element in bedienelemente:
            element.setEnabled(False)

        try:
            dialog = KalibrierungDialog(self.training_session, CONFIG.active_labels, self)
            dialog.exec_()
        finally:
            for element in bedienelemente:
                element.setEnabled(True)
            self._restore_drive_lock()
            self._update_profil_info()

        if dialog.neu_trainieren:
            self._on_retrain()

    def _on_training_progress(self, elapsed: float, count: int) -> None:
        """Aktualisiert die Fortschrittsanzeige waehrend der Aufnahme."""
        self.training_progress_label.setText(f"{elapsed:.0f} s, {count} Samples ...")

    def _on_training_finished(self, count: int) -> None:
        """Wird aufgerufen, wenn die Aufnahme erfolgreich abgeschlossen ist."""
        self.training_progress_label.setText(f"Fertig: {count} Samples gespeichert.")
        self.train_button.setEnabled(True)
        self.train_action.setEnabled(True)
        self._restore_drive_lock()

    def _on_training_failed(self, message: str) -> None:
        """Wird aufgerufen, wenn waehrend der Aufnahme ein Fehler auftrat."""
        self.training_progress_label.setText(f"Fehler: {message}")
        self.train_button.setEnabled(True)
        self.train_action.setEnabled(True)
        self._restore_drive_lock()

    def _restore_drive_lock(self) -> None:
        """Stellt die Fahrsperre auf den Stand vor der Aufnahme zurueck."""
        self.drive_lock_button.setEnabled(True)
        self.drive_lock_button.setChecked(getattr(self, "_lock_vor_training", True))

    def _on_retrain(self) -> None:
        """Startet das Neu-Trainieren aller Modelle im Hintergrund."""
        self._retrain_bedienbar(False)

        self._retrain_worker = RetrainWorker(self.classifier)
        self._retrain_worker.finished_training.connect(self._on_retrain_finished)
        self._retrain_worker.failed.connect(self._on_retrain_failed)
        self._retrain_worker.start()

    def _on_retrain_finished(self, accuracies: dict) -> None:
        """Aktualisiert die Genauigkeitsanzeige mit dem Ergebnis des Trainings."""
        self._retrain_bedienbar(True)

        for name in list(self.accuracy_labels.keys()):
            widget = self.accuracy_labels.pop(name)
            self.accuracy_layout.removeWidget(widget)
            widget.deleteLater()

        if not accuracies:
            placeholder = self._nebeninfo("Noch kein Modell — nicht genug Trainingsdaten.")
            self.accuracy_layout.addWidget(placeholder)
            self.accuracy_labels["_placeholder"] = placeholder
            return

        # Eine Zeile je Verfahren, Name links, Prozent rechtsbuendig -- so
        # stehen die Zahlen untereinander und lassen sich vergleichen. Das
        # beste Verfahren steht oben und fett: Es faehrt das Auto.
        rangfolge = sorted(accuracies.items(), key=lambda kv: -kv[1])
        for platz, (model_name, accuracy) in enumerate(rangfolge):
            zeile = self._genauigkeitszeile(
                model_name, f"{accuracy * 100:.0f} %", fett=platz == 0
            )
            self.accuracy_labels[model_name] = zeile
            self.accuracy_layout.addWidget(zeile)

        # Ohne Vergleichswert laesst sich eine Genauigkeit nicht einordnen:
        # Ein Modell muss die Basisrate deutlich schlagen, sonst raet es nur
        # geschickt. Dazu das benutzte Verfahren, damit sichtbar ist, ob die
        # Zahl belastbar ist.
        basis = getattr(self.classifier, "last_baseline", 0.0)
        if basis:
            basis_widget = self._genauigkeitszeile(
                "Basisrate (raten)", f"{basis * 100:.0f} %", gedaempft=True
            )
            self.accuracy_labels["_basis"] = basis_widget
            self.accuracy_layout.addWidget(basis_widget)

        verfahren = getattr(self.classifier, "last_eval_method", "")
        if verfahren:
            verfahren_widget = self._nebeninfo(verfahren)
            self.accuracy_labels["_verfahren"] = verfahren_widget
            self.accuracy_layout.addWidget(verfahren_widget)

    def _genauigkeitszeile(
        self, name: str, wert: str, fett: bool = False, gedaempft: bool = False
    ) -> QtWidgets.QWidget:
        """Baut eine Zeile "Name ....... Wert" fuer den Modell-Bereich.

        Args:
            name: Bezeichnung links.
            wert: Zahl rechts, bereits formatiert.
            fett: Hebt die Zeile hervor (bestes Verfahren).
            gedaempft: Setzt die Zeile zurueckhaltend (Vergleichswerte).

        Returns:
            Das Zeilen-Widget.
        """
        zeile = QtWidgets.QWidget()
        reihe = QtWidgets.QHBoxLayout(zeile)
        reihe.setContentsMargins(0, 0, 0, 0)

        links = self._nebeninfo(name) if gedaempft else QtWidgets.QLabel(name)
        rechts = self._nebeninfo(wert) if gedaempft else QtWidgets.QLabel(wert)
        rechts.setWordWrap(False)
        rechts.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if fett:
            for label in (links, rechts):
                schrift = label.font()
                schrift.setBold(True)
                label.setFont(schrift)

        reihe.addWidget(links, 1)
        reihe.addWidget(rechts)
        return zeile

    def _on_retrain_failed(self, message: str) -> None:
        """Zeigt eine Fehlermeldung an, wenn das Neu-Trainieren fehlschlug."""
        self._retrain_bedienbar(True)
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
        self._zustand_speichern()

        try:
            self.car_controller.disconnect()
        except Exception:  # noqa: BLE001
            logger.exception("Fehler beim Trennen des RC-Autos")
        try:
            self.processor.receiver.stop()
        except Exception:  # noqa: BLE001
            logger.exception("Fehler beim Stoppen des SerialReceiver")
        event.accept()
