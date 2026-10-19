"""
feature_extractor.py
=====================

Berechnet aus einem Fenster von EEG-Samples einen numerischen
Feature-Vektor, der anschliessend von ``classifier.py`` (Training und
Vorhersage) verwendet wird.

Damit Training und Echtzeit-Vorhersage garantiert dieselben Merkmale in
derselben Reihenfolge sehen, ist die Reihenfolge in ``FEATURE_NAMES``
verbindlich -- ``FeatureExtractor.extract`` liefert seine Werte immer
in genau dieser Reihenfolge.

Die Eingabe ist bewusst "duck-typed": Jedes Objekt im Fenster muss nur
die Attribute ``timestamp``, ``attention``, ``meditation``, ``delta``,
``theta``, ``alpha``, ``beta`` und ``gamma`` besitzen. Sowohl
``serial_receiver.EEGSample`` (Echtzeitbetrieb) als auch die beim Laden
von ``training_data.csv`` erzeugten Zeilen-Objekte (siehe
``classifier.py``) erfuellen das.
"""

from __future__ import annotations

import statistics
from typing import Protocol

from config import CONFIG
from utils import mean


class SampleLike(Protocol):
    """Struktureller Typ, den ein Sample fuer die Feature-Extraktion braucht."""

    timestamp: float
    attention: float
    meditation: float
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float


# Die Basissignale, ueber die einfache Statistiken (Mittelwert, Max,
# Min, Std) berechnet werden.
_BASE_SIGNALS: tuple = ("attention", "meditation", "delta", "theta", "alpha", "beta", "gamma")

# Verbindliche, feste Reihenfolge der berechneten Merkmale. Wird sowohl
# beim Training als auch bei der Live-Vorhersage verwendet.
FEATURE_NAMES: list[str] = (
    [f"{signal}_mean" for signal in _BASE_SIGNALS]
    + [f"{signal}_max" for signal in _BASE_SIGNALS]
    + [f"{signal}_min" for signal in _BASE_SIGNALS]
    + [f"{signal}_std" for signal in _BASE_SIGNALS]
    + [
        "beta_alpha_ratio",
        "attention_delta",
        "meditation_delta",
        "attention_recent_mean",
        "meditation_recent_mean",
    ]
)


class FeatureExtractor:
    """Berechnet statistische Merkmale aus einem Fenster von EEG-Samples.

    Attributes:
        recent_seconds: Fenstergroesse (Sekunden) fuer die Merkmale
            ``attention_recent_mean`` / ``meditation_recent_mean``
            ("Mittelwert der letzten X Sekunden").
    """

    def __init__(self, recent_seconds: float | None = None) -> None:
        """Initialisiert den Feature Extractor.

        Args:
            recent_seconds: Siehe ``recent_seconds``-Attribut. Default
                aus ``CONFIG.recent_mean_seconds``.
        """
        self.recent_seconds = (
            recent_seconds if recent_seconds is not None else CONFIG.recent_mean_seconds
        )

    def extract(self, window: list[SampleLike]) -> list[float]:
        """Berechnet den Feature-Vektor fuer ein Fenster von Samples.

        Args:
            window: Chronologisch aufsteigende Liste von Samples
                (aeltestes zuerst, neuestes zuletzt). Muss mindestens
                ein Element enthalten.

        Returns:
            Liste von Floats in der Reihenfolge von ``FEATURE_NAMES``.

        Raises:
            ValueError: Wenn ``window`` leer ist.
        """
        if not window:
            raise ValueError("Kann keine Features aus einem leeren Fenster berechnen.")

        features: list[float] = []

        signal_series = {
            signal: [getattr(sample, signal) for sample in window] for signal in _BASE_SIGNALS
        }

        for signal in _BASE_SIGNALS:
            features.append(mean(signal_series[signal]))
        for signal in _BASE_SIGNALS:
            features.append(max(signal_series[signal]))
        for signal in _BASE_SIGNALS:
            features.append(min(signal_series[signal]))
        for signal in _BASE_SIGNALS:
            series = signal_series[signal]
            features.append(statistics.pstdev(series) if len(series) > 1 else 0.0)

        alpha_mean = mean(signal_series["alpha"])
        beta_mean = mean(signal_series["beta"])
        beta_alpha_ratio = beta_mean / alpha_mean if alpha_mean > 0 else 0.0
        features.append(beta_alpha_ratio)

        attention_series = signal_series["attention"]
        meditation_series = signal_series["meditation"]
        features.append(attention_series[-1] - attention_series[0])
        features.append(meditation_series[-1] - meditation_series[0])

        latest_time = window[-1].timestamp
        cutoff = latest_time - self.recent_seconds
        recent_attention = [s.attention for s in window if s.timestamp >= cutoff]
        recent_meditation = [s.meditation for s in window if s.timestamp >= cutoff]
        features.append(mean(recent_attention))
        features.append(mean(recent_meditation))

        return features

    def extract_as_dict(self, window: list[SampleLike]) -> dict[str, float]:
        """Wie ``extract``, aber als benanntes Dict statt reiner Liste.

        Nuetzlich fuer Logging/Debugging/GUI-Anzeige.

        Args:
            window: Siehe ``extract``.

        Returns:
            Dict von Feature-Name auf Wert.
        """
        values = self.extract(window)
        return dict(zip(FEATURE_NAMES, values))
