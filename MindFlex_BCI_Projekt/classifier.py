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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import StratifiedGroupKFold, train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
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


def build_dataset_with_groups(
    rows: list[TrainingRow], extractor: FeatureExtractor | None = None
) -> tuple[list[list[float]], list[str], list[int]]:
    """Wie ``build_dataset``, liefert zusaetzlich die Aufnahme-Zugehoerigkeit.

    Die Gruppennummer sagt, aus welcher zusammenhaengenden Aufnahme ein
    Fenster stammt. Sie wird fuer eine ehrliche Bewertung gebraucht: Ein
    Fenster darf nicht im Training landen, waehrend ein stark
    ueberlappendes Nachbarfenster derselben Aufnahme im Test liegt --
    sonst misst man, wie gut das Modell die Aufnahme wiedererkennt, statt
    wie gut es den mentalen Zustand erkennt.

    Args:
        rows: Rohe, chronologische Trainingszeilen.
        extractor: Zu verwendender ``FeatureExtractor``.

    Returns:
        Tuple ``(X, y, groups)``.
    """
    extractor = extractor if extractor is not None else FeatureExtractor()

    window_size = max(
        CONFIG.min_samples_per_window,
        int(CONFIG.feature_window_seconds / CONFIG.nominal_sample_interval),
    )
    stride = max(1, CONFIG.window_stride_samples)

    X: list[list[float]] = []
    y: list[str] = []
    groups: list[int] = []

    for gruppe, segment in enumerate(_group_into_segments(rows)):
        # Nur die aktiven Fahrbefehle trainieren. Ohne diese Pruefung
        # entstuenden Klassen fuer Befehle, die gar nicht mehr gefahren
        # werden sollen -- alte Aufnahmen bleiben ja in der CSV stehen.
        # Sie wuerden den aktiven Klassen Vorhersagen wegnehmen, ohne je
        # nuetzlich zu sein.
        #
        # Gefiltert wird bewusst NACH dem Gruppieren: Wuerde man die
        # Zeilen vorher wegwerfen, ruecken zwei gleichnamige Aufnahmen
        # zusammen, die durch eine weggefallene getrennt waren -- aus zwei
        # getrennten Aufnahmen wuerde eine, und die Bewertung faele zu
        # optimistisch aus.
        if segment[0].label not in CONFIG.active_labels:
            continue

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
            groups.append(gruppe)

    return X, y, groups


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
    X, y, _ = build_dataset_with_groups(rows, extractor)
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

    # KNN entscheidet ueber Abstaende im Merkmalsraum und braucht deshalb
    # vergleichbar skalierte Merkmale. Die Rohwerte sind es nicht einmal
    # ansatzweise: Attention und Meditation liegen zwischen 1 und 100, die
    # Baender Delta und Theta gehen in die Millionen. Ohne Standardisierung
    # bestimmen allein Delta und Theta den Abstand, waehrend Attention und
    # Meditation -- die aussagekraeftigsten Werte des ThinkGear-Chips --
    # rechnerisch untergehen.
    #
    # Der StandardScaler zieht von jedem Merkmal seinen Mittelwert ab und
    # teilt durch die Standardabweichung. Danach zaehlt jedes Merkmal
    # gleich viel, und zwar in "wie viele Standardabweichungen weicht
    # dieser Wert vom Durchschnitt ab".
    #
    # Wichtig ist die Pipeline: Sie sorgt dafuer, dass Mittelwert und
    # Standardabweichung ausschliesslich aus den Trainingsdaten eines
    # Durchgangs stammen und dann auf die zurueckgehaltenen Aufnahmen
    # angewandt werden. Wuerde man vorher ueber den gesamten Datensatz
    # skalieren, floesse Wissen ueber die Testdaten ins Training ein --
    # dieselbe Art von Fehler wie der Zufallssplit ueber ueberlappende
    # Fenster.
    #
    # Die beiden Baumverfahren brauchen das nicht: Sie betrachten jedes
    # Merkmal einzeln und fragen nur "groesser oder kleiner als", was von
    # der Skalierung unabhaengig ist.
    _MODEL_FACTORIES = {
        "Decision Tree": lambda: DecisionTreeClassifier(random_state=CONFIG.random_state),
        "Random Forest": lambda: RandomForestClassifier(
            n_estimators=100, random_state=CONFIG.random_state
        ),
        "KNN": lambda: Pipeline(
            [
                ("scaler", StandardScaler()),
                ("knn", KNeighborsClassifier(n_neighbors=5)),
            ]
        ),
    }

    def __init__(
        self, model_path: Path | None = None, csv_path: Path | None = None
    ) -> None:
        """Initialisiert den Klassifikator (laedt noch kein Modell).

        Beide Pfade gehoeren zusammen und muessen beim Profilwechsel
        gemeinsam umgestellt werden. Wird nur der Modellpfad gewechselt,
        trainiert das Programm auf den Daten des alten Profils und
        speichert das Ergebnis ins neue -- ein Fehler, der erst dadurch
        auffaellt, dass das Modell Fahrbefehle kennt, die im Profil nie
        aufgezeichnet wurden.

        Args:
            model_path: Pfad zur Modelldatei. Default aus
                ``CONFIG.paths.model_path``.
            csv_path: Pfad zu den Trainingsdaten. Default aus
                ``CONFIG.paths.training_csv``.
        """
        self.model_path = model_path if model_path is not None else CONFIG.paths.model_path
        self.csv_path = csv_path if csv_path is not None else CONFIG.paths.training_csv
        self.model = None
        self.feature_names: list[str] = list(FEATURE_NAMES)
        self.last_accuracies: dict[str, float] = {}

        # Vergleichswert und Bewertungsverfahren des letzten Trainings.
        # Konfusionsmatrix des besten Verfahrens, aus denselben
        # zurueckgehaltenen Aufnahmen wie die Genauigkeit. Sie zeigt, WAS
        # verwechselt wird -- eine Genauigkeit allein sagt nur, WIE OFT.
        self.last_confusion: list[list[int]] = []
        self.last_confusion_labels: list[str] = []

        # Eine Genauigkeit ohne Basisrate laesst sich nicht einordnen:
        # 60 Prozent klingen gut, sind bei zwei gleich haeufigen Klassen
        # aber kaum besser als Muenzwurf.
        self.last_baseline: float = 0.0
        self.last_eval_method: str = ""

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
        self.last_confusion = payload.get("confusion", [])
        self.last_confusion_labels = payload.get("confusion_labels", [])
        self.last_baseline = payload.get("baseline", 0.0)
        self.last_eval_method = payload.get("eval_method", "")
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
            # Mitgespeichert, damit die Matrix nach einem Neustart auch
            # ohne erneutes Training zur Verfuegung steht -- genau wie die
            # Genauigkeiten.
            "confusion": self.last_confusion,
            "confusion_labels": self.last_confusion_labels,
            "baseline": self.last_baseline,
            "eval_method": self.last_eval_method,
        }
        # Profilordner kann beim ersten Training noch fehlen.
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(payload, f)
        logger.info("Bestes Modell nach %s gespeichert.", self.model_path)

    def _bewerte_modelle(
        self, X: list[list[float]], y: list[str], groups: list[int]
    ) -> dict[str, float]:
        """Bewertet alle Modelltypen und merkt sich Basisrate und Verfahren.

        Bewertet wird ueber **ganze Aufnahmen**: Eine Aufnahme liegt
        entweder vollstaendig im Training oder vollstaendig im Test. Ein
        zufaelliger Split ueber einzelne Fenster waere wertlos, weil sich
        benachbarte Fenster derselben Aufnahme fast vollstaendig
        ueberlappen -- das Modell saehe dieselben Daten praktisch zweimal
        und im Test viel besser aus, als es ist.

        Reicht die Datenlage dafuer nicht (weniger als zwei getrennte
        Aufnahmen je Klasse), wird auf den einfachen Zufallssplit
        zurueckgefallen, dann aber mit deutlicher Warnung.

        Args:
            X: Merkmalsvektoren.
            y: Labels.
            groups: Nummer der Aufnahme je Merkmalsvektor.

        Returns:
            Genauigkeit je Modelltyp. Leer, wenn keines bewertet werden
            konnte.
        """
        X_arr = np.asarray(X, dtype=float)
        y_arr = np.asarray(y)
        g_arr = np.asarray(groups)

        # Basisrate: Wie gut waere man, wenn man immer die haeufigste
        # Klasse raet? Ohne diesen Vergleich sagt eine Genauigkeit nichts.
        self.last_baseline = Counter(y).most_common(1)[0][1] / len(y)

        aufnahmen_je_klasse = Counter(
            label for label, _ in {(y[i], groups[i]) for i in range(len(y))}
        )
        min_aufnahmen = min(aufnahmen_je_klasse.values())

        if len(set(groups)) >= 2 and min_aufnahmen >= 2:
            n_splits = min(min_aufnahmen, 5)
            splitter = StratifiedGroupKFold(
                n_splits=n_splits, shuffle=True, random_state=CONFIG.random_state
            )
            folds = list(splitter.split(X_arr, y_arr, g_arr))
            self.last_eval_method = (
                f"{n_splits} Durchgänge, ganze Aufnahmen zurückgehalten"
            )
        else:
            logger.warning(
                "Nur %d Aufnahme(n) je Klasse -- fuer eine getrennte Bewertung "
                "sind mindestens zwei noetig. Es wird zufaellig aufgeteilt; die "
                "gemeldete Genauigkeit ist dadurch zu optimistisch. Nimm pro "
                "Fahrbefehl mehrere kuerzere Durchgaenge auf statt einer langen "
                "Aufnahme.",
                min_aufnahmen,
            )
            indizes = np.arange(len(y_arr))
            train_idx, test_idx = train_test_split(
                indizes,
                test_size=CONFIG.test_size,
                random_state=CONFIG.random_state,
                stratify=y_arr if len(set(y)) > 1 else None,
            )
            folds = [(train_idx, test_idx)]
            self.last_eval_method = "Zufallssplit — zu optimistisch"

        accuracies: dict[str, float] = {}

        # Je Verfahren alle Vorhersagen aus den zurueckgehaltenen
        # Aufnahmen sammeln. Daraus entsteht spaeter die Konfusionsmatrix
        # -- sie beruht damit auf denselben Daten wie die Genauigkeit und
        # nicht etwa auf dem Trainingsmaterial.
        vorhersagen: dict[str, tuple[list, list]] = {}

        for model_name, factory in self._MODEL_FACTORIES.items():
            richtig = 0
            gesamt = 0
            wahr_gesamt: list = []
            geschaetzt_gesamt: list = []
            try:
                for train_idx, test_idx in folds:
                    # Fehlt im Training eine Klasse ganz, ist der Durchgang
                    # nicht aussagekraeftig und wird uebersprungen.
                    if len(set(y_arr[train_idx])) < 2:
                        continue
                    model = factory()
                    model.fit(X_arr[train_idx], y_arr[train_idx])
                    vorhersage = model.predict(X_arr[test_idx])
                    richtig += int((vorhersage == y_arr[test_idx]).sum())
                    gesamt += len(test_idx)
                    wahr_gesamt.extend(y_arr[test_idx].tolist())
                    geschaetzt_gesamt.extend(vorhersage.tolist())
            except Exception:  # noqa: BLE001
                logger.exception("Training von %s fehlgeschlagen.", model_name)
                continue

            if gesamt == 0:
                logger.warning(
                    "%s konnte nicht bewertet werden -- kein Durchgang hatte "
                    "alle Klassen im Training.",
                    model_name,
                )
                continue

            accuracies[model_name] = richtig / gesamt
            vorhersagen[model_name] = (wahr_gesamt, geschaetzt_gesamt)
            logger.info(
                "Genauigkeit %s: %.1f %%", model_name, accuracies[model_name] * 100
            )

        # Matrix fuer das Verfahren aufstellen, das auch tatsaechlich
        # fahren wird -- das beste.
        self.last_confusion = []
        self.last_confusion_labels = []
        if accuracies:
            bestes = max(accuracies, key=accuracies.get)
            wahr, geschaetzt = vorhersagen[bestes]
            if wahr:
                self.last_confusion_labels = sorted(set(y))
                self.last_confusion = confusion_matrix(
                    wahr, geschaetzt, labels=self.last_confusion_labels
                ).tolist()
                logger.info(
                    "Konfusionsmatrix fuer %s ueber %d zurueckgehaltene Fenster.",
                    bestes,
                    len(wahr),
                )

        logger.info(
            "Bewertung: %s. Basisrate (immer die haeufigste Klasse raten): %.1f %%",
            self.last_eval_method,
            self.last_baseline * 100,
        )
        return accuracies

    def train(self, csv_path: Path | None = None) -> dict[str, float]:
        """Trainiert alle Modelle, waehlt das beste aus und speichert es.

        Args:
            csv_path: Pfad zur Trainings-CSV. Default ist ``self.csv_path``,
                also die Datei des aktuell eingestellten Profils.

        Returns:
            Dict der Testgenauigkeiten je Modelltyp, z. B.
            ``{"Decision Tree": 0.87, "Random Forest": 0.91, "KNN": 0.85}``.
            Ein leeres Dict bedeutet, dass nicht genug Daten zum
            Trainieren vorhanden waren.
        """
        rows = load_training_rows(csv_path if csv_path is not None else self.csv_path)
        if len(rows) < 2:
            logger.warning("Zu wenige Trainingsdaten (%d Zeilen) zum Trainieren.", len(rows))
            return {}

        extractor = FeatureExtractor()
        X, y, groups = build_dataset_with_groups(rows, extractor)

        unique_labels = set(y)
        if len(X) < 4 or len(unique_labels) < 2:
            logger.warning(
                "Zu wenige Trainingsbeispiele (%d) oder Labels (%d) zum Trainieren.",
                len(X),
                len(unique_labels),
            )
            return {}

        accuracies = self._bewerte_modelle(X, y, groups)

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
