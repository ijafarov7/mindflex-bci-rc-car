"""
classifier.py
==============

Trainiert und verwendet die Machine-Learning-Modelle, die mentale
Zustaende (STOP/FORWARD/BACKWARD/LEFT/RIGHT) aus EEG-Feature-Vektoren
erkennen.

Ablauf beim Training:

1. ``training_data.csv`` einlesen (Rohwerte + Label je Zeile).
2. Zusammenhaengende Zeilen mit demselben Label (= eine Aufnahme-Session
   aus ``trainer.py``) zu einem "Segment" gruppieren.
3. Innerhalb jedes Segments ein gleitendes Zeitfenster ueber die Zeilen
   schieben und pro Fenster einen Feature-Vektor (``feature_extractor``)
   berechnen -- das liefert deutlich mehr Trainingsbeispiele als ein
   Feature-Vektor pro Aufnahme.
4. Drei Modelle trainieren (Decision Tree, Random Forest, KNN),
   Genauigkeit auf einem Testsplit vergleichen.
5. Das beste Modell auf dem kompletten Datensatz neu trainieren und
   nach ``trained_model.pkl`` speichern.

Beim Programmstart wird versucht, ein zuvor gespeichertes Modell zu
laden ("automatisches Laden des letzten Modells").
"""

from __future__ import annotations

import csv
import pickle
from dataclasses import dataclass
from pathlib import Path

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier

from config import CONFIG
from feature_extractor import FEATURE_NAMES, FeatureExtractor
from utils import safe_execute, setup_logger

logger = setup_logger(__name__, CONFIG.paths.log_file)


@dataclass(frozen=True)
class TrainingRow:
    """Eine einzelne, aus ``training_data.csv`` gelesene Zeile.

    Erfuellt dasselbe strukturelle Interface wie
    ``serial_receiver.EEGSample`` (siehe ``feature_extractor.SampleLike``),
    damit derselbe ``FeatureExtractor`` fuer Training UND Echtzeitbetrieb
    verwendet werden kann.
    """

    timestamp: float
    attention: float
    meditation: float
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    label: str


class ModelNotTrainedError(RuntimeError):
    """Wird ausgeloest, wenn eine Vorhersage ohne trainiertes Modell versucht wird."""


