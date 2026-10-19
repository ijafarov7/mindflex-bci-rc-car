"""Eigene Dialogfenster der Oberflaeche."""

from __future__ import annotations

import csv
import math
import shutil
import subprocess
import time
from pathlib import Path

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from config import CONFIG, PROFIL_BASIS, TRAINING_CSV_COLUMNS
from serial_receiver import RohdatenMitschnitt
from gui_konstanten import (
    _CURVE_COLORS,
    _BEFEHL_FARBEN,
    _CURVE_LABELS,
    _BEFEHL_NAME_DE,
    _KALIBRIERUNG_ANWEISUNG,
    _KALIBRIERUNG_ENDTON,
    _KALIBRIERUNG_SEKUNDEN,
    _KALIBRIERUNG_STIMME,
    _KALIBRIERUNG_UEBERGANG,
    _LOG_BODEN,
    _TABELLENFARBEN,
    _Y_ACHSE_EINHEIT,
    _Y_ACHSE_NAME,
    _Y_ACHSE_ZUSATZ_LOG,
    _Y_LOG_STANDARD,
    _Y_SPIELRAUM,
)
from gui_widgets import PlanLeiste, ZahlenAchse, zahl_deutsch
from gui_worker import KalibrierungsWorker
from trainer import (
    kalibrierung_anweisungen_laden,
    kalibrierung_anweisungen_speichern,
    kalibrierungsplan,
)
from utils import setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

def _farbe(art: str) -> QtGui.QColor:
    """Schriftfarbe fuer einen Zustand in einer Tabelle.

    Drei Zustaende, je Darstellung mit eigenem, auf Kontrast geprueftem
    Farbton (siehe ``_TABELLENFARBEN``):

    * ``zu_wenig`` -- angefangen, aber zu duenn: hier ist Handlungsbedarf.
    * ``genug`` -- ausreichend Material, bzw. richtig erkannt.
    * ``neben`` -- bewusst leer, nicht aktiv, Testaufnahme: kein Mangel.

    Welcher Satz gilt, entscheidet der Tabellengrund der Anwendung. Das
    Hauptfenster setzt ihn beim Umschalten der Darstellung; ein Dialog, der
    danach aufgebaut wird, bekommt damit automatisch die passenden Farben.

    Args:
        art: ``zu_wenig``, ``genug`` oder ``neben``.

    Returns:
        Die Farbe.
    """
    app = QtWidgets.QApplication.instance()
    dunkel = (
        app is not None
        and app.palette().color(QtGui.QPalette.Base).lightness() < 128
    )
    return QtGui.QColor(_TABELLENFARBEN["dunkel" if dunkel else "hell"][art])

# Das Headset liefert ein Paket je Sekunde, Samples sind also Sekunden.
_SAMPLES_SOLL = 180        # drei Minuten je Klasse
_AUFNAHMEN_SOLL = 5        # fuer eine belastbare Bewertung
_AUFNAHMEN_MINDESTENS = 3


class Aufnahme:
    """Ein zusammenhaengender Block gleicher Labels in der Trainings-CSV.

    Genau so grenzt auch der Klassifikator die Aufnahmen ab: Ein
    Labelwechsel beendet die eine und beginnt die naechste. Diese
    Abgrenzung entscheidet spaeter darueber, was beim Bewerten
    zurueckgehalten wird -- deshalb ist sie hier sichtbar gemacht.

    Attributes:
        nummer: Fortlaufende Nummer in der Datei, bei 1 beginnend.
        label: Der Fahrbefehl dieser Aufnahme.
        von: Index der ersten Zeile in der CSV (ohne Kopfzeile).
        zeilen: Die Datenzeilen selbst.
    """

    def __init__(
        self,
        nummer: int,
        label: str,
        von: int,
        zeilen: list[list[str]],
        datei: Path | None = None,
        signal: list[float] | None = None,
        sekunden: float | None = None,
    ) -> None:
        self.nummer = nummer
        self.label = label
        self.von = von
        self.zeilen = zeilen
        # Nur bei Testaufnahmen gesetzt: Sie liegen in einer eigenen Datei
        # statt als Abschnitt in der Trainings-CSV.
        self.datei = datei
        # Ebenfalls nur bei Testaufnahmen: die mitgeschriebene
        # Signalqualitaet je Sample und die tatsaechlich verstrichene Zeit.
        self.signal = signal
        self._sekunden = sekunden

    @property
    def ist_test(self) -> bool:
        """True, wenn es sich um eine Testaufnahme handelt."""
        return self.datei is not None

    @property
    def art(self) -> str:
        """Kurzbezeichnung der Aufnahmeart fuer die Anzeige."""
        return "Test" if self.ist_test else "Training"

    @property
    def bis(self) -> int:
        """Index hinter der letzten Zeile dieser Aufnahme."""
        return self.von + len(self.zeilen)

    @property
    def dauer_sekunden(self) -> float:
        """Laenge der Aufnahme in Sekunden.

        Bei Testaufnahmen steht die echte verstrichene Zeit in der Datei.
        Sie ist laenger als die Anzahl Samples, wenn Pakete ausgefallen
        sind -- und genau das will man wissen. Die Trainings-CSV kennt
        keine Zeit, dort zaehlt ein Sample als eine Sekunde.
        """
        if self._sekunden is not None:
            return self._sekunden
        return float(len(self.zeilen))

    @property
    def dauer_text(self) -> str:
        """Laenge der Aufnahme als ``m:ss min``."""
        minuten, sekunden = divmod(int(round(self.dauer_sekunden)), 60)
        return f"{minuten}:{sekunden:02d} min"

    @property
    def signal_text(self) -> str:
        """Mittlere Signalqualitaet in Prozent, oder ``"--"``."""
        if not self.signal:
            return "--"
        return f"{sum(self.signal) / len(self.signal):.0f} %"

    @property
    def zeitpunkt_text(self) -> str:
        """Datum und Uhrzeit einer Testaufnahme, lesbar gesetzt.

        Aus dem Dateinamen kommt ``2026-09-25 164348``; daraus wird
        ``25.09.2026, 16:43 Uhr``.
        """
        if not self.ist_test:
            return self.label
        try:
            datum, zeit = self.label.split(" ")
            jahr, monat, tag = datum.split("-")
            return f"{tag}.{monat}.{jahr}, {zeit[:2]}:{zeit[2:4]} Uhr"
        except ValueError:
            return self.label


def aufnahmen_lesen(csv_pfad: Path) -> tuple[list[list[str]], list[Aufnahme]]:
    """Liest die CSV und zerlegt sie in einzelne Aufnahmen.

    Args:
        csv_pfad: Pfad zur ``training_data.csv``.

    Returns:
        Tuple aus allen Datenzeilen und der Liste der Aufnahmen.
    """
    if not csv_pfad.exists():
        return [], []

    with open(csv_pfad, newline="") as datei:
        alle = list(csv.reader(datei))

    zeilen = [z for z in alle[1:] if z]

    aufnahmen: list[Aufnahme] = []
    for index, zeile in enumerate(zeilen):
        label = zeile[-1]
        if aufnahmen and aufnahmen[-1].label == label and aufnahmen[-1].bis == index:
            aufnahmen[-1].zeilen.append(zeile)
        else:
            aufnahmen.append(Aufnahme(len(aufnahmen) + 1, label, index, [zeile]))

    return zeilen, aufnahmen


def testaufnahmen_lesen(profil_ordner: Path) -> list[Aufnahme]:
    """Liest alle Testaufnahmen eines Profils.

    Testaufnahmen liegen als einzelne Dateien in ``testaufnahmen/`` und
    haben mehr Spalten als die Trainings-CSV (Zeitstempel und
    Signalqualitaet). Fuer die Anzeige werden daraus nur die sieben
    Messkanaele herausgeloest, damit sie sich wie eine Trainingsaufnahme
    darstellen lassen.

    Args:
        profil_ordner: Ordner des Profils.

    Returns:
        Liste von Aufnahmen, nach Dateiname sortiert (also chronologisch).
    """
    ordner = profil_ordner / "testaufnahmen"
    if not ordner.is_dir():
        return []

    aufnahmen: list[Aufnahme] = []
    for nummer, datei in enumerate(sorted(ordner.glob("*.csv")), start=1):
        try:
            with open(datei, newline="") as f:
                alle = list(csv.reader(f))
        except OSError:
            logger.warning("Testaufnahme %s nicht lesbar.", datei.name)
            continue

        if len(alle) < 2:
            continue

        kopf = alle[0]
        # Die sieben Messkanaele stehen hinter Sekunde, Uhrzeit und
        # Signalqualitaet. Ueber den Kopf gesucht statt fest verdrahtet,
        # damit ein spaeter ergaenzter Spalte nichts kaputtmacht.
        try:
            erste = kopf.index("Attention")
        except ValueError:
            erste = 3

        daten = [z for z in alle[1:] if len(z) >= erste + 7]
        zeilen = [z[erste : erste + 7] for z in daten]
        if not zeilen:
            continue

        # Signalqualitaet und verstrichene Zeit stehen vor den Messkanaelen.
        # Fehlen sie -- etwa in einer von Hand zusammengestellten Datei --,
        # bleibt es bei der Zaehlung ueber die Samples.
        signal: list[float] = []
        sekunden: float | None = None
        try:
            spalte_signal = kopf.index("Signalqualitaet")
            signal = [float(z[spalte_signal]) for z in daten]
        except (ValueError, IndexError):
            signal = []
        try:
            spalte_zeit = kopf.index("Sekunde")
            sekunden = float(daten[-1][spalte_zeit])
        except (ValueError, IndexError):
            sekunden = None

        # Der Name traegt Datum und Uhrzeit: testaufnahme_2026-09-25_164348
        stempel = datei.stem.replace("testaufnahme_", "").replace("_", " ")
        aufnahmen.append(
            Aufnahme(
                nummer,
                stempel,
                0,
                zeilen,
                datei=datei,
                signal=signal,
                sekunden=sekunden,
            )
        )

    return aufnahmen


def testaufnahmen_anderer_profile(eigenes: Path) -> dict[str, int]:
    """Zaehlt Testaufnahmen in den uebrigen Profilen.

    Testaufnahmen gehoeren zu einem Profil, weil EEG-Muster von Person zu
    Person verschieden sind. Wer eine Aufnahme unter einem Profil macht und
    die Tabelle spaeter unter einem anderen aufschlaegt, findet sie dort
    nicht -- und haelt das leicht fuer einen Fehler. Deshalb wird hier
    nachgesehen und im Dialog gesagt, wo sie liegen.

    Args:
        eigenes: Ordner des aktiven Profils, der ausgelassen wird.

    Returns:
        Dict Profilname -> Anzahl Testaufnahmen, nur fuer Profile mit
        mindestens einer.
    """
    gefunden: dict[str, int] = {}
    if not PROFIL_BASIS.is_dir():
        return gefunden

    for ordner in sorted(PROFIL_BASIS.iterdir()):
        if not ordner.is_dir() or ordner == eigenes:
            continue
        anzahl = len(list((ordner / "testaufnahmen").glob("*.csv")))
        if anzahl:
            gefunden[ordner.name] = anzahl

    return gefunden


