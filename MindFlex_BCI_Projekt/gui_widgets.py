"""Eigene Anzeigeelemente der Oberflaeche.

Zwei Widgets, die ihren Inhalt selbst zeichnen:

* :class:`BandMonitor` -- eine Kachel je EEG-Kanal mit Balken und Wert
* :class:`DirectionIndicator` -- der Richtungspfeil der Vorhersage

Beide kommen ohne Kenntnis des restlichen Programms aus: Sie bekommen
ihre Werte von aussen gesetzt und wissen nichts von Klassifikator,
Auto oder Messung.
"""

from __future__ import annotations

import math

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from gui_konstanten import (
    _BALKEN_GLAETTUNG,
    _CURVE_BESCHREIBUNG,
    _CURVE_KURZ,
    _CURVE_LABELS,
)


class BandMonitor(QtWidgets.QWidget):
    """Eine Kachel je Kanal: Name, Bedeutung, aktueller Wert, Balken.

    Nachbau der Monitore aus dem Processing Brain Grapher. Dort steht unter
    dem Verlaufsgraphen eine Reihe solcher Kacheln, eine je Kanal, jede mit
    einem Balken, der den aktuellen Wert im Verhaeltnis zum bisher
    gesehenen Bereich zeigt.

    Der Verlaufsgraph beantwortet "wie war es bisher", die Kachel "wo stehe
    ich gerade" -- letzteres liest sich auf einem Balken deutlich schneller
    ab als auf sieben uebereinanderliegenden Kurven.

    Die Skalierung hat zwei Modi:

    * einzeln -- jeder Balken nutzt die volle Hoehe fuer seinen eigenen
      bisher gesehenen Bereich. Gut, um Bewegung in einem einzelnen Band
      zu erkennen.
    * global -- alle Baender teilen sich dieselbe Skala. Gut, um die
      Baender untereinander zu vergleichen; die kleinen verschwinden dabei
      allerdings fast, weil Delta oft hundertmal groesser ist.

    Attributes:
        check: Ankreuzfeld, das die zugehoerige Kurve im Graphen ein- und
            ausblendet.
    """

    sichtbarkeit_geaendert = QtCore.pyqtSignal(str, bool)

    def __init__(self, name: str, farbe: str, parent: QtWidgets.QWidget | None = None) -> None:
        """Baut eine Kachel fuer einen Kanal.

        Args:
            name: Interner Kanalname, z. B. ``"alpha"``.
            farbe: Farbe des Balkens, passend zur Kurve im Graphen.
            parent: Optionales Eltern-Widget (Qt-Konvention).
        """
        super().__init__(parent)
        self._name = name
        self._farbe = QtGui.QColor(farbe)
        self._wert: float | None = None
        self._balken = 0.0          # aktuell gezeichnete Hoehe, 0 bis 1
        self._ziel = 0.0            # angestrebte Hoehe, 0 bis 1
        self._min: float | None = None
        self._max: float | None = None

        self._textfarbe = QtGui.QColor("#000000")
        self._nebenfarbe = QtGui.QColor("#707070")
        self._rahmenfarbe = QtGui.QColor("#c8c8c8")

        self.setMinimumSize(96, 112)
        self.setToolTip(
            f"{_CURVE_LABELS[name]}\n"
            f"{_CURVE_BESCHREIBUNG[name]}\n\n"
            "Haken entfernen blendet die Kurve im Graphen aus."
        )

        self.check = QtWidgets.QCheckBox("Graph")
        self.check.setChecked(True)
        self.check.toggled.connect(
            lambda sichtbar: self.sichtbarkeit_geaendert.emit(self._name, sichtbar)
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addStretch(1)
        layout.addWidget(self.check, alignment=QtCore.Qt.AlignHCenter)

    def setze_wert(self, wert: float, global_max: float | None = None) -> None:
        """Nimmt einen neuen Messwert entgegen und berechnet die Zielhoehe.

        Args:
            wert: Der neue Messwert.
            global_max: Groesster Wert ueber alle Kanaele. Ist er gesetzt,
                wird global skaliert, sonst auf den eigenen Bereich.
        """
        self._wert = wert
        self._min = wert if self._min is None else min(self._min, wert)
        self._max = wert if self._max is None else max(self._max, wert)

        if global_max is not None:
            self._ziel = 0.0 if global_max <= 0 else wert / global_max
        else:
            spanne = self._max - self._min
            # Solange nur ein einziger Wert vorliegt, ist die Spanne null.
            # Dann gibt es keinen sinnvollen Bezug, der Balken bleibt leer.
            self._ziel = 0.0 if spanne <= 0 else (wert - self._min) / spanne

        self._ziel = max(0.0, min(1.0, self._ziel))
        self.update()

    def tick(self) -> None:
        """Bewegt den Balken ein Stueck auf seinen Zielwert zu."""
        vorher = self._balken
        self._balken += (self._ziel - self._balken) * _BALKEN_GLAETTUNG
        if abs(self._balken - vorher) > 0.002:
            self.update()

    def kein_wert(self) -> None:
        """Leert die Anzeige, behaelt aber den bisher gesehenen Bereich.

        Wird benutzt, wenn keine Pakete mehr ankommen. Der alte Wert darf
        dann nicht stehen bleiben -- er sieht aus wie eine aktuelle
        Messung, ist aber nur der letzte vor dem Abriss.
        """
        if self._wert is None and self._ziel == 0.0:
            return
        self._wert = None
        self._ziel = 0.0
        self.update()

    def zuruecksetzen(self) -> None:
        """Vergisst den gesehenen Wertebereich, etwa beim Profilwechsel."""
        self._wert = None
        self._min = None
        self._max = None
        self._ziel = 0.0
        self._balken = 0.0
        self.update()

    def setze_theme(self, theme: dict) -> None:
        """Uebernimmt die Farben der aktuellen hellen/dunklen Darstellung."""
        self._textfarbe = QtGui.QColor(theme["text"])
        self._nebenfarbe = QtGui.QColor(theme["text"])
        self._nebenfarbe.setAlpha(150)
        self._rahmenfarbe = QtGui.QColor(theme["text"])
        self._rahmenfarbe.setAlpha(60)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt-Konvention
        """Zeichnet Balken und Beschriftung."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)

        breite = self.width()
        hoehe = self.height()

        # Balken von unten. Halbtransparent, damit die Schrift darueber
        # lesbar bleibt, ohne sie in eine eigene Flaeche setzen zu muessen.
        balken_hoehe = int(hoehe * max(0.0, min(1.0, self._balken)))
        if balken_hoehe > 0:
            fuellung = QtGui.QColor(self._farbe)
            fuellung.setAlpha(80)
            painter.fillRect(0, hoehe - balken_hoehe, breite, balken_hoehe, fuellung)

            # Oberkante kraeftig, damit der Pegel klar ablesbar ist.
            painter.setPen(QtGui.QPen(self._farbe, 2))
            painter.drawLine(0, hoehe - balken_hoehe, breite, hoehe - balken_hoehe)

        # Trennlinie zur naechsten Kachel
        painter.setPen(QtGui.QPen(self._rahmenfarbe, 1))
        painter.drawLine(breite - 1, 0, breite - 1, hoehe)

        # Name
        schrift = painter.font()
        schrift.setBold(True)
        schrift.setPointSize(10)
        painter.setFont(schrift)
        painter.setPen(self._textfarbe)
        painter.drawText(
            QtCore.QRect(0, 6, breite, 16),
            QtCore.Qt.AlignHCenter,
            _CURVE_KURZ[self._name],
        )

        # Bedeutung
        schrift.setBold(False)
        schrift.setPointSize(8)
        painter.setFont(schrift)
        painter.setPen(self._nebenfarbe)
        painter.drawText(
            QtCore.QRect(2, 23, breite - 4, 14),
            QtCore.Qt.AlignHCenter,
            _CURVE_BESCHREIBUNG[self._name],
        )

        # Aktueller Wert. Grosse Zahlen werden gekuerzt, sonst passen die
        # Bandleistungen (bis in die Millionen) nicht in die Kachel.
        schrift.setBold(True)
        schrift.setPointSize(11)
        painter.setFont(schrift)
        painter.setPen(self._textfarbe)
        painter.drawText(
            QtCore.QRect(0, 40, breite, 18),
            QtCore.Qt.AlignHCenter,
            self._wert_text(),
        )

        painter.end()

    def _wert_text(self) -> str:
        """Formatiert den aktuellen Wert kompakt.

        Returns:
            Der Wert als Text, ab tausend mit k bzw. M abgekuerzt.
        """
        if self._wert is None:
            return "--"
        wert = self._wert
        if wert >= 1_000_000:
            return f"{wert / 1_000_000:.1f} M"
        if wert >= 1_000:
            return f"{wert / 1_000:.0f} k"
        return f"{wert:.0f}"



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

    # Im Handbetrieb faehrt nicht der Klassifikator, sondern die Tastatur.
    # Eine eigene Farbe macht das auf einen Blick unterscheidbar -- sonst
    # sieht ein von Hand gefahrenes "vorwaerts" genauso aus wie ein vom
    # EEG erkanntes, und man haelt versehentlich das eine fuer das andere.
    _MANUAL_COLOR = QtGui.QColor("#3498db")

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """Initialisiert die Anzeige im neutralen "kein Signal"-Zustand.

        Args:
            parent: Optionales Eltern-Widget (Qt-Konvention).
        """
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self._label: str | None = None
        self._confidence: float = 0.0
        self._manuell = False

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

    def set_manuell(self, aktiv: bool) -> None:
        """Schaltet die Anzeige auf Handbetrieb um.

        Args:
            aktiv: True, wenn gerade ueber die Tastatur gefahren wird.
        """
        self._manuell = aktiv
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

            # Im Handbetrieb bekommt der Kreis einen kraeftigen blauen Rand.
            if self._manuell:
                painter.setPen(QtGui.QPen(self._MANUAL_COLOR, 5))
                painter.setBrush(QtGui.QColor("#eaf4fc"))
            else:
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

            if self._manuell:
                self._draw_manuell_hinweis(painter, radius)
        finally:
            painter.end()

    def _draw_manuell_hinweis(self, painter: QtGui.QPainter, radius: float) -> None:
        """Schreibt den Hinweis auf den Handbetrieb in die Anzeige.

        Args:
            painter: Aktiver ``QPainter``, auf die Mitte verschoben.
            radius: Radius des Hintergrund-Kreises.
        """
        text = "MANUELL" if self._label != "STOP" else "MANUELL · Taste halten"

        schrift = painter.font()
        schrift.setBold(True)
        schrift.setPointSize(9)
        painter.setFont(schrift)

        breite = radius * 2
        kasten = QtCore.QRectF(-breite / 2, radius * 0.52, breite, 18)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._MANUAL_COLOR)
        painter.drawRoundedRect(kasten, 9, 9)

        painter.setPen(QtGui.QColor("white"))
        painter.drawText(kasten, QtCore.Qt.AlignCenter, text)

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
        if self._manuell:
            # Von Hand gefahren gibt es keine Konfidenz -- der Befehl ist
            # gedrueckt oder eben nicht.
            color = self._MANUAL_COLOR
        else:
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


def zahl_deutsch(wert: float, nachkomma: int = 0) -> str:
    """Formatiert eine Zahl mit Punkt als Tausender- und Komma als Dezimalzeichen.

    Args:
        wert: Der zu formatierende Wert.
        nachkomma: Anzahl der Nachkommastellen.

    Returns:
        Zum Beispiel ``"1.500.000"`` oder ``"12,5"``.
    """
    text = f"{wert:,.{nachkomma}f}"
    # Erst die englischen Trennzeichen gegeneinander tauschen, sonst
    # ueberschreibt der zweite Austausch das Ergebnis des ersten.
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


class PlanLeiste(QtWidgets.QWidget):
    """Der Ablauf einer Kalibrierung als Reihe kleiner Kaestchen.

    Ein Kaestchen je Block, in der Farbe des Befehls und mit dessen
    Anfangsbuchstaben. Erledigte Bloecke sind gefuellt, der laufende
    gefuellt und umrandet, kommende nur umrandet. So sieht man auf einen
    Blick, wo man steht und was noch kommt -- besser als eine Prozentzahl,
    die bei "Block 4 von 18" schon 16 % zeigte, obwohl Block 4 gerade erst
    lief.
    """

    _KANTE = 24
    _ABSTAND = 5

    def __init__(self, plan: list[str], farben: dict[str, str],
                 parent: QtWidgets.QWidget | None = None) -> None:
        """Legt die Leiste an.

        Args:
            plan: Befehle in Aufnahmereihenfolge.
            farben: Befehl -> Farbe, dazu ``"_schrift"`` fuer die Buchstaben
                auf gefuellten Kaestchen.
            parent: Optionales Eltern-Widget.
        """
        super().__init__(parent)
        self._plan = list(plan)
        self._farben = farben
        self._aktuell = -1
        self._erledigt = 0
        self.setMinimumHeight(self._KANTE + 4)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_plan(self, plan: list[str]) -> None:
        """Tauscht den Plan aus, etwa wenn sich die Einstellungen aendern."""
        self._plan = list(plan)
        self.update()

    def set_farben(self, farben: dict[str, str]) -> None:
        """Tauscht die Farben aus, etwa nach einem Wechsel hell/dunkel."""
        self._farben = farben
        self.update()

    def set_stand(self, aktuell: int, erledigt: int) -> None:
        """Setzt den laufenden Block (0-basiert, -1 fuer keinen) und die Zahl erledigter."""
        self._aktuell = aktuell
        self._erledigt = erledigt
        self.update()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 - Qt-Konvention
        breite = len(self._plan) * (self._KANTE + self._ABSTAND)
        return QtCore.QSize(breite, self._KANTE + 4)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt-Konvention
        if not self._plan:
            return
        maler = QtGui.QPainter(self)
        maler.setRenderHint(QtGui.QPainter.Antialiasing)

        # Kaestchen schrumpfen, wenn das Fenster schmal ist, statt abgeschnitten
        # zu werden.
        platz = self.width() / len(self._plan)
        kante = min(self._KANTE, platz - 3)
        abstand = platz - kante
        schrift = maler.font()
        schrift.setBold(True)
        schrift.setPointSizeF(max(7.0, kante * 0.42))
        maler.setFont(schrift)

        for index, befehl in enumerate(self._plan):
            farbe = QtGui.QColor(self._farben.get(befehl, "#888888"))
            rechteck = QtCore.QRectF(index * platz + abstand / 2, 2, kante, kante)
            gefuellt = index < self._erledigt or index == self._aktuell

            if gefuellt:
                maler.setBrush(farbe)
                maler.setPen(QtCore.Qt.NoPen)
            else:
                maler.setBrush(QtCore.Qt.NoBrush)
                maler.setPen(QtGui.QPen(farbe, 1.5))
            maler.drawRoundedRect(rechteck.adjusted(1, 1, -1, -1), 5, 5)

            if index == self._aktuell:
                ring = QtGui.QPen(self.palette().color(QtGui.QPalette.WindowText), 2)
                maler.setPen(ring)
                maler.setBrush(QtCore.Qt.NoBrush)
                maler.drawRoundedRect(rechteck.adjusted(-1, -1, 1, 1), 6, 6)

            maler.setPen(QtGui.QColor(self._farben["_schrift"]) if gefuellt else farbe)
            maler.drawText(rechteck, QtCore.Qt.AlignCenter, befehl[:1])

        maler.end()


class Hinweiszeile(QtWidgets.QLabel):
    """Beschriftung, die nur Platz belegt, solange sie etwas zu sagen hat.

    Fuer Fortschrittsmeldungen wie "Aufnahme: 12 s von 30 s". Ein leeres
    QLabel nimmt trotzdem eine Zeile Hoehe ein -- in der Seitenleiste stand
    unter "Training starten" deshalb staendig eine Luecke.
    """

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setVisible(bool(text))

    def setText(self, text: str) -> None:  # noqa: N802 - Qt-Konvention
        """Setzt den Text und blendet die Zeile je nach Inhalt ein oder aus."""
        super().setText(text)
        self.setVisible(bool(text))


class ZahlenAchse(pg.AxisItem):
    """Achse mit ausgeschriebenen Zahlen statt Exponentialschreibweise.

    pyqtgraph schreibt bei grossen Werten von sich aus einen Faktor wie
    ``1e+06`` an den Achsenrand und beschriftet die Striche mit kleinen
    Zahlen. Das spart Platz, ist aber schlecht abzulesen -- man muss im
    Kopf multiplizieren, um zu wissen, was dort steht.

    Diese Achse schreibt die Werte stattdessen aus: ``1.500.000`` statt
    ``1,5`` neben einem ``1e+06`` in der Ecke.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Ohne das rechnet pyqtgraph die Werte vorher herunter, und die
        # Beschriftung passt nicht mehr zu den tatsaechlichen Messwerten.
        self.enableAutoSIPrefix(False)

        # Ausgeschriebene Zahlen brauchen mehr Platz als "1,5". Ohne eine
        # breitere Achse laesst pyqtgraph die Beschriftungen weg, sobald
        # sie sich ueberlappen wuerden -- dann steht an der Achse nur noch
        # die Null. Die Breite gilt fuer Zahlen und Beschriftung zusammen;
        # "1.000.000" neben einem hochkant gesetzten Namen braucht diese
        # 112 Punkte, sonst laufen beide ineinander.
        if self.orientation in ("left", "right"):
            self.setWidth(112)

    def tickStrings(self, werte, scale, spacing):  # noqa: N802 - pyqtgraph-Konvention
        """Beschriftet die Achsenstriche.

        Args:
            werte: Die Werte an den Strichen.
            scale: Skalierungsfaktor von pyqtgraph (hier immer 1).
            spacing: Abstand zwischen zwei Strichen -- daraus ergibt sich,
                wie viele Nachkommastellen ueberhaupt sinnvoll sind.

        Returns:
            Liste der Beschriftungen.
        """
        # pyqtgraph ruft im Log-Modus die Basisklasse, damit sie an
        # logTickStrings weiterreicht. Diese Klasse ueberschreibt
        # tickStrings -- also muss die Weiche hier stehen, sonst landen
        # logarithmierte Werte in der linearen Beschriftung.
        if self.logMode:
            return self.logTickStrings(werte, scale, spacing)

        # Die Zwischenstriche kommen ohne Abstandsangabe. Ohne diesen Fall
        # scheitert der Vergleich unten an None.
        if spacing is None or spacing >= 1:
            nachkomma = 0
        else:
            # Bei einem Abstand von 0,25 braucht es zwei Nachkommastellen,
            # bei 0,5 eine. Mehr als drei sind nie sinnvoll.
            nachkomma = min(3, max(0, len(f"{spacing:.10f}".rstrip("0").split(".")[1])))

        return [zahl_deutsch(wert, nachkomma) for wert in werte]

    def logTickStrings(self, werte, scale, spacing):  # noqa: N802 - pyqtgraph-Konvention
        """Beschriftet die Striche einer logarithmischen Achse.

        pyqtgraph schreibt dort von sich aus Potenzen wie ``10³``. Gemeint
        ist aber 1.000 -- und genau das soll dort stehen, aus demselben
        Grund wie bei der linearen Achse.

        Beschriftet werden nur die Zehnerpotenzen sowie deren Zwei- und
        Fuenffaches. Die uebrigen Zwischenstriche bleiben leer: Zwischen
        10.000 und 100.000 liegen sieben weitere, und Werte wie 31.623
        liest niemand ab.

        Args:
            werte: Die Werte an den Strichen -- bereits logarithmiert.
            scale: Skalierungsfaktor von pyqtgraph (hier immer 1).
            spacing: Abstand zwischen zwei Strichen, ungenutzt.

        Returns:
            Liste der Beschriftungen, leere Zeichenkette fuer unbeschriftete
            Striche.
        """
        beschriftungen = []
        for wert in werte:
            echt = 10.0 ** float(wert)
            # Aus dem Logarithmus die Dekade und die Mantisse zurueckholen:
            # 4,3 heisst Dekade 4 und Mantisse 2 -- also 2 mal 10.000.
            dekade = math.floor(float(wert) + 1e-9)
            mantisse = echt / 10.0**dekade

            gerundet = any(abs(mantisse - m) < 0.02 for m in (1.0, 2.0, 5.0))
            if not gerundet:
                beschriftungen.append("")
                continue

            # Unterhalb von eins braucht es Nachkommastellen, sonst stuende
            # dort dreimal die Null. Darueber sind sie immer ueberfluessig.
            nachkomma = 0 if echt >= 1 else min(3, -dekade)
            beschriftungen.append(zahl_deutsch(echt, nachkomma))

        return beschriftungen
