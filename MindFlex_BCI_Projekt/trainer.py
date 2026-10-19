"""
trainer.py
==========

Interaktiver Trainingsmodus: Der Benutzer waehlt einen Fahrbefehl
(FORWARD/BACKWARD/LEFT/RIGHT/STOP), denkt fuer eine festgelegte Dauer
an den entsprechenden mentalen Zustand, und alle in dieser Zeit
empfangenen (gueltigen) EEG-Rohwerte werden mit diesem Label versehen
in ``training_data.csv`` gespeichert.

Dieses Modul kuemmert sich NUR um das Aufzeichnen roher, gelabelter
Zeilen. Die Umwandlung in ML-Features (gleitende Fenster, Statistiken)
passiert erst in ``classifier.py`` beim Trainieren der Modelle -- so
bleibt die CSV-Datei einfach lesbar und unabhaengig von der gewaehlten
Fenstergroesse.

Kann sowohl interaktiv per Konsole (``run_cli``) als auch programmatisch
(z. B. von der GUI in ``visualizer.py`` ueber ``record_label``)
verwendet werden.
"""

from __future__ import annotations

import csv
import json
import threading
import time
from collections import Counter
from itertools import permutations
from pathlib import Path
from typing import Callable

from brain_processor import BrainProcessor
from config import CONFIG, TRAINING_CSV_COLUMNS
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)

# Menu-Zuordnung fuer den interaktiven CLI-Trainingsmodus. Angeboten werden
# nur die aktuell aktiven Fahrbefehle (``CONFIG.active_labels``). Die uebrigen
# aus ``CONFIG.labels`` bleiben als Reserve gueltig und lassen sich weiterhin
# ueber ``TrainingSession.record_label`` aufzeichnen -- oder wieder ins Menue
# holen, indem man ``MINDFLEX_ACTIVE_LABELS`` setzt.
_MENU: dict[str, str] = {
    str(index): label for index, label in enumerate(CONFIG.active_labels, start=1)
}


