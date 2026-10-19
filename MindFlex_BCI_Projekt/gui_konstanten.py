"""Farben, Beschriftungen und Kennzahlen der Oberflaeche.

Alle Werte, die das Aussehen und Verhalten der Anzeige bestimmen, stehen
an dieser einen Stelle -- damit man sie aendern kann, ohne sich durch den
Aufbau der Fenster zu arbeiten.

Benutzt von :mod:`gui_widgets`, :mod:`gui_dialoge` und :mod:`visualizer`.
"""

from __future__ import annotations

from PyQt5 import QtCore


_SIGNAL_GOOD_COLOR = "#2ecc71"
_SIGNAL_BAD_COLOR = "#f1c40f"
_SIGNAL_NONE_COLOR = "#e74c3c"

# Farben der Fahrsperre. Rot heisst hier ausdruecklich nicht "Fehler",
# sondern "Auto steht still" -- das ist beim Aufnehmen der gewuenschte
# Zustand.
_LOCK_ON_COLOR = "#c0392b"
_LOCK_OFF_COLOR = "#27ae60"

# Blau fuer den Handbetrieb -- deutlich unterscheidbar von den Farben der
# Fahrsperre, damit man die beiden Zustaende nicht verwechselt.
_MANUELL_COLOR = "#2980b9"

_CURVE_COLORS = {
    "attention": "#3498db",
    "meditation": "#9b59b6",
    "delta": "#e67e22",
    "theta": "#e74c3c",
    "alpha": "#1abc9c",
    "beta": "#2c3e50",
    "gamma": "#7f8c8d",
}

# Beschriftung der Kurven in Legende und Seitenleiste.
#
# Die fuenf Frequenzbaender bekommen ihren Frequenzbereich nach der
# ThinkGear-Spezifikation. Alpha, Beta und Gamma sind dabei jeweils die
# Summe aus dem tiefen und dem hohen Teilband, deshalb der durchgehende
# Bereich.
#
# Attention und Meditation sind eSense-Werte von 0 bis 100 -- sie sind
# keine Frequenzen und bekommen deshalb ihre Skala statt einer Hz-Angabe.
_CURVE_LABELS = {
    "attention": "Attention (0–100)",
    "meditation": "Meditation (0–100)",
    "delta": "Delta (0,5–2,75 Hz)",
    "theta": "Theta (3,5–6,75 Hz)",
    "alpha": "Alpha (7,5–11,75 Hz)",
    "beta": "Beta (13–29,75 Hz)",
    "gamma": "Gamma (31–49,75 Hz)",
}

# Kurzname fuer die Kachel-Ueberschrift, ohne den Frequenzbereich.
_CURVE_KURZ = {
    "attention": "Attention",
    "meditation": "Meditation",
    "delta": "Delta",
    "theta": "Theta",
    "alpha": "Alpha",
    "beta": "Beta",
    "gamma": "Gamma",
}

# Was der Kanal ueber den Zustand aussagt. Uebernommen aus dem Processing
# Brain Grapher von Eric Mika, der jedem Band eine Wachheitsstufe zuordnet
# ("Dreamless Sleep", "Drowsy", "Relaxed", "Alert").
#
# Der Nutzen ist die Lesbarkeit fuer Zuschauer: Mit "Theta" kann niemand
# etwas anfangen, mit "schlaefrig" schon. Es sind Merkhilfen, keine
# Diagnosen -- ein hoher Theta-Wert heisst nicht, dass jemand schlaeft.
_CURVE_BESCHREIBUNG = {
    "attention": "Konzentration",
    "meditation": "Entspannung",
    "delta": "Tiefschlaf",
    "theta": "schläfrig",
    "alpha": "entspannt",
    "beta": "wach",
    "gamma": "Reizverarbeitung",
}

