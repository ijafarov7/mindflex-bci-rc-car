"""
utils.py
========

Allgemeine Hilfsfunktionen und -klassen, die von mehreren Modulen
gemeinsam genutzt werden:

* ``setup_logger``: einheitliches Logging (Konsole + Datei) fuer das
  gesamte Projekt.
* ``TimeRingBuffer``: ein zeitbasierter Ringpuffer, der automatisch
  Eintraege verwirft, die aelter als ``max_age_seconds`` sind. Wird von
  ``brain_processor.py`` genutzt, um z. B. "die letzten 60 Sekunden"
  EEG-Daten vorzuhalten.
* ``safe_execute``: kleiner Decorator, der jede Ausnahme in einer
  Funktion abfaengt, loggt und einen Default-Rueckgabewert liefert --
  zentrales Bauteil fuer die Anforderung "das Programm darf niemals
  abstuerzen".
"""

from __future__ import annotations

import functools
import logging
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Deque, Generic, Iterable, TypeVar

T = TypeVar("T")


def setup_logger(
    name: str,
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Erstellt (oder holt) einen konfigurierten Logger.

    Der Logger schreibt gleichzeitig auf die Konsole und -- falls
    ``log_file`` angegeben ist -- in eine rotierende Log-Datei (max.
    2 MB, 3 Backups), damit Log-Dateien nicht unbegrenzt wachsen.

    Args:
        name: Logger-Name, ueblicherweise ``__name__`` des aufrufenden
            Moduls.
        log_file: Optionaler Pfad zur Log-Datei. Wenn ``None``, wird nur
            auf die Konsole geloggt.
        level: Log-Level (Standard: ``logging.INFO``).

    Returns:
        Ein fertig konfigurierter ``logging.Logger``.
    """
    logger = logging.getLogger(name)

    # Verhindert doppelte Handler, falls setup_logger mehrfach fuer
    # denselben Namen aufgerufen wird (z. B. bei Modul-Neuladen).
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file is not None:
        try:
            file_handler = RotatingFileHandler(
                log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            # Wenn die Log-Datei nicht angelegt werden kann (z. B. Rechte-
            # Problem), soll das Programm trotzdem weiterlaufen -- nur
            # eben ohne Datei-Logging.
            logger.warning("Konnte Log-Datei %s nicht oeffnen: %s", log_file, exc)

    logger.propagate = False
    return logger


class TimeRingBuffer(Generic[T]):
    """Ein zeitbasierter Ringpuffer.

    Im Gegensatz zu einem klassischen, laengenbasierten Ringpuffer
    (fixe Anzahl Elemente) behaelt dieser Puffer alle Eintraege, deren
    Zeitstempel nicht aelter als ``max_age_seconds`` ist, und entfernt
    automatisch aeltere Eintraege bei jedem ``append``/``prune``-Aufruf.

    Das entspricht der Anforderung "Anzeige der letzten 60 Sekunden
    EEG-Daten" und "automatisches Entfernen alter Daten".

    Attributes:
        max_age_seconds: Maximales Alter eines Eintrags in Sekunden.
    """

    def __init__(self, max_age_seconds: float) -> None:
        """Initialisiert den Ringpuffer.

        Args:
            max_age_seconds: Wie lange (in Sekunden) ein Eintrag im
                Puffer verbleibt, bevor er automatisch entfernt wird.
        """
        self.max_age_seconds = max_age_seconds
        self._items: Deque[tuple] = deque()

    def append(self, timestamp: float, value: T) -> None:
        """Fuegt einen neuen Eintrag hinzu und entfernt veraltete Eintraege.

        Args:
            timestamp: Zeitstempel des Eintrags (z. B. ``time.time()``).
            value: Der zu speichernde Wert.
        """
        self._items.append((timestamp, value))
        self.prune(reference_time=timestamp)

    def prune(self, reference_time: float | None = None) -> None:
        """Entfernt alle Eintraege, die aelter als ``max_age_seconds`` sind.

        Args:
            reference_time: Zeitpunkt, gegen den das Alter berechnet wird.
                Falls ``None``, wird ``time.time()`` verwendet.
        """
        now = reference_time if reference_time is not None else time.time()
        cutoff = now - self.max_age_seconds
        while self._items and self._items[0][0] < cutoff:
            self._items.popleft()

    def values(self) -> list[T]:
        """Gibt alle aktuell gueltigen Werte (ohne Zeitstempel) zurueck."""
        self.prune()
        return [value for _, value in self._items]

    def items(self) -> list[tuple]:
        """Gibt alle aktuell gueltigen (Zeitstempel, Wert)-Paare zurueck."""
        self.prune()
        return list(self._items)

    def recent(self, seconds: float) -> list[T]:
        """Gibt nur die Werte der letzten ``seconds`` Sekunden zurueck.

        Args:
            seconds: Zeitfenster in Sekunden, gemessen vom letzten Eintrag
                rueckwaerts.

        Returns:
            Liste der Werte innerhalb des Zeitfensters (aelteste zuerst).
        """
        self.prune()
        if not self._items:
            return []
        latest_time = self._items[-1][0]
        cutoff = latest_time - seconds
        return [value for ts, value in self._items if ts >= cutoff]

    def clear(self) -> None:
        """Leert den Puffer vollstaendig."""
        self._items.clear()

    def __len__(self) -> int:
        self.prune()
        return len(self._items)


def safe_execute(
    logger: logging.Logger, default: Any = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator-Fabrik: faengt jede Exception der dekorierten Funktion ab.

    Zentrales Werkzeug fuer die Anforderung "Das Programm darf niemals
    abstuerzen": statt eine Exception nach oben durchzureichen, wird sie
    geloggt und ``default`` zurueckgegeben.

    Args:
        logger: Logger, ueber den Fehler protokolliert werden.
        default: Wert, der im Fehlerfall zurueckgegeben wird.

    Returns:
        Ein Decorator, der die uebergebene Funktion umschliesst.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except Exception:  # noqa: BLE001 - bewusst breit, siehe Docstring
                logger.exception("Unerwarteter Fehler in %s", func.__name__)
                return default  # type: ignore[return-value]

        return wrapper

    return decorator


def mean(values: Iterable[float]) -> float:
    """Arithmetisches Mittel einer Zahlenfolge, 0.0 bei leerer Eingabe.

    Args:
        values: Iterable von Zahlen.

    Returns:
        Mittelwert oder 0.0, falls ``values`` leer ist.
    """
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)