class AufnahmeGraphDialog(QtWidgets.QDialog):
    """Zeigt die Messwerte einer einzelnen Aufnahme als Verlauf.

    Die CSV enthaelt keine Zeitstempel. Da das Headset aber gleichmaessig
    ein Paket je Sekunde liefert, entspricht die Zeilennummer der Sekunde
    seit Beginn der Aufnahme -- das reicht, um den Verlauf zu beurteilen.
    """

    def __init__(self, aufnahme: Aufnahme, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut den Graphen zu einer Aufnahme.

        Args:
            aufnahme: Die darzustellende Aufnahme.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        if aufnahme.ist_test:
            titel = (
                f"Testaufnahme vom {aufnahme.label} "
                f"({len(aufnahme.zeilen)} Samples, {aufnahme.dauer_text})"
            )
        else:
            titel = (
                f"Aufnahme {aufnahme.nummer} — {aufnahme.label} "
                f"({len(aufnahme.zeilen)} Samples, {aufnahme.dauer_text})"
            )
        self.setWindowTitle(titel)
        self.resize(900, 560)

        layout = QtWidgets.QVBoxLayout(self)

        plot = pg.PlotWidget(
            title=titel,
            axisItems={"left": ZahlenAchse("left")},
        )
        plot.setLabel("bottom", "Sekunde seit Beginn der Aufnahme", units="s")
        plot.showGrid(x=True, y=True, alpha=0.2)
        # Die Legende liegt im Graphen und verdeckt dort Kurven. Ein
        # gedeckter Hintergrund macht sie wenigstens lesbar -- ohne ihn
        # laufen Beschriftung und Messwerte ineinander.
        legende = plot.addLegend(offset=(12, 12))
        legende.setBrush(pg.mkBrush(0, 0, 0, 205))
        legende.setPen(pg.mkPen(110, 110, 110))

        layout.addWidget(plot, 1)

        self._plot = plot
        self._kurven: list = []

        x = list(range(len(aufnahme.zeilen)))
        groesster_wert = 0.0

        # Reihenfolge der Spalten entspricht TRAINING_CSV_COLUMNS ohne Label.
        for spalte, name in enumerate(_CURVE_COLORS):
            try:
                werte = [float(z[spalte]) for z in aufnahme.zeilen]
            except (ValueError, IndexError):
                continue
            groesster_wert = max(groesster_wert, max(werte, default=0.0))
            self._kurven.append(
                plot.plot(
                    x,
                    # Nullen auf den Achsenboden heben, damit sie auch im
                    # Log-Modus einen Punkt haben. Siehe _LOG_BODEN.
                    [max(wert, _LOG_BODEN) for wert in werte],
                    pen=pg.mkPen(_CURVE_COLORS[name], width=2),
                    name=_CURVE_LABELS[name],
                )
            )

        # Dieselbe Begrenzung wie im Hauptfenster: Die Aufnahme beginnt bei
        # Sekunde null und endet mit dem letzten Sample, Bandleistungen sind
        # nie negativ, und nach oben bleibt etwas Luft ueber dem groessten
        # Wert der Aufnahme. So verliert man die Kurven beim Herauszoomen
        # nicht aus den Augen.
        #
        # Die Grenzen werden erst nach dem Zeichnen gesetzt, weil der
        # groesste Wert vorher nicht bekannt ist.
        self._x_grenze = max(len(aufnahme.zeilen) - 1, 1)
        self._y_grenze = max(groesster_wert * _Y_SPIELRAUM, 10.0)

        # Dieselbe Umschaltung wie im Hauptfenster. Linear bestimmt Delta
        # allein den Massstab und drueckt Attention, Meditation und Gamma
        # auf die Nulllinie -- deshalb ist die logarithmische Achse hier
        # ebenfalls die Vorgabe.
        self.log_box = QtWidgets.QCheckBox(
            "Logarithmische Y-Achse (alle Kanäle gleichzeitig sichtbar)"
        )
        self.log_box.setChecked(_Y_LOG_STANDARD)
        self.log_box.setToolTip(
            "Attention und Meditation liegen zwischen 0 und 100, Delta geht "
            "in die Millionen. Linear ist davon nur Delta zu sehen."
        )
        self.log_box.toggled.connect(self._auf_log_umgeschaltet)
        layout.addWidget(self.log_box)

        self._auf_log_umgeschaltet(self.log_box.isChecked())

        knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)

    def _auf_log_umgeschaltet(self, logarithmisch: bool) -> None:
        """Stellt Achse, Kurven und Grenzen auf die gewaehlte Skalierung ein.

        Args:
            logarithmisch: True fuer die logarithmische Achse.
        """
        self._plot.setLogMode(y=logarithmisch)
        for kurve in self._kurven:
            kurve.setLogMode(False, logarithmisch)

        zusatz = _Y_ACHSE_ZUSATZ_LOG if logarithmisch else ""
        self._plot.setLabel("left", _Y_ACHSE_NAME + zusatz, units=_Y_ACHSE_EINHEIT)

        # Der ViewBox rechnet im Log-Modus in logarithmierten Koordinaten.
        self._plot.getViewBox().setLimits(
            xMin=0,
            xMax=self._x_grenze,
            yMin=0,
            yMax=math.log10(self._y_grenze) if logarithmisch else self._y_grenze,
        )
        self._plot.getViewBox().autoRange()