def load_training_rows(csv_path: Path | None = None) -> list[TrainingRow]:
    """Liest ``training_data.csv`` ein und rekonstruiert synthetische Zeitstempel.

    Args:
        csv_path: Pfad zur CSV-Datei. Default aus
            ``CONFIG.paths.training_csv``.

    Returns:
        Liste von ``TrainingRow`` in der Reihenfolge der Datei (also
        chronologisch, so wie sie von ``trainer.py`` angehaengt wurden).
    """
    path = csv_path if csv_path is not None else CONFIG.paths.training_csv
    rows: list[TrainingRow] = []

    if not path.exists():
        logger.warning("Trainingsdatei %s existiert nicht.", path)
        return rows

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for index, raw_row in enumerate(reader):
            try:
                rows.append(
                    TrainingRow(
                        timestamp=index * CONFIG.nominal_sample_interval,
                        attention=float(raw_row["Attention"]),
                        meditation=float(raw_row["Meditation"]),
                        delta=float(raw_row["Delta"]),
                        theta=float(raw_row["Theta"]),
                        alpha=float(raw_row["Alpha"]),
                        beta=float(raw_row["Beta"]),
                        gamma=float(raw_row["Gamma"]),
                        label=raw_row["Label"],
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("Ueberspringe ungueltige Zeile %d: %s", index, exc)

    return rows


def _group_into_segments(rows: list[TrainingRow]) -> list[list[TrainingRow]]:
    """Gruppiert aufeinanderfolgende Zeilen mit identischem Label.

    Args:
        rows: Chronologische Liste von ``TrainingRow``.

    Returns:
        Liste von Segmenten (jedes Segment ist eine Liste von
        ``TrainingRow`` mit demselben Label).
    """
    segments: list[list[TrainingRow]] = []
    for row in rows:
        if segments and segments[-1][-1].label == row.label:
            segments[-1].append(row)
        else:
            segments.append([row])
    return segments


def build_dataset(
    rows: list[TrainingRow], extractor: FeatureExtractor | None = None
) -> tuple[list[list[float]], list[str]]:
    """Baut aus rohen Trainingszeilen Feature-Vektoren + Labels fuer sklearn.

    Args:
        rows: Rohe, chronologische Trainingszeilen (siehe
            ``load_training_rows``).
        extractor: Zu verwendender ``FeatureExtractor``. Default: neue
            Instanz mit Standard-Konfiguration.

    Returns:
        Tuple ``(X, y)`` -- ``X`` ist eine Liste von Feature-Vektoren,
        ``y`` die dazugehoerigen Labels.
    """
    extractor = extractor if extractor is not None else FeatureExtractor()

    window_size = max(
        CONFIG.min_samples_per_window,
        int(CONFIG.feature_window_seconds / CONFIG.nominal_sample_interval),
    )
    stride = max(1, CONFIG.window_stride_samples)

    X: list[list[float]] = []
    y: list[str] = []

    for segment in _group_into_segments(rows):
        if len(segment) < CONFIG.min_samples_per_window:
            continue
        for start in range(0, len(segment), stride):
            window = segment[start : start + window_size]
            if len(window) < CONFIG.min_samples_per_window:
                continue
            try:
                features = extractor.extract(window)
            except ValueError:
                continue
            X.append(features)
            y.append(segment[0].label)

    return X, y


class BCIClassifier:
    """Trainiert, evaluiert, speichert und laedt die BCI-Klassifikationsmodelle.

    Attributes:
        model_path: Pfad zur serialisierten Modelldatei.
        model: Das aktuell geladene/trainierte sklearn-Modell (oder
            ``None``, falls noch keines geladen wurde).
        feature_names: Reihenfolge der Feature-Namen, mit der ``model``
            trainiert wurde.
        last_accuracies: Genauigkeiten aller beim letzten Training
            verglichenen Modelle, z. B.
            ``{"Decision Tree": 0.87, "Random Forest": 0.91, "KNN": 0.85}``.
    """

    _MODEL_FACTORIES = {
        "Decision Tree": lambda: DecisionTreeClassifier(random_state=CONFIG.random_state),
        "Random Forest": lambda: RandomForestClassifier(
            n_estimators=100, random_state=CONFIG.random_state
        ),
        "KNN": lambda: KNeighborsClassifier(n_neighbors=5),
    }

    def __init__(self, model_path: Path | None = None) -> None:
        """Initialisiert den Klassifikator (laedt noch kein Modell).

        Args:
            model_path: Pfad zur Modelldatei. Default aus
                ``CONFIG.paths.model_path``.
        """
        self.model_path = model_path if model_path is not None else CONFIG.paths.model_path
        self.model = None
        self.feature_names: list[str] = list(FEATURE_NAMES)
        self.last_accuracies: dict[str, float] = {}

    @safe_execute(logger, default=False)
    def load_model(self) -> bool:
        """Laedt ein zuvor gespeichertes Modell von der Festplatte.

        Returns:
            ``True``, wenn erfolgreich geladen, sonst ``False`` (z. B.
            weil noch keine Modelldatei existiert).
        """
        if not self.model_path.exists():
            logger.info("Keine gespeicherte Modelldatei unter %s gefunden.", self.model_path)
            return False

        with open(self.model_path, "rb") as f:
            payload = pickle.load(f)

        self.model = payload["model"]
        self.feature_names = payload["feature_names"]
        self.last_accuracies = payload.get("accuracies", {})
        logger.info("Modell aus %s geladen.", self.model_path)
        return True

    def _save_model(self, model, accuracies: dict[str, float]) -> None:
        """Speichert Modell + Metadaten als Pickle-Datei.

        Args:
            model: Das zu speichernde, trainierte sklearn-Modell.
            accuracies: Genauigkeiten aller verglichenen Modelle (fuer
                Anzeige-/Debug-Zwecke mit abgespeichert).
        """
        payload = {
            "model": model,
            "feature_names": self.feature_names,
            "accuracies": accuracies,
        }
        with open(self.model_path, "wb") as f:
            pickle.dump(payload, f)
        logger.info("Bestes Modell nach %s gespeichert.", self.model_path)

    def train(self, csv_path: Path | None = None) -> dict[str, float]:
        """Trainiert alle Modelle, waehlt das beste aus und speichert es.

        Args:
            csv_path: Pfad zur Trainings-CSV. Default aus
                ``CONFIG.paths.training_csv``.

        Returns:
            Dict der Testgenauigkeiten je Modelltyp, z. B.
            ``{"Decision Tree": 0.87, "Random Forest": 0.91, "KNN": 0.85}``.
            Ein leeres Dict bedeutet, dass nicht genug Daten zum
            Trainieren vorhanden waren.
        """
        rows = load_training_rows(csv_path)
        if len(rows) < 2:
            logger.warning("Zu wenige Trainingsdaten (%d Zeilen) zum Trainieren.", len(rows))
            return {}

        extractor = FeatureExtractor()
        X, y = build_dataset(rows, extractor)

        unique_labels = set(y)
        if len(X) < 4 or len(unique_labels) < 2:
            logger.warning(
                "Zu wenige Trainingsbeispiele (%d) oder Labels (%d) zum Trainieren.",
                len(X),
                len(unique_labels),
            )
            return {}

        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=CONFIG.test_size,
                random_state=CONFIG.random_state,
                stratify=y,
            )
        except ValueError:
            # Stratifizierter Split scheitert z. B. bei sehr wenigen
            # Beispielen pro Klasse -- dann ohne Stratifizierung splitten.
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=CONFIG.test_size, random_state=CONFIG.random_state
            )

        accuracies: dict[str, float] = {}
        fitted_models: dict[str, object] = {}

        for model_name, factory in self._MODEL_FACTORIES.items():
            try:
                model = factory()
                model.fit(X_train, y_train)
                predictions = model.predict(X_test)
                accuracy = accuracy_score(y_test, predictions)
            except Exception:  # noqa: BLE001
                logger.exception("Training von %s fehlgeschlagen.", model_name)
                continue

            accuracies[model_name] = accuracy
            fitted_models[model_name] = model
            logger.info("Genauigkeit %s: %.1f %%", model_name, accuracy * 100)

        if not accuracies:
            logger.error("Kein Modell konnte trainiert werden.")
            return {}

        best_name = max(accuracies, key=accuracies.get)
        logger.info(
            "Bestes Modell: %s (%.1f %% Genauigkeit) -- wird auf allen Daten neu trainiert.",
            best_name,
            accuracies[best_name] * 100,
        )

        # Bestes Modell auf dem GESAMTEN Datensatz (Train + Test) neu
        # trainieren, um alle verfuegbaren Daten fuer den produktiven
        # Einsatz zu nutzen.
        best_model = self._MODEL_FACTORIES[best_name]()
        best_model.fit(X, y)

        self.model = best_model
        self.feature_names = list(FEATURE_NAMES)
        self.last_accuracies = accuracies
        self._save_model(best_model, accuracies)

        return accuracies

    @safe_execute(logger, default=(None, 0.0))
    def predict(self, window: list) -> tuple[str | None, float]:
        """Sagt das Fahrbefehl-Label fuer ein Fenster aktueller Samples voraus.

        Args:
            window: Fenster von Samples (siehe
                ``feature_extractor.SampleLike``), typischerweise aus
                ``BrainProcessor.get_window()``.

        Returns:
            Tuple ``(label, confidence)``. ``label`` ist ``None``, wenn
            kein Modell geladen ist oder die Vorhersage fehlschlaegt.
            ``confidence`` liegt zwischen 0.0 und 1.0.

        Raises:
            ModelNotTrainedError: Wenn kein Modell geladen/trainiert ist.
        """
        if self.model is None:
            raise ModelNotTrainedError("Es ist noch kein Modell trainiert/geladen.")

        extractor = FeatureExtractor()
        features = extractor.extract(window)

        label = self.model.predict([features])[0]

        confidence = 1.0
        if hasattr(self.model, "predict_proba"):
            probabilities = self.model.predict_proba([features])[0]
            confidence = float(max(probabilities))

        return label, confidence