# Tastenbelegung im Handbetrieb. WASD und die Pfeiltasten liegen parallel,
# damit man nicht umlernen muss -- WASD fuer die rechte Hand an der Maus,
# Pfeiltasten fuer alle anderen.
#
# Gefahren wird nur, solange die Taste gedrueckt bleibt: Beim Loslassen
# geht sofort STOPP raus. Das ist bei einem Fahrzeug die sichere Variante
# -- wer den Finger hebt oder das Fenster wechselt, bekommt ein stehendes
# Auto statt eines weiterfahrenden.
# Kalibrierung: Vorgaben fuer die Anweisung je Befehl -- wie man den Befehl
# erzeugt. Nur Vorgaben: Jede Person traegt im Kalibrierungsfenster ihre
# eigene Strategie ein (Moritz etwa "Kiefer zusammenbeissen" fuer
# Rueckwaerts), gespeichert je Profil in kalibrierung.json.
_KALIBRIERUNG_ANWEISUNG = {
    "STOP": "Augen zu",
    "FORWARD": "Augen auf",
    "BACKWARD": "Kopf schütteln",
    "LEFT": "Links",
    "RIGHT": "Rechts",
}

# Wie der Befehl in der Ansage heisst: "Rückwärts. Kiefer zusammenbeißen."
_BEFEHL_NAME_DE = {
    "STOP": "Stopp",
    "FORWARD": "Vorwärts",
    "BACKWARD": "Rückwärts",
    "LEFT": "Links",
    "RIGHT": "Rechts",
}

# Ton am Ende jedes Blocks. Mit geschlossenen Augen ist sonst nicht zu
# erkennen, wann ein Block vorbei ist. Hell und eindeutig, 1,7 s.
_KALIBRIERUNG_ENDTON = "/System/Library/Sounds/Glass.aiff"

# Farbe je Befehl in der Kalibrierung. Nur fuer die Befehle, nicht fuer
# Zustaende -- Rot/Gruen bleiben Fahrsperre und Signal vorbehalten. Jeder
# Block traegt zusaetzlich den Anfangsbuchstaben, die Farbe ist also nie das
# einzige Unterscheidungsmerkmal.
#
# Je Darstellung ein eigener Satz (nachgerechnet):
#   hell:   weisse Schrift darauf 6,0 / 5,2 / 4,6 : 1
#   dunkel: helle Toene, gegen den Fenstergrund 5,1 / 6,3 / 6,2 : 1,
#           dunkle Schrift darauf 6,0 / 7,5 / 7,4 : 1
_BEFEHL_FARBEN = {
    "hell": {
        "STOP": "#6a4fc2", "FORWARD": "#1f7a6e", "BACKWARD": "#b45f12",
        "LEFT": "#2f6fdf", "RIGHT": "#a8327a", "_schrift": "#ffffff",
    },
    "dunkel": {
        "STOP": "#a48ef0", "FORWARD": "#45c2ae", "BACKWARD": "#f09a4a",
        "LEFT": "#7fa8f5", "RIGHT": "#e58ac0", "_schrift": "#1e1f22",
    },
}

_KALIBRIERUNG_SEKUNDEN = 15
# Nicht aufgezeichnete Wechselzeit vor jedem Block. Drei Sekunden, weil
# Alpha nach dem Augenoeffnen im Blindtest 2-3 s nachlief.
_KALIBRIERUNG_UEBERGANG = 3
_KALIBRIERUNG_STIMME = "Anna"

_MANUELL_TASTEN = {
    QtCore.Qt.Key_W: "FORWARD",
    QtCore.Qt.Key_Up: "FORWARD",
    QtCore.Qt.Key_S: "BACKWARD",
    QtCore.Qt.Key_Down: "BACKWARD",
    QtCore.Qt.Key_A: "LEFT",
    QtCore.Qt.Key_Left: "LEFT",
    QtCore.Qt.Key_D: "RIGHT",
    QtCore.Qt.Key_Right: "RIGHT",
}

# Anteil, um den sich ein Balken je Bildschirmaktualisierung seinem
# Zielwert naehert. Ohne diese Glaettung springen die Balken bei jedem
# neuen Messwert, was unruhig aussieht und schlechter ablesbar ist.
# Wert aus dem Brain Grapher uebernommen.
_BALKEN_GLAETTUNG = 0.08