class TestaufnahmenDialog(QtWidgets.QDialog):
    """Zeigt die einfachen Aufnahmen eines Profils.

    Einfache Aufnahmen entstehen ohne Fahrbefehl und fliessen nie ins
    Modell ein. Sie beantworten eine andere Frage als Trainingsdaten:
    nicht "reicht das zum Lernen", sondern "ist in diesem Zeitraum
    ueberhaupt etwas passiert". Deshalb stehen hier andere Angaben --
    Zeitpunkt, Dauer und vor allem die Signalqualitaet, ohne die sich
    kein Verlauf beurteilen laesst.

    In der Trainingsdaten-Ansicht tauchen sie zusaetzlich auf, damit man
    dort sieht, was insgesamt aufgezeichnet wurde.
    """

    def __init__(self, profil_ordner: Path, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut den Dialog aus dem Ordner ``testaufnahmen/`` eines Profils.

        Args:
            profil_ordner: Ordner des Profils.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Einfache Aufnahmen")
        self.resize(760, 520)

        self._profil_ordner = profil_ordner
        self._aufnahmen: list[Aufnahme] = []

        layout = QtWidgets.QVBoxLayout(self)

        self.kopf_label = QtWidgets.QLabel()
        layout.addWidget(self.kopf_label)

        self.erklaerung = QtWidgets.QLabel(
            "Einfache Aufnahmen werden nur aufgezeichnet und angesehen — "
            "sie fließen nie in das Modell ein. Aufnehmen über "
            "„Modell → Einfache Aufnahme (ohne Training)“."
        )
        self.erklaerung.setWordWrap(True)
        layout.addWidget(self.erklaerung)

        self.tabelle = QtWidgets.QTableWidget(0, 5)
        self.tabelle.setHorizontalHeaderLabels(
            ["Nr.", "Aufgenommen", "Samples", "Dauer", "Signal ⌀"]
        )
        self.tabelle.verticalHeader().setVisible(False)
        self.tabelle.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.tabelle.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.tabelle.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        # Die Zeitspalte darf den uebrigen Platz nehmen. Ohne das zieht Qt
        # die letzte Spalte auf und "100 %" steht verloren in der Breite.
        kopf = self.tabelle.horizontalHeader()
        kopf.setStretchLastSection(False)
        kopf.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.tabelle.itemSelectionChanged.connect(self._auswahl_geaendert)
        self.tabelle.doubleClicked.connect(self._auf_graph_ansehen)
        layout.addWidget(self.tabelle, 1)

        # Hinweis auf Aufnahmen in anderen Profilen. Ohne ihn sucht man
        # eine Aufnahme, die unter einem anderen Profil entstanden ist.
        self.fremd_label = QtWidgets.QLabel()
        self.fremd_label.setWordWrap(True)
        self.fremd_label.setStyleSheet(f"color: {_farbe('neben').name()};")
        self.fremd_label.setVisible(False)
        layout.addWidget(self.fremd_label)

        knopfreihe = QtWidgets.QHBoxLayout()

        self.graph_button = QtWidgets.QPushButton("Im Graphen ansehen")
        self.graph_button.setToolTip("Zeigt den Verlauf dieser Aufnahme.")
        self.graph_button.clicked.connect(self._auf_graph_ansehen)
        knopfreihe.addWidget(self.graph_button)

        self.ordner_button = QtWidgets.QPushButton("Ordner öffnen")
        self.ordner_button.setToolTip(
            "Öffnet den Ordner mit den CSV-Dateien — etwa um eine Aufnahme "
            "weiterzugeben oder in einer Tabellenkalkulation zu öffnen."
        )
        self.ordner_button.clicked.connect(self._auf_ordner_oeffnen)
        knopfreihe.addWidget(self.ordner_button)

        self.loeschen_button = QtWidgets.QPushButton("Aufnahme löschen")
        self.loeschen_button.clicked.connect(self._auf_loeschen)
        knopfreihe.addWidget(self.loeschen_button)

        knopfreihe.addStretch(1)
        layout.addLayout(knopfreihe)

        knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)

        self._neu_laden()

    def _neu_laden(self) -> None:
        """Liest den Ordner neu ein und baut die Tabelle auf."""
        self._aufnahmen = testaufnahmen_lesen(self._profil_ordner)

        anzahl = len(self._aufnahmen)
        wort = "Aufnahme" if anzahl == 1 else "Aufnahmen"
        self.kopf_label.setText(
            f"<b>{anzahl}</b> einfache {wort} &nbsp;·&nbsp; "
            # Name aus dem Ordner, nicht aus CONFIG: Die Kopfzeile muss zu
            # den angezeigten Daten passen, auch nach einem Profilwechsel.
            f"Profil <b>{self._profil_ordner.name}</b>"
        )

        self.tabelle.setRowCount(anzahl)
        for reihe, aufnahme in enumerate(self._aufnahmen):
            texte = [
                str(aufnahme.nummer),
                aufnahme.zeitpunkt_text,
                str(len(aufnahme.zeilen)),
                aufnahme.dauer_text,
                aufnahme.signal_text,
            ]
            for spalte, text in enumerate(texte):
                zelle = QtWidgets.QTableWidgetItem(text)
                if aufnahme.datei is not None:
                    zelle.setToolTip(aufnahme.datei.name)
                self.tabelle.setItem(reihe, spalte, zelle)

        self.tabelle.resizeColumnsToContents()

        fremde = testaufnahmen_anderer_profile(self._profil_ordner)
        if fremde:
            liste = ", ".join(f"{name} ({zahl})" for name, zahl in fremde.items())
            self.fremd_label.setText(
                f"Weitere einfache Aufnahmen liegen in: <b>{liste}</b>. "
                "Zum Ansehen dort das Profil wechseln."
            )
            self.fremd_label.setVisible(True)
        else:
            self.fremd_label.setVisible(False)

        self._auswahl_geaendert()

    def _gewaehlte(self) -> Aufnahme | None:
        """Liefert die markierte Aufnahme, falls es eine gibt."""
        reihen = self.tabelle.selectionModel().selectedRows()
        if not reihen:
            return None
        index = reihen[0].row()
        if 0 <= index < len(self._aufnahmen):
            return self._aufnahmen[index]
        return None

    def _auswahl_geaendert(self) -> None:
        """Schaltet die Knoepfe je nach Auswahl frei."""
        vorhanden = self._gewaehlte() is not None
        self.graph_button.setEnabled(vorhanden)
        self.loeschen_button.setEnabled(vorhanden)
        self.ordner_button.setEnabled((self._profil_ordner / "testaufnahmen").is_dir())

    def _auf_graph_ansehen(self, *_egal) -> None:
        """Oeffnet den Verlauf der markierten Aufnahme."""
        aufnahme = self._gewaehlte()
        if aufnahme is None:
            return
        AufnahmeGraphDialog(aufnahme, self).exec_()

    def _auf_ordner_oeffnen(self, *_egal) -> None:
        """Zeigt den Ordner mit den CSV-Dateien im Dateiverwalter."""
        ordner = self._profil_ordner / "testaufnahmen"
        if not ordner.is_dir():
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(ordner.resolve()))
        )

    def _auf_loeschen(self, *_egal) -> None:
        """Loescht die markierte Aufnahme nach Rueckfrage."""
        aufnahme = self._gewaehlte()
        if aufnahme is None or aufnahme.datei is None:
            return

        antwort = QtWidgets.QMessageBox.question(
            self,
            "Aufnahme löschen",
            f"Die einfache Aufnahme vom {aufnahme.zeitpunkt_text} "
            f"({len(aufnahme.zeilen)} Samples) löschen?\n\n"
            f"Datei: {aufnahme.datei.name}\n"
            "Das lässt sich nicht rückgängig machen.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if antwort != QtWidgets.QMessageBox.Yes:
            return

        try:
            aufnahme.datei.unlink()
        except OSError as fehler:
            QtWidgets.QMessageBox.critical(
                self, "Fehler", f"Die Datei ließ sich nicht löschen:\n{fehler}"
            )
            return

        logger.info("Testaufnahme %s geloescht.", aufnahme.datei.name)
        self._neu_laden()


class TrainingsdatenDialog(QtWidgets.QDialog):
    """Zeigt die gesammelten Trainingsdaten und was noch fehlt.

    Zwei Zahlen entscheiden ueber die Qualitaet eines Modells:

    * **Samples je Klasse** -- wie viel Material vorliegt. Ein Paket je
      Sekunde, 180 Samples sind also drei Minuten.
    * **Getrennte Aufnahmen je Klasse** -- der wichtigere Wert. Bewertet
      wird ueber ganze zurueckgehaltene Aufnahmen; dafuer braucht es
      mindestens drei, besser fuenf. Darunter faellt die Bewertung auf den
      Zufallssplit zurueck und meldet viel zu optimistische Werte -- genau
      der Fehler hinter den scheinbaren 100 Prozent im August.

    Klassen ohne jede Aufnahme gelten hier ausdruecklich **nicht** als
    Mangel. Wer rueckwaerts nicht steuern will, laesst BACKWARD eben leer.
    Rot wird nur, was angefangen und dann zu duenn geblieben ist.
    """

    def __init__(self, csv_pfad: Path, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut den Dialog aus der Trainings-CSV.

        Args:
            csv_pfad: Pfad zur ``training_data.csv`` des aktiven Profils.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Gesammelte Trainingsdaten")
        self.resize(860, 760)

        self._csv_pfad = csv_pfad
        self._zeilen: list[list[str]] = []
        self._aufnahmen: list[Aufnahme] = []

        layout = QtWidgets.QVBoxLayout(self)

        self.kopf_label = QtWidgets.QLabel()
        layout.addWidget(self.kopf_label)

        layout.addWidget(QtWidgets.QLabel("<b>Je Fahrbefehl</b>"))
        self.uebersicht = QtWidgets.QTableWidget(0, 4)
        self.uebersicht.setHorizontalHeaderLabels(
            ["Fahrbefehl", "Samples", "Dauer", "getrennte Aufnahmen"]
        )
        self.uebersicht.verticalHeader().setVisible(False)
        self.uebersicht.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.uebersicht.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.uebersicht)

        self.empfehlung_label = QtWidgets.QLabel()
        self.empfehlung_label.setWordWrap(True)
        self.empfehlung_label.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(QtWidgets.QLabel("<b>Empfehlung</b>"))
        layout.addWidget(self.empfehlung_label)

        layout.addWidget(QtWidgets.QLabel("<b>Einzelne Aufnahmen</b>"))
        # Nur Trainingsaufnahmen. Die einfachen Aufnahmen haben ihr eigenes
        # Fenster ("Einfache Aufnahmen ansehen") -- hier standen sie bis
        # 2026-09-27 mit drin und verwischten, was ins Modell einfliesst.
        self.aufnahme_tabelle = QtWidgets.QTableWidget(0, 4)
        self.aufnahme_tabelle.setHorizontalHeaderLabels(
            ["Nr.", "Fahrbefehl", "Samples", "Dauer"]
        )
        self.aufnahme_tabelle.verticalHeader().setVisible(False)
        self.aufnahme_tabelle.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.aufnahme_tabelle.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.aufnahme_tabelle.setSelectionMode(QtWidgets.QTableWidget.SingleSelection)
        self.aufnahme_tabelle.horizontalHeader().setStretchLastSection(True)
        self.aufnahme_tabelle.itemSelectionChanged.connect(self._auswahl_geaendert)
        self.aufnahme_tabelle.doubleClicked.connect(self._auf_graph_ansehen)
        layout.addWidget(self.aufnahme_tabelle, 1)

        knopfreihe = QtWidgets.QHBoxLayout()
        self.graph_button = QtWidgets.QPushButton("Im Graphen ansehen")
        self.graph_button.clicked.connect(self._auf_graph_ansehen)
        knopfreihe.addWidget(self.graph_button)

        self.label_button = QtWidgets.QPushButton("Fahrbefehl ändern …")
        self.label_button.setToolTip(
            "Ordnet die Aufnahme einem anderen Fahrbefehl zu — etwa wenn "
            "versehentlich LEFT statt RIGHT aufgezeichnet wurde."
        )
        self.label_button.clicked.connect(self._auf_label_aendern)
        knopfreihe.addWidget(self.label_button)

        self.loeschen_button = QtWidgets.QPushButton("Aufnahme löschen")
        self.loeschen_button.clicked.connect(self._auf_loeschen)
        knopfreihe.addWidget(self.loeschen_button)

        knopfreihe.addStretch(1)
        layout.addLayout(knopfreihe)

        knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)

        self._neu_laden()

    # ------------------------------------------------------------------
    # Aufbau der Anzeige
    # ------------------------------------------------------------------
    def _neu_laden(self) -> None:
        """Liest die Trainingsaufnahmen neu ein und baut die Anzeigen auf."""
        self._zeilen, self._aufnahmen = aufnahmen_lesen(self._csv_pfad)

        self.kopf_label.setText(
            f"<b>{len(self._zeilen)}</b> Samples in "
            f"<b>{len(self._aufnahmen)}</b> Trainingsaufnahmen"
            f" &nbsp;·&nbsp; Profil <b>{self._csv_pfad.parent.name}</b>"
        )

        statistik = self._auswerten()
        self._uebersicht_fuellen(statistik)
        self.empfehlung_label.setText(self._empfehlung(statistik))
        self._aufnahmen_fuellen()
        self._auswahl_geaendert()

    def _auswerten(self) -> dict:
        """Zaehlt Samples und Aufnahmen je Label.

        Returns:
            Dict Label -> {"samples": int, "aufnahmen": int}.
        """
        statistik: dict[str, dict[str, int]] = {
            label: {"samples": 0, "aufnahmen": 0} for label in CONFIG.active_labels
        }
        for aufnahme in self._aufnahmen:
            eintrag = statistik.setdefault(aufnahme.label, {"samples": 0, "aufnahmen": 0})
            eintrag["samples"] += len(aufnahme.zeilen)
            eintrag["aufnahmen"] += 1
        return statistik

    def _uebersicht_fuellen(self, statistik: dict) -> None:
        """Fuellt die Tabelle je Fahrbefehl und faerbt sie ein."""
        self.uebersicht.setRowCount(len(statistik))
        self.uebersicht.setMaximumHeight(46 + 28 * len(statistik))

        for reihe, (label, werte) in enumerate(sorted(statistik.items())):
            samples = werte["samples"]
            aufnahmen = werte["aufnahmen"]
            minuten, sekunden = divmod(samples, 60)

            if samples == 0:
                farbe = "neben"
                anmerkung = "nicht trainiert"
            elif label not in CONFIG.active_labels:
                # Aufgenommen, aber gerade nicht gesteuert (etwa LEFT). Die
                # Empfehlung darunter laesst solche Befehle aus -- rot waere
                # hier ein Widerspruch zu "ausreichend abgedeckt".
                farbe = "neben"
                anmerkung = "nicht aktiv"
            elif samples < _SAMPLES_SOLL or aufnahmen < _AUFNAHMEN_MINDESTENS:
                farbe = "zu_wenig"
                anmerkung = ""
            else:
                farbe = "genug"
                anmerkung = ""

            texte = [
                f"{label} — {anmerkung}" if anmerkung else label,
                str(samples),
                f"{minuten}:{sekunden:02d} min",
                str(aufnahmen),
            ]
            for spalte, text in enumerate(texte):
                zelle = QtWidgets.QTableWidgetItem(text)
                zelle.setForeground(_farbe(farbe))
                self.uebersicht.setItem(reihe, spalte, zelle)

        self.uebersicht.resizeColumnsToContents()

    def _empfehlung(self, statistik: dict) -> str:
        """Formuliert, welche Klassen noch Material brauchen.

        Args:
            statistik: Ergebnis von :meth:`_auswerten`.

        Returns:
            Fertiger HTML-Text.
        """
        hinweise: list[str] = []
        ungenutzt: list[str] = []

        for label in CONFIG.active_labels:
            werte = statistik.get(label, {"samples": 0, "aufnahmen": 0})
            samples = werte["samples"]
            aufnahmen = werte["aufnahmen"]

            if samples == 0:
                ungenutzt.append(label)
                continue

            teile = []
            if samples < _SAMPLES_SOLL:
                fehlend = _SAMPLES_SOLL - samples
                teile.append(
                    f"noch rund {fehlend} Samples "
                    f"({fehlend // 60}:{fehlend % 60:02d} min)"
                )
            if aufnahmen < _AUFNAHMEN_MINDESTENS:
                teile.append(
                    f"nur {aufnahmen} getrennte Aufnahme(n) — unter "
                    f"{_AUFNAHMEN_MINDESTENS} ist die Genauigkeitsangabe wertlos"
                )
            elif aufnahmen < _AUFNAHMEN_SOLL:
                teile.append(
                    f"{aufnahmen} Aufnahmen — mit {_AUFNAHMEN_SOLL} wird die "
                    "Bewertung verlässlicher"
                )

            if teile:
                hinweise.append(f"<b>{label}</b>: " + ", ".join(teile) + ".")

        vorhandene = [w["samples"] for w in statistik.values() if w["samples"] > 0]
        if len(vorhandene) > 1 and min(vorhandene) * 2 < max(vorhandene):
            hinweise.append(
                "Die trainierten Klassen sind ungleich stark vertreten "
                f"({min(vorhandene)} gegen {max(vorhandene)} Samples). Das "
                "Modell lernt dann vor allem, im Zweifel die häufigste zu "
                "raten. Besser die schwachen nachholen als die starken kürzen."
            )

        if not hinweise:
            hinweise.append("Alle trainierten Klassen sind ausreichend abgedeckt.")

        if ungenutzt:
            hinweise.append(
                "Ohne Aufnahmen und damit ungenutzt: <b>"
                + "</b>, <b>".join(ungenutzt)
                + "</b>. Das ist kein Mangel — diese Befehle werden schlicht "
                "nicht gesteuert. Sollen sie doch dazukommen, hier aufnehmen."
            )

        return "<br><br>".join(f"• {h}" for h in hinweise)

    def _aufnahmen_fuellen(self) -> None:
        """Fuellt die Tabelle mit den Trainingsaufnahmen des Profils."""
        self.aufnahme_tabelle.setRowCount(len(self._aufnahmen))

        for reihe, aufnahme in enumerate(self._aufnahmen):
            texte = [
                str(aufnahme.nummer),
                aufnahme.label,
                str(len(aufnahme.zeilen)),
                aufnahme.dauer_text,
            ]
            for spalte, text in enumerate(texte):
                self.aufnahme_tabelle.setItem(reihe, spalte, QtWidgets.QTableWidgetItem(text))

        self.aufnahme_tabelle.resizeColumnsToContents()

    # ------------------------------------------------------------------
    # Bedienung
    # ------------------------------------------------------------------
    def _gewaehlte_aufnahme(self) -> Aufnahme | None:
        """Liefert die aktuell markierte Aufnahme, falls es eine gibt."""
        reihen = self.aufnahme_tabelle.selectionModel().selectedRows()
        if not reihen:
            return None
        index = reihen[0].row()
        if 0 <= index < len(self._aufnahmen):
            return self._aufnahmen[index]
        return None

    def _auswahl_geaendert(self) -> None:
        """Schaltet die Knoepfe je nach Auswahl frei."""
        vorhanden = self._gewaehlte_aufnahme() is not None
        self.graph_button.setEnabled(vorhanden)
        self.loeschen_button.setEnabled(vorhanden)
        self.label_button.setEnabled(vorhanden)

    def _auf_graph_ansehen(self, *_egal) -> None:
        """Oeffnet den Verlauf der markierten Aufnahme."""
        aufnahme = self._gewaehlte_aufnahme()
        if aufnahme is None:
            return
        AufnahmeGraphDialog(aufnahme, self).exec_()

    def _csv_schreiben(self, zeilen: list[list[str]]) -> None:
        """Schreibt die Trainings-CSV komplett neu.

        Args:
            zeilen: Alle Datenzeilen ohne Kopfzeile.
        """
        with open(self._csv_pfad, "w", newline="") as datei:
            schreiber = csv.writer(datei)
            schreiber.writerow(TRAINING_CSV_COLUMNS)
            schreiber.writerows(zeilen)

    def _auf_label_aendern(self, *_egal) -> None:
        """Ordnet die markierte Aufnahme einem anderen Fahrbefehl zu.

        Gedacht fuer Vertipper beim Aufnehmen: Wer versehentlich LEFT
        gewaehlt hat, obwohl er an RIGHT gedacht hat, muss die Aufnahme
        nicht wegwerfen. Die Messwerte bleiben unveraendert, nur das Label
        in der letzten Spalte wird ausgetauscht.

        Angeboten wird das **vollstaendige** Vokabular aus
        ``CONFIG.labels``, nicht nur die aktiven Befehle -- sonst liesse
        sich eine Aufnahme nicht auf einen Befehl umschreiben, der gerade
        nicht trainiert wird.
        """
        aufnahme = self._gewaehlte_aufnahme()
        if aufnahme is None:
            return

        moeglich = list(CONFIG.labels)
        aktuell = moeglich.index(aufnahme.label) if aufnahme.label in moeglich else 0

        neu, ok = QtWidgets.QInputDialog.getItem(
            self,
            "Fahrbefehl ändern",
            f"Aufnahme {aufnahme.nummer} ist als „{aufnahme.label}“ "
            f"aufgezeichnet ({len(aufnahme.zeilen)} Samples, "
            f"{aufnahme.dauer_text}).\n\nNeuer Fahrbefehl:",
            moeglich,
            aktuell,
            False,
        )
        if not ok or neu == aufnahme.label:
            return

        for zeile in self._zeilen[aufnahme.von : aufnahme.bis]:
            zeile[-1] = neu

        self._csv_schreiben(self._zeilen)
        logger.info(
            "Aufnahme %d (%d Samples) von %s auf %s umgeschrieben.",
            aufnahme.nummer,
            len(aufnahme.zeilen),
            aufnahme.label,
            neu,
        )
        self._neu_laden()

        # Grenzt die Aufnahme jetzt an eine gleich benannte, verschmelzen
        # die beiden zu einer einzigen -- Aufnahmen sind zusammenhaengende
        # Bloecke gleicher Labels. Das ist gewollt, sieht in der Liste aber
        # nach einem Sprung aus, deshalb der Hinweis.
        if len(self._aufnahmen) < aufnahme.nummer or self._verschmolzen(aufnahme, neu):
            QtWidgets.QMessageBox.information(
                self,
                "Aufnahmen zusammengefasst",
                f"Die Aufnahme grenzt jetzt an eine andere mit „{neu}“ und "
                "wurde mit ihr zu einer zusammengefasst.\n\n"
                "Das entspricht dem, was der Klassifikator sieht: Er grenzt "
                "Aufnahmen an den Labelwechseln ab. Für die Bewertung zählt "
                "das künftig als eine Aufnahme statt zwei.",
            )

    def _verschmolzen(self, vorher: Aufnahme, neues_label: str) -> bool:
        """Prueft, ob die umbenannte Aufnahme mit einer Nachbarin verschmolz.

        Args:
            vorher: Die Aufnahme vor dem Umbenennen.
            neues_label: Das neu vergebene Label.

        Returns:
            True, wenn es hinterher weniger Aufnahmen gibt als vorher.
        """
        # Nach dem Neuladen deckt eine Aufnahme den alten Bereich ab. Ist
        # sie laenger als die urspruengliche, wurde verschmolzen.
        for aufnahme in self._aufnahmen:
            if aufnahme.von <= vorher.von < aufnahme.bis:
                return len(aufnahme.zeilen) > len(vorher.zeilen)
        return False

    def _auf_loeschen(self, *_egal) -> None:
        """Loescht die markierte Aufnahme aus der CSV, nach Rueckfrage."""
        aufnahme = self._gewaehlte_aufnahme()
        if aufnahme is None:
            return

        antwort = QtWidgets.QMessageBox.question(
            self,
            "Aufnahme löschen",
            f"Aufnahme {aufnahme.nummer} ({aufnahme.label}, "
            f"{len(aufnahme.zeilen)} Samples, {aufnahme.dauer_text}) "
            "endgültig aus den Trainingsdaten entfernen?\n\n"
            "Das Modell ändert sich dadurch erst beim nächsten Training.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if antwort != QtWidgets.QMessageBox.Yes:
            return

        self._csv_schreiben(self._zeilen[: aufnahme.von] + self._zeilen[aufnahme.bis :])

        logger.info(
            "Aufnahme %d (%s, %d Samples) aus %s geloescht.",
            aufnahme.nummer,
            aufnahme.label,
            len(aufnahme.zeilen),
            self._csv_pfad,
        )
        self._neu_laden()


class KonfusionsmatrixDialog(QtWidgets.QDialog):
    """Zeigt, welche Fahrbefehle das Modell miteinander verwechselt.

    Eine Genauigkeit von 75 Prozent sagt nur, wie oft das Modell richtig
    liegt -- nicht, *was* es verwechselt. Genau das ist aber die
    interessante Frage: Ob sich zwei Zustaende ueberhaupt trennen lassen,
    zeigt sich daran, ob sie systematisch gegeneinander vertauscht werden.

    Gelesen wird zeilenweise: Eine Zeile sind alle Fenster, die
    tatsaechlich zu diesem Fahrbefehl gehoeren; die Spalten sagen, wofuer
    das Modell sie gehalten hat. Auf der Diagonalen steht also das
    Richtige, alles daneben ist ein Fehler.

    Die Zahlen stammen aus denselben zurueckgehaltenen Aufnahmen wie die
    Genauigkeit -- nicht aus dem Trainingsmaterial.
    """

    def __init__(self, classifier, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut den Dialog aus dem Zustand des Klassifikators.

        Args:
            classifier: Der ``BCIClassifier`` mit der letzten Bewertung.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Welche Befehle werden verwechselt?")
        self.resize(720, 620)

        labels = list(getattr(classifier, "last_confusion_labels", []))
        matrix = [list(z) for z in getattr(classifier, "last_confusion", [])]

        layout = QtWidgets.QVBoxLayout(self)

        if not matrix:
            layout.addWidget(
                QtWidgets.QLabel(
                    "Noch keine Auswertung vorhanden.\n\n"
                    "Trainiere das Modell einmal neu, dann steht die Matrix "
                    "zur Verfügung."
                )
            )
            knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            knoepfe.rejected.connect(self.reject)
            layout.addWidget(knoepfe)
            return

        bestes = max(
            classifier.last_accuracies,
            key=classifier.last_accuracies.get,
            default="--",
        )
        genauigkeit = classifier.last_accuracies.get(bestes, 0.0)

        layout.addWidget(
            QtWidgets.QLabel(
                f"Verfahren: <b>{bestes}</b> &nbsp;·&nbsp; "
                f"Genauigkeit <b>{genauigkeit * 100:.1f} %</b> &nbsp;·&nbsp; "
                f"Basisrate {classifier.last_baseline * 100:.1f} %<br>"
                f"<span style='color:{_farbe('neben').name()}'>"
                f"Bewertung: {classifier.last_eval_method}</span>"
            )
        )

        hinweis = QtWidgets.QLabel(
            "<b>Zeile</b> = was es tatsächlich war, <b>Spalte</b> = wofür das "
            "Modell es gehalten hat. Auf der Diagonalen steht das Richtige."
        )
        hinweis.setWordWrap(True)
        layout.addWidget(hinweis)

        layout.addWidget(self._matrix_bauen(labels, matrix))
        layout.addWidget(QtWidgets.QLabel("<b>Was daraus folgt</b>"))
        deutung = QtWidgets.QLabel(self._deutung(labels, matrix))
        deutung.setWordWrap(True)
        deutung.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(deutung, 1)

        knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)

    def _matrix_bauen(self, labels: list[str], matrix: list[list[int]]) -> QtWidgets.QTableWidget:
        """Baut die Tabelle mit Anzahl und Zeilenanteil je Zelle."""
        tabelle = QtWidgets.QTableWidget(len(labels), len(labels) + 1)
        tabelle.setHorizontalHeaderLabels(labels + ["davon richtig"])
        tabelle.setVerticalHeaderLabels(labels)
        tabelle.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        tabelle.horizontalHeader().setStretchLastSection(True)

        for i, _ in enumerate(labels):
            summe = sum(matrix[i]) or 1
            for j, _ in enumerate(labels):
                anzahl = matrix[i][j]
                anteil = anzahl / summe * 100
                zelle = QtWidgets.QTableWidgetItem(f"{anzahl}\n{anteil:.0f} %")
                zelle.setTextAlignment(QtCore.Qt.AlignCenter)

                if i == j:
                    zelle.setForeground(_farbe("genug"))
                elif anteil >= 20:
                    # Ab einem Fuenftel ist es keine Streuung mehr, sondern
                    # eine systematische Verwechslung.
                    zelle.setForeground(_farbe("zu_wenig"))
                else:
                    zelle.setForeground(_farbe("neben"))
                tabelle.setItem(i, j, zelle)

            treffer = matrix[i][i] / summe * 100
            zelle = QtWidgets.QTableWidgetItem(f"{treffer:.1f} %")
            zelle.setTextAlignment(QtCore.Qt.AlignCenter)
            zelle.setForeground(_farbe("genug" if treffer >= 70 else "zu_wenig"))
            tabelle.setItem(i, len(labels), zelle)

        tabelle.resizeColumnsToContents()
        tabelle.resizeRowsToContents()
        return tabelle

    def _deutung(self, labels: list[str], matrix: list[list[int]]) -> str:
        """Formuliert, was in der Matrix auffaellt.

        Args:
            labels: Die Klassennamen in Zeilen- und Spaltenreihenfolge.
            matrix: Die Konfusionsmatrix.

        Returns:
            Fertiger HTML-Text.
        """
        saetze: list[str] = []

        # Zuverlaessige und schwache Klassen
        zuverlaessig, schwach = [], []
        for i, label in enumerate(labels):
            summe = sum(matrix[i]) or 1
            treffer = matrix[i][i] / summe * 100
            (zuverlaessig if treffer >= 70 else schwach).append((label, treffer))

        if zuverlaessig:
            saetze.append(
                "Zuverlässig erkannt: "
                + ", ".join(f"<b>{l}</b> ({t:.0f} %)" for l, t in zuverlaessig)
                + "."
            )
        if schwach:
            saetze.append(
                "Schwach: "
                + ", ".join(f"<b>{l}</b> ({t:.0f} %)" for l, t in schwach)
                + "."
            )

        # Groesste systematische Verwechslung, in beide Richtungen gezaehlt
        paare: dict[tuple[str, str], int] = {}
        for i, a in enumerate(labels):
            for j, b in enumerate(labels):
                if i == j:
                    continue
                schluessel = tuple(sorted((a, b)))
                paare[schluessel] = paare.get(schluessel, 0) + matrix[i][j]

        if paare:
            (a, b), anzahl = max(paare.items(), key=lambda kv: kv[1])
            gesamt = sum(sum(zeile) for zeile in matrix)
            if anzahl and anzahl / gesamt >= 0.1:
                saetze.append(
                    f"Am häufigsten verwechselt werden <b>{a}</b> und "
                    f"<b>{b}</b> — {anzahl} von {gesamt} Fenstern, also "
                    f"{anzahl / gesamt * 100:.0f} Prozent aller Fälle. Wenn "
                    "zwei Befehle so systematisch ineinander laufen, liegt "
                    "das selten am Modell: Die beiden mentalen Zustände "
                    "sehen für das Headset dann schlicht gleich aus. Zwei "
                    "deutlicher verschiedene Zustände zu wählen bringt mehr "
                    "als mehr Aufnahmen desselben."
                )

        if len(labels) < 3:
            saetze.append(
                "Es sind nur zwei Klassen im Spiel — für die übrigen "
                "Fahrbefehle liegen keine Trainingsdaten vor."
            )

        return "<br><br>".join(f"• {t}" for t in saetze)


class RohdatenDialog(QtWidgets.QDialog):
    """Zeigt live, was vom EEG-Arduino ankommt -- Zeile fuer Zeile.

    Wie der serielle Monitor der Arduino-IDE, nur fuer beide Wege (Funk und
    Kabel) und mit einer Tabelle, in der die elf Werte unter ihrem Namen
    stehen. Angezeigt wird **jede** Zeile, auch die, die das Programm
    verwirft: Wenn im Graphen nichts erscheint, sieht man hier, ob gar
    nichts ankommt, ob Pakete ohne Hautkontakt kommen (Signal 200) oder ob
    der Datenstrom verstuemmelt ist.

    Das Fenster ist nicht modal -- es kann neben dem Hauptfenster offen
    bleiben, waehrend dort gemessen wird. Es liest aus dem Mitschnitt des
    Empfaengers (``RohdatenMitschnitt``) und haelt dort nichts an: Anhalten
    friert nur die Anzeige ein, empfangen wird weiter.
    """

    # Der Status steht gleich hinter der Uhrzeit: Er ist das, wonach man
    # sucht, und rutschte am rechten Ende bei schmalem Fenster aus dem Bild.
    _SPALTEN = [
        "Uhrzeit", "Status", "Signal", "Attention", "Meditation", "Delta",
        "Theta", "Low Alpha", "High Alpha", "Low Beta", "High Beta",
        "Low Gamma", "High Gamma",
    ]
    _ERSTE_WERTSPALTE = 2
    _MAX_ZEILEN = 1000
    _TAKT_MS = 250

    def __init__(self, receiver, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut das Fenster.

        Args:
            receiver: Der EEG-Empfaenger (Funk oder Kabel) mit ``mitschnitt``.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Rohdaten vom Headset")
        # Breit genug fuer alle dreizehn Spalten ohne waagerechtes Scrollen.
        self.resize(1240, 560)
        self.setModal(False)

        self._receiver = receiver
        self._letzte_nummer = 0
        self._text_zeilen: list[str] = []

        layout = QtWidgets.QVBoxLayout(self)

        self.kopf_label = QtWidgets.QLabel()
        layout.addWidget(self.kopf_label)

        erklaerung = QtWidgets.QLabel(
            "Jede Zeile, so wie sie vom Uno ankommt — etwa eine je Sekunde. "
            "<b>Signal</b> ist der Rohwert des ThinkGear-Chips: 0 heißt "
            "bester Kontakt, 200 heißt kein Kontakt zur Haut."
        )
        erklaerung.setWordWrap(True)
        layout.addWidget(erklaerung)

        self.reiter = QtWidgets.QTabWidget()

        self.tabelle = QtWidgets.QTableWidget(0, len(self._SPALTEN))
        self.tabelle.setHorizontalHeaderLabels(self._SPALTEN)
        self.tabelle.verticalHeader().setVisible(False)
        self.tabelle.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.tabelle.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.tabelle.setWordWrap(False)
        kopf = self.tabelle.horizontalHeader()
        kopf.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        kopf.setStretchLastSection(True)
        self.reiter.addTab(self.tabelle, "Tabelle")

        # Unveraendert, wie empfangen -- nur mit Uhrzeit davor. Fester
        # Zeichenabstand, damit die Kommas untereinander stehen.
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(self._MAX_ZEILEN)
        self.text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.reiter.addTab(self.text, "Text (wie empfangen)")

        layout.addWidget(self.reiter, 1)

        knopfreihe = QtWidgets.QHBoxLayout()

        self.anhalten_button = QtWidgets.QPushButton("Anzeige anhalten")
        self.anhalten_button.setCheckable(True)
        self.anhalten_button.setToolTip(
            "Friert nur die Anzeige ein. Empfangen und gemessen wird weiter; "
            "beim Fortsetzen wird nachgetragen."
        )
        self.anhalten_button.toggled.connect(self._auf_anhalten)
        knopfreihe.addWidget(self.anhalten_button)

        self.leeren_button = QtWidgets.QPushButton("Leeren")
        self.leeren_button.setToolTip("Leert die Anzeige. Die Messung bleibt unberührt.")
        self.leeren_button.clicked.connect(self._auf_leeren)
        knopfreihe.addWidget(self.leeren_button)

        self.kopieren_button = QtWidgets.QPushButton("Kopieren")
        self.kopieren_button.setToolTip(
            "Kopiert die angezeigten Zeilen als Text in die Zwischenablage."
        )
        self.kopieren_button.clicked.connect(self._auf_kopieren)
        knopfreihe.addWidget(self.kopieren_button)

        knopfreihe.addStretch(1)
        layout.addLayout(knopfreihe)

        knoepfe = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        knoepfe.rejected.connect(self.close)
        layout.addWidget(knoepfe)

        # Nur solange das Fenster offen ist -- siehe showEvent/hideEvent.
        self._takt = QtCore.QTimer(self)
        self._takt.setInterval(self._TAKT_MS)
        self._takt.timeout.connect(self._aktualisieren)

    # ------------------------------------------------------------------
    # Lebenszyklus
    # ------------------------------------------------------------------
    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt-Konvention
        """Beginnt beim Oeffnen mit dem Nachlesen."""
        super().showEvent(event)
        self._aktualisieren()
        self._takt.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt-Konvention
        """Hoert beim Schliessen auf, nachzulesen -- das Fenster bleibt erhalten."""
        self._takt.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Anzeige
    # ------------------------------------------------------------------
    def _mitschnitt(self) -> RohdatenMitschnitt | None:
        return getattr(self._receiver, "mitschnitt", None)

    def _aktualisieren(self) -> None:
        """Holt neue Zeilen aus dem Mitschnitt und haengt sie an."""
        mitschnitt = self._mitschnitt()
        self._kopf_setzen(mitschnitt)
        if mitschnitt is None or self.anhalten_button.isChecked():
            return

        neue = mitschnitt.seit(self._letzte_nummer)
        if not neue:
            return
        self._letzte_nummer = neue[-1][0]

        # Nur mitscrollen, wenn man ohnehin am Ende steht. Wer nach oben
        # gescrollt hat, um eine Zeile zu lesen, soll nicht weggerissen
        # werden.
        leiste = self.tabelle.verticalScrollBar()
        am_ende = leiste.value() >= leiste.maximum() - 2

        self.tabelle.setUpdatesEnabled(False)
        for _nummer, zeitpunkt, text, art in neue:
            uhrzeit = time.strftime("%H:%M:%S", time.localtime(zeitpunkt))
            self._zeile_anhaengen(uhrzeit, text, art)
            zeile = f"{uhrzeit}  {text}"
            self._text_zeilen.append(zeile)
            self.text.appendPlainText(zeile)

        ueberzaehlig = self.tabelle.rowCount() - self._MAX_ZEILEN
        for _ in range(max(0, ueberzaehlig)):
            self.tabelle.removeRow(0)
        del self._text_zeilen[: max(0, len(self._text_zeilen) - self._MAX_ZEILEN)]
        self.tabelle.setUpdatesEnabled(True)

        if am_ende:
            self.tabelle.scrollToBottom()

    def _kopf_setzen(self, mitschnitt: RohdatenMitschnitt | None) -> None:
        """Schreibt Quelle, Verbindung und Zaehler in die Kopfzeile."""
        if mitschnitt is None:
            self.kopf_label.setText("Dieser Empfänger zeichnet keine Rohdaten auf.")
            return

        ble = type(self._receiver).__name__ == "BleEegReceiver"
        quelle = "Bluetooth" if ble else "USB-Kabel"
        verbunden = getattr(self._receiver, "connected", False)
        zustand = "verbunden" if verbunden else "<b>nicht verbunden</b>"

        zuletzt = mitschnitt.zuletzt
        if zuletzt is None:
            juengste = "noch keine Zeile empfangen"
        else:
            vor = time.time() - zuletzt
            juengste = (
                f"letzte Zeile vor {vor:.0f} s"
                if vor < 120
                else f"letzte Zeile um {time.strftime('%H:%M:%S', time.localtime(zuletzt))}"
            )

        angehalten = " &nbsp;·&nbsp; <b>Anzeige angehalten</b>" if self.anhalten_button.isChecked() else ""
        self.kopf_label.setText(
            f"Quelle <b>{quelle}</b> ({zustand}) &nbsp;·&nbsp; "
            f"<b>{mitschnitt.gesamt}</b> Zeilen empfangen, davon "
            f"<b>{mitschnitt.verworfen}</b> unlesbar &nbsp;·&nbsp; {juengste}{angehalten}"
        )

    def _zeile_anhaengen(self, uhrzeit: str, text: str, art: str) -> None:
        """Haengt eine Zeile an die Tabelle an.

        Args:
            uhrzeit: Empfangszeit als Text.
            text: Die Zeile wie empfangen.
            art: Art laut ``RohdatenMitschnitt``.
        """
        reihe = self.tabelle.rowCount()
        self.tabelle.insertRow(reihe)
        self.tabelle.setItem(reihe, 0, QtWidgets.QTableWidgetItem(uhrzeit))
        erste = self._ERSTE_WERTSPALTE

        if art == RohdatenMitschnitt.EEG:
            werte = [int(teil) for teil in text.split(",")]
            for spalte, wert in enumerate(werte, start=erste):
                zelle = QtWidgets.QTableWidgetItem(zahl_deutsch(wert))
                zelle.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.tabelle.setItem(reihe, spalte, zelle)

            signal = werte[0]
            if signal >= CONFIG.invalid_signal_quality:
                status, farbe = "kein Kontakt", "zu_wenig"
                erklaerung = (
                    "Rohwert 200: kein Kontakt zur Haut. Das Programm wertet "
                    "diese Zeile nicht aus — kein Graph, keine Vorhersage."
                )
            elif signal > 0:
                prozent = (1 - signal / CONFIG.invalid_signal_quality) * 100
                status, farbe = f"schwach ({prozent:.0f} %)", "neben"
                erklaerung = "Kontakt vorhanden, aber nicht optimal. Wird ausgewertet."
            else:
                status, farbe = "gültig", "genug"
                erklaerung = "Bester Kontakt. Wird ausgewertet."
        else:
            # Keine elf Zahlen: Die Zeile steht unzerlegt ueber die
            # Wertespalten hinweg, damit man sieht, was wirklich kam.
            roh = QtWidgets.QTableWidgetItem(text or "(leer)")
            self.tabelle.setItem(reihe, erste, roh)
            self.tabelle.setSpan(reihe, erste, 1, len(self._SPALTEN) - erste)
            if art == RohdatenMitschnitt.BATTERIE:
                status, farbe = "Batterie", "neben"
                erklaerung = "Batteriemeldung des Arduino. Wird ignoriert."
            else:
                status, farbe = "unlesbar", "zu_wenig"
                erklaerung = (
                    "Keine elf Zahlen — etwa ein abgeschnittenes Paket. "
                    "Wird verworfen."
                )

        zelle = QtWidgets.QTableWidgetItem(status)
        zelle.setForeground(_farbe(farbe))
        zelle.setToolTip(erklaerung)
        self.tabelle.setItem(reihe, 1, zelle)

    # ------------------------------------------------------------------
    # Bedienung
    # ------------------------------------------------------------------
    def _auf_anhalten(self, angehalten: bool) -> None:
        """Friert die Anzeige ein oder setzt sie fort."""
        self.anhalten_button.setText("Anzeige fortsetzen" if angehalten else "Anzeige anhalten")
        self._aktualisieren()

    def _auf_leeren(self, *_egal) -> None:
        """Leert die Anzeige, ohne am Mitschnitt etwas zu aendern."""
        mitschnitt = self._mitschnitt()
        if mitschnitt is not None:
            self._letzte_nummer = mitschnitt.gesamt
        self.tabelle.setRowCount(0)
        self.text.clear()
        self._text_zeilen.clear()

    def _auf_kopieren(self, *_egal) -> None:
        """Legt die angezeigten Zeilen als Text in die Zwischenablage."""
        QtWidgets.QApplication.clipboard().setText("\n".join(self._text_zeilen))


class KalibrierungDialog(QtWidgets.QDialog):
    """Gefuehrte Kalibrierung: jeder Befehl in jeder Reihenfolge.

    Drei Seiten:

    1. **Vorbereitung** -- was passiert, der Ablauf als Kaestchenreihe, die
       Einstellungen und was vorher zu pruefen ist.
    2. **Ablauf** -- eine grosse Karte in der Farbe des aktuellen Befehls.
       Sie unterscheidet sichtbar zwischen *Wechseln* (nicht aufgezeichnet,
       mit Countdown) und *Aufnahme* (mit Fortschrittsbalken). Die Anweisung
       ist so gross, dass sie aus einem Meter Abstand lesbar ist; jeder
       Wechsel wird zusaetzlich angesagt.
    3. **Ergebnis** -- was je Befehl aufgenommen wurde, und das Angebot,
       das Modell gleich neu zu trainieren.

    Bricht man ab, fragt der Dialog, ob die schon aufgenommenen Bloecke
    bleiben sollen. Dafuer merkt er sich vor dem Start den Zeilenstand der
    Trainingsdatei.

    Attributes:
        neu_trainieren: True, wenn am Ende "Modell jetzt neu trainieren"
            gewaehlt wurde. Das Training selbst startet das Hauptfenster.
    """

    _AKZENT = "#2f6fdf"

    def __init__(self, session, labels, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut den Dialog.

        Args:
            session: ``TrainingSession`` des aktiven Profils.
            labels: Die zu kalibrierenden Befehle.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Kalibrierung")
        self.resize(700, 700)

        self._session = session
        self._labels = tuple(labels)
        self._worker: KalibrierungsWorker | None = None
        self._ansage: subprocess.Popen | None = None
        self._startstand = 0
        self._block = 0
        self._plan_laenge = 0
        self._aufnahme_laeuft = False
        self.neu_trainieren = False

        # Eigene Anweisungen des Profils, sonst die Vorgaben.
        self._profil_ordner = self._session.csv_path.parent
        self._anweisungen = kalibrierung_anweisungen_laden(
            self._profil_ordner, _KALIBRIERUNG_ANWEISUNG
        )
        self._anweisung_felder: dict[str, QtWidgets.QLineEdit] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        self.seiten = QtWidgets.QStackedWidget()
        layout.addWidget(self.seiten)

        self.seiten.addWidget(self._seite_vorbereitung())
        self.seiten.addWidget(self._seite_ablauf())
        self.seiten.addWidget(self._seite_ergebnis())
        self._plan_anzeigen()

    # ------------------------------------------------------------------
    # Hilfen fuer Aussehen
    # ------------------------------------------------------------------
    @staticmethod
    def _dunkel() -> bool:
        app = QtWidgets.QApplication.instance()
        return app is not None and app.palette().color(QtGui.QPalette.Base).lightness() < 128

    def _farben(self) -> dict[str, str]:
        return _BEFEHL_FARBEN["dunkel" if self._dunkel() else "hell"]

    def _anweisung(self, befehl: str) -> str:
        """Die Anweisung fuer einen Befehl, so wie sie gerade eingetragen ist."""
        feld = self._anweisung_felder.get(befehl)
        if feld is not None and feld.text().strip():
            return feld.text().strip()
        return self._anweisungen.get(befehl, befehl)

    def _ansagetext(self, befehl: str) -> str:
        """Was angesagt wird: "Rückwärts. Kiefer zusammenbeißen." """
        return f"{_BEFEHL_NAME_DE.get(befehl, befehl)}. {self._anweisung(befehl)}."

    @staticmethod
    def _endton_da() -> bool:
        return shutil.which("afplay") is not None and Path(_KALIBRIERUNG_ENDTON).exists()

    def _kleiner_knopf(self, text: str, tipp: str) -> QtWidgets.QPushButton:
        knopf = QtWidgets.QPushButton(text)
        knopf.setToolTip(tipp)
        knopf.setAutoDefault(False)
        knopf.setFixedWidth(34)
        return knopf

    @staticmethod
    def _ueberschrift(text: str, groesse: int = 18) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        schrift = label.font()
        schrift.setPointSize(groesse)
        schrift.setBold(True)
        label.setFont(schrift)
        return label

    @staticmethod
    def _abschnitt(text: str) -> QtWidgets.QLabel:
        """Kleine Zwischenueberschrift in Grossbuchstaben, wie in der Seitenleiste."""
        label = QtWidgets.QLabel(text.upper())
        label.setStyleSheet(
            f"color: {_farbe('neben').name()}; font-size: 11px; font-weight: 600; "
            "letter-spacing: 1px; margin-top: 10px;"
        )
        return label

    @staticmethod
    def _nebeninfo(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {_farbe('neben').name()}; font-size: 12px;")
        return label

    def _hauptknopf(self, knopf: QtWidgets.QPushButton) -> None:
        """Hebt die eine Hauptaktion einer Seite hervor, wie in der Seitenleiste."""
        knopf.setDefault(True)
        knopf.setStyleSheet(
            f"QPushButton {{ background: {self._AKZENT}; color: #ffffff; "
            f"border: 1px solid {self._AKZENT}; border-radius: 6px; "
            "padding: 6px 14px; font-weight: 600; }"
        )

    def _anweisungen_bereich(self) -> QtWidgets.QWidget:
        """Je Befehl: Farbe, Name und ein Feld fuer die eigene Anweisung.

        Die Anweisung ist das, was man tut, um den Befehl zu erzeugen. Sie
        steht waehrend des Ablaufs gross im Fenster und wird angesagt. Mit
        dem Knopf daneben hoert man die Ansage vorab -- auch, um die
        Lautstaerke zu pruefen.
        """
        feld = QtWidgets.QWidget()
        raster = QtWidgets.QGridLayout(feld)
        raster.setContentsMargins(0, 0, 0, 0)
        raster.setHorizontalSpacing(10)
        raster.setVerticalSpacing(6)
        raster.setColumnStretch(2, 1)
        farben = self._farben()

        for reihe, befehl in enumerate(self._labels):
            punkt = QtWidgets.QLabel(befehl[:1])
            punkt.setFixedSize(22, 22)
            punkt.setAlignment(QtCore.Qt.AlignCenter)
            punkt.setStyleSheet(
                f"background: {farben.get(befehl, '#888')}; color: {farben['_schrift']}; "
                "border-radius: 5px; font-weight: 700;"
            )
            raster.addWidget(punkt, reihe, 0)

            name = QtWidgets.QLabel(f"{_BEFEHL_NAME_DE.get(befehl, befehl)} "
                                    f"<span style='color:{_farbe('neben').name()}'>({befehl})</span>")
            name.setMinimumWidth(150)
            raster.addWidget(name, reihe, 1)

            eingabe = QtWidgets.QLineEdit(self._anweisungen.get(befehl, befehl))
            eingabe.setPlaceholderText(_KALIBRIERUNG_ANWEISUNG.get(befehl, befehl))
            eingabe.setMaxLength(40)
            self._anweisung_felder[befehl] = eingabe
            raster.addWidget(eingabe, reihe, 2)

            probe = self._kleiner_knopf("▶", "Ansage anhören")
            probe.clicked.connect(lambda _=False, b=befehl: self._ansagen(self._ansagetext(b), immer=True))
            probe.setEnabled(shutil.which("say") is not None)
            raster.addWidget(probe, reihe, 3)
        return feld

    # ------------------------------------------------------------------
    # Seite 1: Vorbereitung
    # ------------------------------------------------------------------
    def _seite_vorbereitung(self) -> QtWidgets.QWidget:
        seite = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(self._ueberschrift("Kalibrierung"))
        layout.addWidget(self._nebeninfo(
            f"Profil <b>{self._session.csv_path.parent.name}</b> · "
            f"{len(self._labels)} Befehle"
        ))

        einleitung = QtWidgets.QLabel(
            "Jeder Befehl wird in <b>jeder möglichen Reihenfolge</b> "
            "aufgenommen. So lernt das Modell die Befehle selbst — und nicht, "
            "was typischerweise davor kam."
        )
        einleitung.setWordWrap(True)
        layout.addWidget(einleitung)

        layout.addWidget(self._abschnitt("Befehle und Anweisungen"))
        layout.addWidget(self._nebeninfo(
            "Was du für den Befehl tust — so, wie es angesagt werden soll. "
            "Wird im Profil gespeichert."
        ))
        layout.addWidget(self._anweisungen_bereich())

        layout.addWidget(self._abschnitt("Ablauf"))
        self.plan_leiste_vorschau = PlanLeiste(self._plan(), self._farben())
        layout.addWidget(self.plan_leiste_vorschau)
        self.plan_label = QtWidgets.QLabel()
        layout.addWidget(self.plan_label)
        layout.addWidget(self._nebeninfo(
            "Je Block: Ansage → Wechselzeit → Aufnahme → Ton. "
            "Die Aufnahme beginnt erst, wenn die Ansage zu Ende ist."
        ))

        layout.addWidget(self._abschnitt("Einstellungen"))
        raster = QtWidgets.QGridLayout()
        raster.setHorizontalSpacing(12)
        raster.setVerticalSpacing(8)
        raster.setColumnStretch(2, 1)

        self.sekunden_feld = QtWidgets.QSpinBox()
        self.sekunden_feld.setRange(5, 60)
        self.sekunden_feld.setValue(_KALIBRIERUNG_SEKUNDEN)
        self.sekunden_feld.setSuffix(" s")
        self.sekunden_feld.setFixedWidth(80)
        self.sekunden_feld.valueChanged.connect(self._plan_anzeigen)
        raster.addWidget(QtWidgets.QLabel("Aufnahme je Block"), 0, 0)
        raster.addWidget(self.sekunden_feld, 0, 1)
        raster.addWidget(self._nebeninfo("Länger heißt mehr Material, aber auch anstrengender."), 0, 2)

        self.uebergang_feld = QtWidgets.QSpinBox()
        self.uebergang_feld.setRange(0, 10)
        self.uebergang_feld.setValue(_KALIBRIERUNG_UEBERGANG)
        self.uebergang_feld.setSuffix(" s")
        self.uebergang_feld.setFixedWidth(80)
        self.uebergang_feld.valueChanged.connect(self._plan_anzeigen)
        raster.addWidget(QtWidgets.QLabel("Wechselzeit davor"), 1, 0)
        raster.addWidget(self.uebergang_feld, 1, 1)
        raster.addWidget(self._nebeninfo(
            "Nach der Ansage, nicht aufgezeichnet — das EEG hängt einem Wechsel "
            "2–3 s hinterher."
        ), 1, 2)

        say_da = shutil.which("say") is not None
        self.ansage_box = QtWidgets.QCheckBox("Befehle ansagen")
        self.ansage_box.setChecked(say_da)
        self.ansage_box.setEnabled(say_da)
        self.ansage_box.toggled.connect(self._plan_anzeigen)
        raster.addWidget(self.ansage_box, 2, 0, 1, 2)
        raster.addWidget(self._nebeninfo(
            "Nötig bei geschlossenen Augen — dann ist der Bildschirm nicht zu sehen."
            if say_da else "Keine Sprachausgabe gefunden."
        ), 2, 2)

        ton_da = self._endton_da()
        self.endton_box = QtWidgets.QCheckBox("Ton am Ende jedes Blocks")
        self.endton_box.setChecked(ton_da)
        self.endton_box.setEnabled(ton_da)
        self.endton_box.toggled.connect(self._plan_anzeigen)
        raster.addWidget(self.endton_box, 3, 0, 1, 2)
        ton_zeile = QtWidgets.QHBoxLayout()
        ton_zeile.setSpacing(8)
        ton_probe = self._kleiner_knopf("▶", "Endton anhören")
        ton_probe.setEnabled(ton_da)
        ton_probe.clicked.connect(self._endton_probe)
        ton_zeile.addWidget(ton_probe)
        ton_zeile.addWidget(self._nebeninfo(
            "Zeigt mit geschlossenen Augen, dass der Block vorbei ist."
            if ton_da else "Kein Ton gefunden."
        ), 1)
        raster.addLayout(ton_zeile, 3, 2)
        layout.addLayout(raster)

        layout.addWidget(self._abschnitt("Vorher prüfen"))
        layout.addWidget(QtWidgets.QLabel(
            "• Headset sitzt, oben steht „Signal 100 %“<br>"
            "• Ton am Mac ist an und laut genug<br>"
            "• Sonst still sitzen — nur tun, was die Anweisung verlangt"
        ))

        layout.addStretch(1)

        knoepfe = QtWidgets.QDialogButtonBox()
        abbrechen = knoepfe.addButton("Abbrechen", QtWidgets.QDialogButtonBox.RejectRole)
        start = knoepfe.addButton("Kalibrierung starten", QtWidgets.QDialogButtonBox.AcceptRole)
        self._hauptknopf(start)
        abbrechen.setAutoDefault(False)
        knoepfe.accepted.connect(self._starten)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)
        return seite

    # ------------------------------------------------------------------
    # Seite 2: Ablauf
    # ------------------------------------------------------------------
    def _seite_ablauf(self) -> QtWidgets.QWidget:
        seite = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        kopf = QtWidgets.QHBoxLayout()
        self.block_label = self._ueberschrift("", 15)
        kopf.addWidget(self.block_label)
        kopf.addStretch(1)
        self.rest_label = self._nebeninfo("")
        self.rest_label.setWordWrap(False)
        kopf.addWidget(self.rest_label)
        layout.addLayout(kopf)

        self.plan_leiste = PlanLeiste(self._plan(), self._farben())
        layout.addWidget(self.plan_leiste)

        # Die Karte in der Farbe des Befehls: Rahmen kraeftig, Flaeche nur
        # leicht getoent -- die Schrift darauf bleibt in der normalen Farbe
        # und damit in jeder Darstellung lesbar.
        self.karte = QtWidgets.QFrame()
        self.karte.setObjectName("kalibrierkarte")
        karte_layout = QtWidgets.QVBoxLayout(self.karte)
        karte_layout.setContentsMargins(24, 20, 24, 22)
        karte_layout.setSpacing(6)

        self.phase_label = QtWidgets.QLabel()
        self.phase_label.setAlignment(QtCore.Qt.AlignCenter)
        karte_layout.addWidget(self.phase_label, 0, QtCore.Qt.AlignHCenter)

        karte_layout.addStretch(1)
        self.anweisung_label = self._ueberschrift("", 40)
        self.anweisung_label.setAlignment(QtCore.Qt.AlignCenter)
        # Eigene Anweisungen koennen laenger sein als "Augen zu".
        self.anweisung_label.setWordWrap(True)
        karte_layout.addWidget(self.anweisung_label)

        self.befehl_label = QtWidgets.QLabel()
        self.befehl_label.setAlignment(QtCore.Qt.AlignCenter)
        karte_layout.addWidget(self.befehl_label)
        karte_layout.addStretch(1)

        self.zustand_label = QtWidgets.QLabel()
        schrift = self.zustand_label.font()
        schrift.setPointSize(16)
        self.zustand_label.setFont(schrift)
        self.zustand_label.setAlignment(QtCore.Qt.AlignCenter)
        karte_layout.addWidget(self.zustand_label)

        self.block_balken = QtWidgets.QProgressBar()
        self.block_balken.setTextVisible(False)
        self.block_balken.setFixedHeight(8)
        karte_layout.addWidget(self.block_balken)

        layout.addWidget(self.karte, 1)

        unten = QtWidgets.QHBoxLayout()
        self.danach_label = QtWidgets.QLabel()
        unten.addWidget(self.danach_label)
        unten.addStretch(1)
        self.abbrechen_button = QtWidgets.QPushButton("Abbrechen")
        self.abbrechen_button.setAutoDefault(False)
        self.abbrechen_button.clicked.connect(self._abbrechen)
        unten.addWidget(self.abbrechen_button)
        layout.addLayout(unten)
        return seite

    def _karte_faerben(self, befehl: str, art: str) -> None:
        """Toent die Karte in der Farbe des Befehls.

        Gestrichelt und blass waehrend Ansage und Wechselzeit, durchgezogen
        und kraeftig waehrend der Aufnahme -- so sieht man auch aus dem
        Augenwinkel, ob gerade aufgezeichnet wird.
        """
        aufnahme = art == "aufnahme"
        farbe = QtGui.QColor(self._farben().get(befehl, "#888888"))
        flaeche = QtGui.QColor(farbe)
        flaeche.setAlpha(60 if aufnahme else 22)
        rand = "solid" if aufnahme else "dashed"
        self.karte.setStyleSheet(
            f"QFrame#kalibrierkarte {{ background: rgba({flaeche.red()}, {flaeche.green()}, "
            f"{flaeche.blue()}, {flaeche.alpha()}); border: 3px {rand} {farbe.name()}; "
            "border-radius: 14px; }"
            "QFrame#kalibrierkarte QLabel { background: transparent; border: none; }"
        )
        self.block_balken.setStyleSheet(
            "QProgressBar { border: none; border-radius: 4px; "
            f"background: rgba({farbe.red()}, {farbe.green()}, {farbe.blue()}, 50); }}"
            f"QProgressBar::chunk {{ background: {farbe.name()}; border-radius: 4px; }}"
        )
        schrift = self._farben()["_schrift"]
        marke = {"ansage": "ANSAGE", "uebergang": "WECHSELN",
                 "aufnahme": "AUFNAHME", "ende": "FERTIG"}.get(art, art.upper())
        self.phase_label.setText(marke)
        self.phase_label.setStyleSheet(
            f"background: {farbe.name() if aufnahme else 'transparent'}; "
            f"color: {schrift if aufnahme else farbe.name()}; "
            f"border: 2px solid {farbe.name()}; border-radius: 10px; "
            "padding: 2px 12px; font-weight: 700; font-size: 12px; letter-spacing: 1px;"
        )

    # ------------------------------------------------------------------
    # Seite 3: Ergebnis
    # ------------------------------------------------------------------
    def _seite_ergebnis(self) -> QtWidgets.QWidget:
        seite = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.ergebnis_titel = self._ueberschrift("")
        layout.addWidget(self.ergebnis_titel)
        self.ergebnis_label = QtWidgets.QLabel()
        self.ergebnis_label.setWordWrap(True)
        layout.addWidget(self.ergebnis_label)

        self.ergebnis_tabelle = QtWidgets.QTableWidget(0, 3)
        self.ergebnis_tabelle.setHorizontalHeaderLabels(["Befehl", "Blöcke", "Samples"])
        self.ergebnis_tabelle.verticalHeader().setVisible(False)
        self.ergebnis_tabelle.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.ergebnis_tabelle.setSelectionMode(QtWidgets.QTableWidget.NoSelection)
        self.ergebnis_tabelle.setFocusPolicy(QtCore.Qt.NoFocus)
        self.ergebnis_tabelle.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.ergebnis_tabelle)

        self.ergebnis_hinweis = self._nebeninfo("")
        layout.addWidget(self.ergebnis_hinweis)
        layout.addStretch(1)

        knoepfe = QtWidgets.QDialogButtonBox()
        schliessen = knoepfe.addButton("Schließen", QtWidgets.QDialogButtonBox.RejectRole)
        schliessen.setAutoDefault(False)
        self.trainieren_button = knoepfe.addButton(
            "Modell jetzt neu trainieren", QtWidgets.QDialogButtonBox.AcceptRole
        )
        self._hauptknopf(self.trainieren_button)
        knoepfe.accepted.connect(self._trainieren)
        knoepfe.rejected.connect(self.reject)
        layout.addWidget(knoepfe)
        return seite

    def _ergebnis_zeigen(self, titel: str, text: str, hinweis: str, tabelle: bool) -> None:
        """Fuellt die Ergebnisseite; die Tabelle zaehlt nach, was wirklich in der Datei steht."""
        self.ergebnis_titel.setText(titel)
        self.ergebnis_label.setText(text)
        self.ergebnis_hinweis.setText(hinweis)
        self.ergebnis_tabelle.setVisible(tabelle)

        if tabelle:
            _, aufnahmen = aufnahmen_lesen(self._session.csv_path)
            neue = [a for a in aufnahmen if a.von >= self._startstand]
            farben = self._farben()
            self.ergebnis_tabelle.setRowCount(len(self._labels))
            for reihe, befehl in enumerate(self._labels):
                eigene = [a for a in neue if a.label == befehl]
                punkt = QtWidgets.QTableWidgetItem(f"{_BEFEHL_NAME_DE.get(befehl, befehl)} — {self._anweisung(befehl)}")
                punkt.setIcon(self._farbsymbol(farben.get(befehl, "#888")))
                self.ergebnis_tabelle.setItem(reihe, 0, punkt)
                self.ergebnis_tabelle.setItem(reihe, 1, QtWidgets.QTableWidgetItem(str(len(eigene))))
                self.ergebnis_tabelle.setItem(
                    reihe, 2, QtWidgets.QTableWidgetItem(str(sum(len(a.zeilen) for a in eigene)))
                )
            self.ergebnis_tabelle.resizeColumnsToContents()
            hoehe = self.ergebnis_tabelle.horizontalHeader().height() + 4
            for reihe in range(self.ergebnis_tabelle.rowCount()):
                hoehe += self.ergebnis_tabelle.rowHeight(reihe)
            self.ergebnis_tabelle.setFixedHeight(hoehe)

        self.seiten.setCurrentIndex(2)

    @staticmethod
    def _farbsymbol(farbe: str) -> QtGui.QIcon:
        bild = QtGui.QPixmap(12, 12)
        bild.fill(QtCore.Qt.transparent)
        maler = QtGui.QPainter(bild)
        maler.setRenderHint(QtGui.QPainter.Antialiasing)
        maler.setBrush(QtGui.QColor(farbe))
        maler.setPen(QtCore.Qt.NoPen)
        maler.drawRoundedRect(0, 0, 12, 12, 3, 3)
        maler.end()
        return QtGui.QIcon(bild)

    # ------------------------------------------------------------------
    # Ablauf
    # ------------------------------------------------------------------
    def _plan(self) -> list[str]:
        return kalibrierungsplan(self._labels)

    def _je_block(self) -> float:
        """Ungefaehre Dauer eines Blocks samt Ansage und Endton."""
        dauer = float(self.sekunden_feld.value() + self.uebergang_feld.value())
        if self.ansage_box.isChecked():
            dauer += 2.0      # eine Ansage dauert etwa 1,5-2,5 s
        if self.endton_box.isChecked():
            dauer += 1.7      # Laenge von Glass.aiff
        return dauer

    @staticmethod
    def _dauer_text(sekunden: float) -> str:
        minuten, rest = divmod(int(round(sekunden)), 60)
        return f"{minuten}:{rest:02d} min"

    def _plan_anzeigen(self, *_egal) -> None:
        """Zeigt Bloecke und Gesamtdauer der aktuellen Einstellungen."""
        plan = self._plan()
        je_befehl = len(plan) // max(len(self._labels), 1)
        self.plan_label.setText(
            f"<b>{len(plan)} Blöcke</b> · je Befehl {je_befehl} × "
            f"{self.sekunden_feld.value()} s · Dauer etwa "
            f"<b>{self._dauer_text(len(plan) * self._je_block())}</b>"
        )

    def _ansagen(self, text: str, immer: bool = False) -> None:
        """Spricht einen Text ueber die macOS-Sprachausgabe, ohne zu warten.

        Fuer Probe und Schlussmeldung. Die Ansagen im Ablauf spricht der
        Worker selbst, weil er auf ihr Ende warten muss.

        Args:
            text: Was gesprochen wird.
            immer: Auch dann sprechen, wenn die Ansage abgeschaltet ist
                (Probe-Knopf).
        """
        if not (immer or self.ansage_box.isChecked()):
            return
        if self._ansage is not None and self._ansage.poll() is None:
            self._ansage.terminate()
        try:
            self._ansage = subprocess.Popen(
                ["say", "-v", _KALIBRIERUNG_STIMME, text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            QtWidgets.QApplication.beep()

    def _endton_probe(self, *_egal) -> None:
        """Spielt den Endton einmal vor."""
        try:
            subprocess.Popen(["afplay", _KALIBRIERUNG_ENDTON],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            QtWidgets.QApplication.beep()

    def _starten(self) -> None:
        """Merkt sich den Zeilenstand und startet den Ablauf."""
        self._startstand = self._session.zeilen_zaehlen()
        plan = self._plan()
        self._plan_laenge = len(plan)
        self.plan_leiste.set_plan(plan)
        self.plan_leiste.set_farben(self._farben())
        self.block_balken.setRange(0, self.sekunden_feld.value())
        self.seiten.setCurrentIndex(1)

        # Eigene Anweisungen fuers naechste Mal im Profil merken.
        self._anweisungen = {befehl: self._anweisung(befehl) for befehl in self._labels}
        kalibrierung_anweisungen_speichern(self._profil_ordner, self._anweisungen)
        for feld in self._anweisung_felder.values():
            feld.setEnabled(False)

        self._worker = KalibrierungsWorker(
            self._session,
            plan,
            float(self.sekunden_feld.value()),
            float(self.uebergang_feld.value()),
            ansagen=(
                {befehl: self._ansagetext(befehl) for befehl in self._labels}
                if self.ansage_box.isChecked() else None
            ),
            endton=_KALIBRIERUNG_ENDTON if self.endton_box.isChecked() else None,
            stimme=_KALIBRIERUNG_STIMME,
        )
        self._worker.phase.connect(self._auf_phase)
        self._worker.uebergang_rest.connect(self._auf_uebergang_rest)
        self._worker.progress.connect(self._auf_fortschritt)
        self._worker.fertig.connect(self._auf_fertig)
        self._worker.abgebrochen.connect(self._auf_abgebrochen)
        self._worker.failed.connect(self._auf_fehler)
        self._worker.start()
        logger.info(
            "Kalibrierung gestartet: %d Bloecke a %d s, Wechselzeit %d s, Profil %s.",
            len(plan), self.sekunden_feld.value(), self.uebergang_feld.value(),
            self._session.csv_path.parent.name,
        )

    def _restzeit_zeigen(self, im_block: float) -> None:
        """Schaetzt die verbleibende Gesamtzeit."""
        offen = (self._plan_laenge - self._block) * self._je_block() + im_block
        self.rest_label.setText(f"noch etwa {self._dauer_text(offen)}")

    def _auf_phase(self, art: str, block: int, gesamt: int, label: str, naechstes: str) -> None:
        """Stellt Karte und Texte auf den neuen Abschnitt eines Blocks um."""
        self._block = block
        self._aufnahme_laeuft = art == "aufnahme"
        self.block_label.setText(f"Block {block} von {gesamt}")
        self.anweisung_label.setText(self._anweisung(label))
        self.befehl_label.setText(f"{_BEFEHL_NAME_DE.get(label, label)} ({label})")
        self._karte_faerben(label, art)

        if naechstes:
            self.danach_label.setText(
                f"Danach: <b>{self._anweisung(naechstes)}</b> ({naechstes})"
            )
        else:
            self.danach_label.setText("Danach: fertig")

        if art == "ende":
            # Block ist geschafft: Kaestchen fuellen, waehrend der Ton laeuft.
            self.plan_leiste.set_stand(-1, block)
            self.zustand_label.setText("Block vorbei")
            self.block_balken.setValue(self.block_balken.maximum())
            self._restzeit_zeigen(0)
            return

        self.plan_leiste.set_stand(block - 1, block - 1)
        if art == "ansage":
            self.zustand_label.setText("Ansage …")
            self.block_balken.setValue(0)
            self._restzeit_zeigen(self._je_block())
        elif art == "uebergang":
            self.zustand_label.setText("Gleich geht es los …")
            self.block_balken.setValue(0)
        else:
            self.zustand_label.setText(f"0 von {self.sekunden_feld.value()} s")
            self._restzeit_zeigen(self.sekunden_feld.value())

    def _auf_uebergang_rest(self, rest: float) -> None:
        self.zustand_label.setText(f"Aufnahme beginnt in {max(1, int(rest + 0.99))} s")

    def _auf_fortschritt(self, sekunden: float, samples: int) -> None:
        dauer = self.sekunden_feld.value()
        self.zustand_label.setText(f"{sekunden:.0f} von {dauer} s · {samples} Samples")
        self.block_balken.setValue(min(int(sekunden), dauer))
        self._restzeit_zeigen(max(0.0, dauer - sekunden))

    def _auf_fertig(self, bloecke: int, samples: int) -> None:
        self.plan_leiste.set_stand(-1, bloecke)
        self._ansagen("Kalibrierung beendet.")
        logger.info("Kalibrierung abgeschlossen: %d Bloecke, %d Samples.", bloecke, samples)
        self._ergebnis_zeigen(
            "Kalibrierung abgeschlossen",
            f"<b>{bloecke} Blöcke</b> mit zusammen <b>{samples} Samples</b> im Profil "
            f"<b>{self._session.csv_path.parent.name}</b> gespeichert.",
            "Jeder Block steht als eigene Aufnahme unter „Trainingsdaten ansehen“ "
            "und lässt sich dort ansehen, umbenennen oder löschen. Das Modell "
            "kennt die neuen Daten erst nach dem nächsten Training.",
            tabelle=True,
        )

    def _auf_abgebrochen(self, bloecke: int, samples: int) -> None:
        self._ansagen("Abgebrochen.")
        logger.info("Kalibrierung abgebrochen nach %d Bloecken, %d Samples.", bloecke, samples)
        behalten = False
        if samples:
            antwort = QtWidgets.QMessageBox.question(
                self,
                "Kalibrierung abgebrochen",
                f"Bis zum Abbruch wurden {bloecke} Blöcke mit {samples} Samples "
                "aufgenommen.\n\nSollen sie in den Trainingsdaten bleiben? "
                "Bei „Nein“ werden sie wieder entfernt.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            behalten = antwort == QtWidgets.QMessageBox.Yes
            if not behalten:
                self._session.auf_zeilenstand_kuerzen(self._startstand)

        self.trainieren_button.setVisible(behalten)
        if behalten:
            self._ergebnis_zeigen(
                "Kalibrierung abgebrochen",
                f"<b>{bloecke} Blöcke</b> mit {samples} Samples wurden behalten.",
                "Eine unvollständige Kalibrierung ist nicht mehr ausgewogen — "
                "manche Befehle haben mehr Blöcke als andere.",
                tabelle=True,
            )
        else:
            self._ergebnis_zeigen(
                "Kalibrierung abgebrochen",
                "Die Trainingsdaten sind unverändert.",
                "",
                tabelle=False,
            )

    def _auf_fehler(self, meldung: str) -> None:
        self.trainieren_button.setVisible(False)
        self._ergebnis_zeigen("Fehler bei der Kalibrierung", meldung, "", tabelle=False)

    def _abbrechen(self, *_egal) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.abbrechen_button.setEnabled(False)
            self.abbrechen_button.setText("Wird abgebrochen …")
            self._worker.abbrechen()

    def _trainieren(self) -> None:
        self.neu_trainieren = True
        self.accept()

    # ------------------------------------------------------------------
    # Schliessen waehrend des Ablaufs
    # ------------------------------------------------------------------
    def reject(self) -> None:  # noqa: D401 - Qt-Methode
        """Schliessen (Esc, Fensterkreuz) bricht einen laufenden Ablauf erst ab."""
        if self._worker is not None and self._worker.isRunning():
            self._abbrechen()
            return
        super().reject()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt-Konvention
        if self._worker is not None and self._worker.isRunning():
            self._abbrechen()
            event.ignore()
            return
        super().closeEvent(event)