class TrainingSession:
    """Kapselt das Aufzeichnen von gelabelten Trainingsdaten.

    Attributes:
        processor: ``BrainProcessor``, aus dem aktuelle EEG-Samples
            gelesen werden.
        csv_path: Pfad zur Trainings-CSV-Datei.
    """

    def __init__(
        self,
        processor: BrainProcessor,
        csv_path: Path | None = None,
    ) -> None:
        """Initialisiert die Trainingssession.

        Args:
            processor: Bereits laufender ``BrainProcessor``, der die
                serielle Verbindung im Hintergrund konsumiert.
            csv_path: Zielpfad fuer die CSV-Datei. Default aus
                ``CONFIG.paths.training_csv``.
        """
        self.processor = processor
        self.csv_path = csv_path if csv_path is not None else CONFIG.paths.training_csv

        # Label und Zeilenzahl der zuletzt aufgezeichneten Aufnahme, damit
        # sie sich rueckgaengig machen laesst.
        self._letzte_aufnahme: tuple[str, int] | None = None

        self._ensure_csv_header()

    def wechsle_profil(self, csv_path: Path) -> None:
        """Stellt die Session auf die Trainingsdatei eines anderen Profils um.

        Args:
            csv_path: Zielpfad der neuen Trainingsdatei.
        """
        self.csv_path = csv_path
        self._letzte_aufnahme: tuple[str, int] | None = None
        self._ensure_csv_header()
        logger.info("Trainingsdatei gewechselt: %s", self.csv_path)

    def _ensure_csv_header(self) -> None:
        """Legt Ordner und CSV-Datei mit Header an, falls noch nicht vorhanden."""
        # Profilordner koennen beim ersten Gebrauch noch fehlen.
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(TRAINING_CSV_COLUMNS)
            logger.info("Neue Trainingsdatei angelegt: %s", self.csv_path)

    def zeilen_zaehlen(self) -> int:
        """Zaehlt die Datenzeilen der aktuellen Trainingsdatei (ohne Kopfzeile).

        Returns:
            Anzahl aufgezeichneter Samples.
        """
        if not self.csv_path.exists():
            return 0
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            return max(0, sum(1 for zeile in csv.reader(f) if zeile) - 1)

    @safe_execute(logger, default=0)
    def letzte_aufnahme_loeschen(self) -> int:
        """Entfernt die zuletzt aufgezeichnete Aufnahme wieder aus der Datei.

        Gedacht fuer den Fall, dass waehrend der Aufnahme etwas
        dazwischenkam -- Headset verrutscht, abgelenkt, falscher Befehl
        gewaehlt. Rueckgaengig gemacht werden kann immer nur die letzte
        Aufnahme, und nur solange das Programm laeuft.

        Returns:
            Anzahl der entfernten Zeilen. 0, wenn es nichts zu loeschen
            gab.
        """
        if not self._letzte_aufnahme:
            logger.info("Keine Aufnahme zum Loeschen vorgemerkt.")
            return 0

        label, anzahl = self._letzte_aufnahme
        if anzahl <= 0:
            self._letzte_aufnahme = None
            return 0

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            zeilen = [zeile for zeile in csv.reader(f) if zeile]

        kopf, daten = zeilen[0], zeilen[1:]
        if anzahl > len(daten):
            logger.warning(
                "Sollte %d Zeilen loeschen, es gibt aber nur %d -- loesche alle.",
                anzahl,
                len(daten),
            )
            anzahl = len(daten)

        behalten = daten[: len(daten) - anzahl]

        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(kopf)
            writer.writerows(behalten)

        logger.info(
            "Letzte Aufnahme geloescht: %d Zeilen fuer '%s'. Verbleibend: %d.",
            anzahl,
            label,
            len(behalten),
        )
        self._letzte_aufnahme = None
        return anzahl

    @safe_execute(logger, default=0)
    def auf_zeilenstand_kuerzen(self, stand: int) -> int:
        """Entfernt alle Zeilen, die nach einem frueheren Zeilenstand dazukamen.

        Fuer eine abgebrochene Kalibrierung: Sie besteht aus vielen
        Aufnahmen, "letzte Aufnahme loeschen" traefe nur die juengste.
        Vor dem Start wird deshalb der Zeilenstand gemerkt und hier wieder
        hergestellt.

        Args:
            stand: Anzahl der Datenzeilen, die erhalten bleiben.

        Returns:
            Anzahl der entfernten Zeilen.
        """
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            zeilen = [zeile for zeile in csv.reader(f) if zeile]

        kopf, daten = zeilen[0], zeilen[1:]
        entfernt = max(0, len(daten) - stand)
        if not entfernt:
            return 0

        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(kopf)
            writer.writerows(daten[:stand])

        logger.info("Auf %d Zeilen gekuerzt, %d entfernt.", stand, entfernt)
        self._letzte_aufnahme = None
        return entfernt

    @property
    def letzte_aufnahme(self) -> tuple[str, int] | None:
        """Label und Zeilenzahl der letzten Aufnahme, oder ``None``."""
        return self._letzte_aufnahme

    @safe_execute(logger, default=0)
    def record_label(
        self,
        label: str,
        duration: float | None = None,
        on_tick: Callable[[float, int], None] | None = None,
        abbrechen: threading.Event | None = None,
    ) -> int:
        """Zeichnet fuer ``duration`` Sekunden gelabelte EEG-Rohdaten auf.

        Nur Samples mit gueltigem Signal werden gespeichert. Faellt die
        Signalqualitaet auf 0 Prozent, pausiert die Aufnahme: Die Uhr
        steht still und laeuft erst weiter, wenn wieder Kontakt besteht.
        ``duration`` meint also die Dauer an brauchbaren Daten, nicht die
        Zeit, die man davorsitzt. Kommt das Signal gar nicht zurueck,
        bricht die Aufnahme nach dem Dreifachen der Dauer ab.

        Args:
            label: Fahrbefehl-Label, muss in ``CONFIG.labels`` enthalten
                sein.
            duration: Aufnahmedauer in Sekunden. Default aus
                ``CONFIG.default_training_duration``.
            on_tick: Optionaler Callback ``(elapsed_seconds,
                samples_recorded)``, der einmal pro Sekunde aufgerufen
                wird -- z. B. um in der GUI einen Fortschrittsbalken zu
                aktualisieren.
            abbrechen: Optionales Signal zum vorzeitigen Beenden. Wird es
                gesetzt, endet die Aufnahme beim naechsten Schleifendurchlauf;
                was bis dahin aufgezeichnet wurde, bleibt in der Datei.

        Jedes Datenpaket wird genau einmal gespeichert. Da das Headset
        ungefaehr ein Paket pro Sekunde liefert, ergibt eine Aufnahme von
        60 Sekunden also rund 60 Zeilen -- nicht mehr.

        Returns:
            Anzahl der tatsaechlich aufgezeichneten (gueltigen) Samples.

        Raises:
            ValueError: Wenn ``label`` kein bekanntes Fahrbefehl-Label ist.
        """
        if label not in CONFIG.labels:
            raise ValueError(f"Unbekanntes Label: {label!r} (erlaubt: {CONFIG.labels})")

        record_duration = duration if duration is not None else CONFIG.default_training_duration

        logger.info("Trainiere: %s (%.0f Sekunden) ...", label, record_duration)

        recorded = 0
        # Zaehlt nur die Zeit, in der tatsaechlich ein Signal anliegt. Faellt
        # der Kontakt aus, steht die Uhr still und die Aufnahme laeuft
        # danach weiter -- so bekommt man am Ende die volle Dauer an
        # brauchbaren Daten statt einer halb leeren Aufnahme.
        gemessene_zeit = 0.0
        letzter_takt = time.time()
        start_time = time.time()
        last_tick_second = -1
        pausiert = False

        # Zeitstempel des zuletzt geschriebenen Samples. Die Schleife dreht
        # sich zwanzigmal pro Sekunde, das Headset liefert aber nur ein
        # Paket pro Sekunde -- ohne diese Merker wuerde dasselbe Sample
        # rund zwanzigmal in die CSV geschrieben.
        letztes_sample: float | None = None

        # Notbremse: Kommt das Signal gar nicht zurueck, soll die Aufnahme
        # nicht endlos laufen.
        max_wartezeit = record_duration * 3 + 30.0

        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            while gemessene_zeit < record_duration:
                if abbrechen is not None and abbrechen.is_set():
                    logger.info("Aufnahme fuer %s abgebrochen (nach %.0f s).", label, gemessene_zeit)
                    break

                jetzt = time.time()
                schritt = jetzt - letzter_takt
                letzter_takt = jetzt

                if jetzt - start_time > max_wartezeit:
                    logger.warning(
                        "Aufnahme fuer %s abgebrochen: Signal kam nicht "
                        "zurueck (%.0f s gewartet, %.0f s aufgezeichnet).",
                        label,
                        jetzt - start_time,
                        gemessene_zeit,
                    )
                    break

                self.processor.poll()
                sample = self.processor.latest_sample

                if sample is not None and sample.is_valid:
                    if pausiert:
                        logger.info("Signal wieder da -- Aufnahme laeuft weiter.")
                        pausiert = False
                    # Die Uhr laeuft, solange Signal anliegt -- unabhaengig
                    # davon, ob gerade ein neues Paket eingetroffen ist.
                    gemessene_zeit += schritt

                    # Geschrieben wird dagegen nur, wenn das Sample neu ist.
                    if letztes_sample is None or sample.timestamp > letztes_sample:
                        letztes_sample = sample.timestamp
                        writer.writerow(
                            [
                                sample.attention,
                                sample.meditation,
                                sample.delta,
                                sample.theta,
                                sample.alpha,
                                sample.beta,
                                sample.gamma,
                                label,
                            ]
                        )
                        recorded += 1
                elif not pausiert:
                    pausiert = True
                    logger.info(
                        "Signalqualitaet 0 Prozent -- Aufnahme pausiert, "
                        "Uhr steht bei %.0f s.",
                        gemessene_zeit,
                    )

                current_second = int(gemessene_zeit)
                if on_tick is not None and current_second != last_tick_second:
                    last_tick_second = current_second
                    on_tick(gemessene_zeit, recorded)

                time.sleep(0.05)

        logger.info("Aufnahme fuer %s abgeschlossen: %d Samples gespeichert.", label, recorded)
        self._letzte_aufnahme = (label, recorded) if recorded else None
        return recorded

    def record_test(
        self,
        duration: float,
        on_tick: Callable[[float, int], None] | None = None,
    ) -> Path:
        """Zeichnet eine reine Testaufnahme auf, die nie trainiert wird.

        Gedacht zum Nachschauen und Auswerten: Man nimmt einen Zeitraum
        auf, in dem man etwas Bestimmtes tut, und prueft hinterher, ob sich
        das in den Daten wiederfindet.

        Drei bewusste Unterschiede zur Trainingsaufnahme:

        * **Eigene Datei** in ``testaufnahmen/`` statt in
          ``training_data.csv``. Der Klassifikator liest ausschliesslich
          letztere -- eine Testaufnahme kann also gar nicht versehentlich
          ins Modell geraten.
        * **Echte Uhrzeit statt Messzeit.** Bei der Trainingsaufnahme
          steht die Uhr still, solange kein Signal anliegt. Fuer eine
          Auswertung waere das fatal: Der zeitliche Verlauf muss dem
          entsprechen, was die Person in dieser Zeit wirklich getan hat.
        * **Alle Samples, auch die ungueltigen**, zusammen mit der
          Signalqualitaet. Ein Aussetzer ist eine Information, kein Grund
          zum Wegwerfen -- er erklaert spaeter, warum an einer Stelle
          nichts zu erkennen ist.

        Args:
            duration: Aufnahmedauer in Sekunden (echte Zeit).
            on_tick: Optionaler Callback ``(vergangene_sekunden,
                aufgezeichnete_samples)``, einmal je Sekunde.

        Returns:
            Pfad der geschriebenen Datei.
        """
        ordner = self.csv_path.parent / "testaufnahmen"
        ordner.mkdir(parents=True, exist_ok=True)
        ziel = ordner / f"testaufnahme_{time.strftime('%Y-%m-%d_%H%M%S')}.csv"

        logger.info("Testaufnahme ueber %.0f Sekunden nach %s ...", duration, ziel)

        recorded = 0
        start = time.time()
        letzter_takt = -1
        letztes_sample: float | None = None

        with open(ziel, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(TESTAUFNAHME_SPALTEN)

            while True:
                vergangen = time.time() - start
                if vergangen >= duration:
                    break

                self.processor.poll()
                sample = self.processor.latest_sample

                # Jedes Paket genau einmal. Die Schleife dreht sich
                # zwanzigmal je Sekunde, das Headset liefert einmal.
                if sample is not None and (
                    letztes_sample is None or sample.timestamp > letztes_sample
                ):
                    letztes_sample = sample.timestamp
                    writer.writerow(
                        [
                            f"{vergangen:.1f}",
                            time.strftime("%H:%M:%S"),
                            f"{sample.signal_percent:.0f}",
                            sample.attention,
                            sample.meditation,
                            sample.delta,
                            sample.theta,
                            sample.alpha,
                            sample.beta,
                            sample.gamma,
                        ]
                    )
                    recorded += 1

                sekunde = int(vergangen)
                if on_tick is not None and sekunde != letzter_takt:
                    letzter_takt = sekunde
                    on_tick(vergangen, recorded)

                time.sleep(0.05)

        logger.info(
            "Testaufnahme abgeschlossen: %d Samples in %s.", recorded, ziel.name
        )
        return ziel


KALIBRIERUNG_DATEI = "kalibrierung.json"


def kalibrierung_anweisungen_laden(profil_ordner: Path, vorgaben: dict[str, str]) -> dict[str, str]:
    """Liest die eigenen Anweisungen eines Profils fuer die Kalibrierung.

    Welche Strategie jemand fuer einen Befehl nutzt, ist persoenlich -- der
    eine schuettelt fuer Rueckwaerts den Kopf, der andere beisst die Zaehne
    zusammen. Deshalb liegt das je Profil in ``kalibrierung.json``.

    Args:
        profil_ordner: Ordner des Profils.
        vorgaben: Anweisungen fuer Befehle, die (noch) nicht eingetragen sind.

    Returns:
        Befehl -> Anweisung, Vorgaben mit den gespeicherten ueberschrieben.
    """
    anweisungen = dict(vorgaben)
    datei = profil_ordner / KALIBRIERUNG_DATEI
    try:
        gespeichert = json.loads(datei.read_text(encoding="utf-8")).get("anweisungen", {})
    except (OSError, ValueError):
        return anweisungen
    for befehl, text in gespeichert.items():
        if isinstance(text, str) and text.strip():
            anweisungen[befehl] = text.strip()
    return anweisungen


def kalibrierung_anweisungen_speichern(profil_ordner: Path, anweisungen: dict[str, str]) -> None:
    """Speichert die Anweisungen eines Profils, siehe ``kalibrierung_anweisungen_laden``."""
    profil_ordner.mkdir(parents=True, exist_ok=True)
    datei = profil_ordner / KALIBRIERUNG_DATEI
    try:
        inhalt = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inhalt = {}
    inhalt["anweisungen"] = anweisungen
    datei.write_text(json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Kalibrierungs-Anweisungen gespeichert: %s", anweisungen)


def kalibrierungsplan(labels: tuple[str, ...] | list[str]) -> list[str]:
    """Reihenfolge der Bloecke einer Kalibrierung.

    Jede moegliche Reihenfolge der Befehle kommt genau einmal vor -- bei
    drei Befehlen sechs Reihenfolgen, also 18 Bloecke. Das gleicht
    Reihenfolgeeffekte aus:

    * **Position:** Jeder Befehl steht gleich oft am Anfang, in der Mitte
      und am Ende einer Reihenfolge. Ermuedung oder Aufwaermen gegen Ende
      trifft alle Befehle gleich.
    * **Vorgaenger:** Innerhalb der Reihenfolgen folgt jeder Befehl jedem
      anderen gleich oft. Das zaehlt, weil Zustaende nachwirken -- Alpha
      faellt nach dem Augenoeffnen erst nach 2-3 s ab (Blindtest
      2026-09-27). Kaeme FORWARD immer nach STOP, lernte das Modell diesen
      Nachlauf als Teil von FORWARD.

    Die Reihenfolgen werden so aneinandergehaengt, dass nie zweimal
    derselbe Befehl aufeinanderfolgt: In der Trainingsdatei stehen keine
    Zeitstempel, zwei gleiche Bloecke hintereinander verschmoelzen dort zu
    einer Aufnahme. Unter den moeglichen Anordnungen wird die gewaehlt, bei
    der auch an den Nahtstellen alle Wechsel moeglichst gleich oft
    vorkommen.

    Args:
        labels: Die Befehle, etwa ``("STOP", "FORWARD", "BACKWARD")``.

    Returns:
        Die Bloecke in Aufnahmereihenfolge.
    """
    reihen = list(permutations(labels))
    if len(reihen) <= 1:
        return list(labels)

    def ungleichgewicht(anordnung) -> int:
        folge = [l for reihe in anordnung for l in reihe]
        paare = Counter(zip(folge, folge[1:]))
        return max(paare.values()) - min(paare.values())

    if len(reihen) <= 6:
        # Bei bis zu drei Befehlen sind das hoechstens 720 Anordnungen --
        # alle durchprobieren ist schneller, als darueber nachzudenken.
        gueltige = [
            anordnung
            for anordnung in permutations(reihen)
            if all(a[-1] != b[0] for a, b in zip(anordnung, anordnung[1:]))
        ]
        beste = min(gueltige, key=ungleichgewicht)
    else:
        # Ab vier Befehlen (24 Reihenfolgen) nicht mehr durchprobierbar:
        # der Reihe nach anhaengen, was nicht mit dem letzten Befehl beginnt.
        beste, offen = [reihen[0]], reihen[1:]
        while offen:
            naechste = next((r for r in offen if r[0] != beste[-1][-1]), offen[0])
            beste.append(naechste)
            offen.remove(naechste)

    return [l for reihe in beste for l in reihe]


# Spalten der Testaufnahme. Bewusst anders als TRAINING_CSV_COLUMNS: Fuer
# eine Auswertung braucht man den zeitlichen Verlauf und die
# Signalqualitaet, beides fehlt in der Trainings-CSV.
TESTAUFNAHME_SPALTEN = [
    "Sekunde",
    "Uhrzeit",
    "Signalqualitaet",
    "Attention",
    "Meditation",
    "Delta",
    "Theta",
    "Alpha",
    "Beta",
    "Gamma",
]


def _print_menu() -> None:
    """Gibt das Auswahlmenue fuer den CLI-Trainingsmodus aus."""
    print("\nWaehle einen Fahrbefehl zum Trainieren:")
    for key, label in _MENU.items():
        print(f"  {key} {label.capitalize()}")
    print("  q Beenden")


def run_cli(processor: BrainProcessor) -> None:
    """Startet den interaktiven, konsolenbasierten Trainingsmodus.

    Laeuft in einer Endlosschleife, bis der Benutzer ``q`` eingibt oder
    das Programm per Strg+C abgebrochen wird.

    Args:
        processor: Bereits laufender ``BrainProcessor``.
    """
    session = TrainingSession(processor)

    while True:
        _print_menu()
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nTraining beendet.")
            return

        if choice == "q":
            print("Training beendet.")
            return

        label = _MENU.get(choice)
        if label is None:
            print(f"Ungueltige Eingabe, bitte 1-{len(_MENU)} oder q waehlen.")
            continue

        print(f"Trainiere: {label} ...")

        def _progress(elapsed: float, count: int, _duration=CONFIG.default_training_duration) -> None:
            remaining = max(0.0, _duration - elapsed)
            print(f"  {remaining:4.1f} s verbleibend, {count} Samples aufgezeichnet", end="\r")

        try:
            recorded = session.record_label(label, on_tick=_progress)
        except Exception:  # noqa: BLE001 - Trainingsmodus darf nie abstuerzen
            logger.exception("Fehler bei der Aufnahme fuer Label %s", label)
            print("\nFehler bei der Aufnahme, siehe Log.")
            continue

        print(f"\nFertig: {recorded} Samples fuer '{label}' gespeichert.")