# Beschriftung der Y-Achse.
#
# "Bandleistung" statt "Amplitude": NeuroSky beschreibt die Werte im
# Protokolldokument als *magnitude* der acht Frequenzbaender, also als
# Leistungen, nicht als Amplituden.
#
# "arbitrary units" statt "µV": Dieselbe Quelle haelt ausdruecklich fest,
# dass die Werte einheitenlos sind -- "These values have no units and
# therefore are only meaningful compared to each other and to themselves"
# (MindSet Communications Protocol, Kapitel ThinkGear Data Values). Eine
# µV-Angabe waere schlicht falsch: 700.000 µV waeren 0,7 Volt am Kopf.
# Nur das Rohsignal liesse sich in Mikrovolt umrechnen, und das zeichnet
# dieses Projekt gar nicht auf.
#
# Auf derselben Achse liegen auch Attention und Meditation, die als
# eSense-Werte von 0 bis 100 keine Bandleistungen sind. Sie verschwinden
# im Massstab der Baender ohnehin; wer es ganz streng haben will, nennt
# die Achse "Messwert".
_Y_ACHSE_NAME = "Bandleistung"
_Y_ACHSE_EINHEIT = "arbitrary units"

# Luft oberhalb des groessten gemessenen Werts, bis zu der sich der
# Ausschnitt aufziehen laesst. 1,15 heisst: 15 Prozent Spielraum. Ohne
# Obergrenze koennte man so weit herauszoomen, dass die Kurven zu einer
# Linie am unteren Rand zusammenschrumpfen.
_Y_SPIELRAUM = 1.15

# Logarithmische Y-Achse.
#
# Die sieben Kanaele liegen groessenordnungsweise auseinander: Delta geht
# in die Millionen, Gamma bleibt im Tausenderbereich, Attention und
# Meditation gehen nie ueber 100. Auf einer linearen Achse bestimmt der
# groesste Kanal allein den Massstab -- sobald Delta einen Ausschlag hat,
# liegen alle anderen Kurven platt auf der Nulllinie und sind nicht mehr
# ablesbar. Genau das ist die Beobachtung "das Diagramm zeigt nicht alle
# Werte an".
#
# Auf einer logarithmischen Achse belegt jede Zehnerpotenz denselben
# Abstand. Damit sind alle sieben Kanaele gleichzeitig zu sehen, und die
# Achse traegt weiter echte Zahlen (1.000, 10.000, ...) statt Potenzen.
# In der Spektralanalyse ist das die uebliche Darstellung, weil
# Bandleistungen ueber Dekaden streuen.
#
# Umschaltbar bleibt es trotzdem: Lineare Darstellung zeigt die Verhaeltnisse
# unverfaelscht, wenn man nur ein paar Kanaele betrachtet.
_Y_LOG_STANDARD = True

# Kleinster Wert, der auf der logarithmischen Achse dargestellt wird.
# Der Logarithmus von null ist nicht definiert, und eine Null kommt vor:
# bei fehlendem Hautkontakt liefert das Headset Nullen, Attention und
# Meditation koennen ebenfalls null sein. Solche Werte werden auf eins
# gehoben -- auf der Achse ist das der unterste Strich, also sichtbar
# "am Boden", ohne dass die Kurve abreisst.
_LOG_BODEN = 1.0

# Hinweis auf die Skalierung, damit sie nicht stillschweigend wechselt. Ein
# logarithmischer Verlauf sieht flacher aus als ein linearer; ohne Hinweis
# waere das irrefuehrend.
#
# Der Hinweis steht im Titel und nicht an der Achse: Die Achsenbeschriftung
# steht hochkant in einer schmalen Spalte neben den Zahlen, und "Bandleistung
# (logarithmisch) (arbitrary units)" lief dort in die 1.000.000 hinein.
_PLOT_TITEL_LOG = " — logarithmische Skala"
_PLOT_TITEL_LINEAR = " — lineare Skala"

# Im Aufnahme-Graphen ist fuer den Zusatz an der Achse Platz, weil dort der
# Titel die Aufnahme selbst benennt.
_Y_ACHSE_ZUSATZ_LOG = " (logarithmisch)"

# Attention und Meditation sind keine Bandleistungen, sondern eSense-Werte:
# NeuroSkys eigene Kennzahlen auf einer festen Skala von 0 bis 100. Wie sie
# zustande kommen, legt der Hersteller nicht offen.
#
# Sie gehoeren deshalb aus zwei Gruenden nicht auf dieselbe Achse wie die
# Baender:
#
# 1. Andere Groessenordnung. Selbst logarithmisch liegen sie als schmales
#    Band ganz unten im Bild, waehrend die Baender daruber den Platz fuellen.
# 2. Andere Bedeutung. Eine gemeinsame Achse behauptet Vergleichbarkeit --
#    "Attention 60 ist weniger als Alpha 15.000" ist aber eine Aussage ohne
#    Inhalt, weil die Zahlen nichts miteinander zu tun haben.
#
# Zwei uebereinanderliegende Graphen mit gemeinsamer Zeitachse loesen beides:
# Jede Werteart bekommt ihren eigenen Massstab, und der zeitliche Zusammenhang
# bleibt trotzdem ablesbar. Eine zweite Y-Achse im selben Bild waere die
# schlechtere Loesung -- bei der haengt jeder abgelesene Zusammenhang davon
# ab, wie man die beiden Skalen zufaellig gegeneinander legt.
#
# Der Processing Brain Grapher trennt an derselben Stelle: Dort sind fuer
# Attention und Meditation die Grenzen fest auf 0 bis 100 gesetzt und von
# der gemeinsamen Skalierung ausgenommen (allowGlobal = false).
_ESENSE_KANAELE = ("attention", "meditation")

_ESENSE_ACHSE_NAME = "eSense"
_ESENSE_ACHSE_EINHEIT = "0-100"

# Feste Achsengrenzen statt automatischer Anpassung: Der Wertebereich ist
# vom Hersteller festgelegt, also ist er auch dann die richtige Achse, wenn
# gerade nur Werte zwischen 40 und 60 vorkommen. Andernfalls spraenge die
# Achse bei jedem Messwert und aus ruhigen Verlaeufen wuerden Zacken.
_ESENSE_MIN = 0.0
_ESENSE_MAX = 100.0

# Die Zeitachse zaehlt nur die Zeit, in der wirklich gemessen wurde. Pausen
# ruecken nicht mit ihrer echten Dauer ein, sondern als schmaler Balken --
# siehe _PAUSE_SCHRITT. Die Gesamtzeit seit Programmstart steht getrennt
# davon als "Laufzeit" oben in der Statuszeile.
_X_ACHSE_NAME = "Messungszeit"
_PLOT_TITEL = "EEG-Verlauf"

# Breite des sichtbaren Zeitfensters in Sekunden. Der Ausschnitt wandert
# mit den neuen Messwerten mit; aufgezeichnet wird trotzdem alles, man
# kann also nach links zurueckziehen.
_ZEITFENSTER = 60.0

# Nach so vielen Sekunden ohne neues Paket gilt die Verbindung als tot.
# Das Headset liefert nominell ein Paket je Sekunde; drei Sekunden lassen
# also Raum fuer einen Aussetzer, ohne gleich Alarm zu schlagen.
_VERBINDUNG_TIMEOUT = 3.0

# Ab dieser Luecke zwischen zwei Messwerten gilt die Messung als
# unterbrochen. Der Graph zeichnet dann keine Linie ueber die Luecke,
# sondern markiert sie -- sonst suggeriert eine durchgezogene Linie einen
# Verlauf, der nie gemessen wurde.
_PAUSE_AB_SEKUNDEN = 3.0

# Wie viel Platz eine Pause auf der Zeitachse bekommt, in Sekunden.
#
# Frueher rueckte eine Pause mit ihrer echten Dauer ein: Zehn Minuten Pause
# waren zehn Minuten leere Flaeche, und die eigentliche Messung schrumpfte
# daneben zusammen. Jetzt zaehlt die Achse nur gemessene Zeit, und eine
# Pause bekommt genau einen Messschritt -- so viel, wie zwischen zwei
# Paketen ohnehin liegt. Dadurch stimmt die Achse weiterhin mit der
# Messungszeit ueberein, statt je Pause ein Stueck davonzulaufen.
_PAUSE_SCHRITT = 1.0

# Breite des Pausenbalkens in Bildpunkten. Bewusst in Pixeln statt in
# Sekunden: So bleibt der Balken gleich schmal, egal wie weit man in der
# Zeit zurueckscrollt, und traegt trotzdem seine Beschriftung.
_PAUSE_BALKEN_PX = 22

_PAUSE_TEXT = "Messung pausiert"

# Wie nah am neuesten Wert der rechte Rand sein muss, damit die Anzeige
# wieder automatisch mitlaeuft, nachdem man von Hand gescrollt hat.
_LIVE_TOLERANZ = 2.0

# Optik der Kurven.
_LINIEN_BREITE = 3

# Kantenglaettung ist bewusst AUS. Sie sieht zwar etwas weicher aus,
# kostet bei breiten Linien aber das Zweihundertfache an Rechenzeit:
# gemessen bei 3600 Punkten (eine Stunde Aufnahme) rund 20 Sekunden pro
# Neuzeichnen gegenueber 95 Millisekunden ohne. Die Oberflaeche friert
# damit nach laengerer Laufzeit ein. Die runden Enden und Ecken der
# Linien bleiben erhalten, die kosten nichts.
_KANTENGLAETTUNG = False

# Farben fuer helle und dunkle Darstellung.
# Schriftfarben fuer Zustaende in Tabellen: zu wenig Material, genug
# Material, und Nebensaechliches (nicht trainiert, nicht aktiv, Testaufnahme).
#
# Frueher gab es dafuer je eine feste Farbe fuer beide Darstellungen. Das
# ging nicht auf: Das Gruen #27ae60 hatte auf weissem Grund nur 2,9 : 1
# Kontrast, das Rot #c0392b auf dunklem nur 3,0 : 1 -- beides unter der
# ueblichen Lesbarkeitsgrenze von 4,5 : 1. Jetzt gibt es je Darstellung
# einen eigenen Satz, jeder Wert auf Tabellengrund und Zeilenwechsel
# nachgerechnet:
#
#   hell   (#ffffff / #f0f0f0):  Rot 6,6 / 5,8   Gruen 6,5 / 5,7   Grau 6,0 / 5,3
#   dunkel (#1e1f22 / #2b2d30):  Rot 7,2 / 6,1   Gruen 9,7 / 8,1   Grau 6,8 / 5,7
_TABELLENFARBEN = {
    "hell": {"zu_wenig": "#b42318", "genug": "#146c2e", "neben": "#5f636b"},
    "dunkel": {"zu_wenig": "#ff8a80", "genug": "#6fdc8c", "neben": "#a3a7ae"},
}

# Farben je Darstellung.
#
# Die Eintraege ab "karte" gehoeren zur Seitenleiste: Jeder Bereich sitzt
# dort auf einer eigenen Flaeche, die sich leicht vom Fensterhintergrund
# abhebt. "gedaempft" ist fuer Ueberschriften und Nebeninformationen --
# auf der Kartenflaeche mit mindestens 4,5 : 1 Kontrast, also auch klein
# gesetzt noch lesbar. "akzent" markiert die eine Hauptaktion (Training
# starten); weisse Schrift darauf erreicht ebenfalls 4,5 : 1.
_THEMES = {
    "hell": {
        "hintergrund": "#ffffff",
        "vordergrund": "#000000",
        "fenster": "#f0f0f0",
        "text": "#000000",
        "feld": "#ffffff",
        "karte": "#ffffff",
        "rand": "#d6d7db",
        "gedaempft": "#5f636b",
        "knopf": "#f3f3f5",
        "knopf_hover": "#e7e8eb",
        "akzent": "#2f6fdf",
        "pause": (120, 120, 120, 70),
        "pause_text": "#3a3d42",
    },
    "dunkel": {
        "hintergrund": "#1e1f22",
        "vordergrund": "#d8d8d8",
        "fenster": "#2b2d30",
        "text": "#e0e0e0",
        "feld": "#1e1f22",
        "karte": "#323438",
        "rand": "#45484e",
        "gedaempft": "#a3a7ae",
        "knopf": "#3d4045",
        "knopf_hover": "#484b51",
        "akzent": "#2f6fdf",
        "pause": (150, 150, 150, 60),
        "pause_text": "#d8d8d8",
    },
}


