"""
config.py
=========

Zentrale Konfigurationsdatei fuer das MindFlex-BCI-Projekt.

Alle Werte, die sich je nach Hardware-Setup aendern koennen (COM-Ports,
Baudraten, Datei-Pfade, Zeitfenster, ...) werden ausschliesslich hier
gepflegt. Kein anderes Modul soll "magische Zahlen" fuer diese Werte
enthalten -- stattdessen wird immer aus ``config`` importiert.

Die Konfiguration ist bewusst als einfache Dataclass-Struktur gehalten
(kein YAML/JSON-Parsing), damit das Projekt ohne zusaetzliche
Abhaengigkeiten lauffaehig bleibt. Wer die Werte zur Laufzeit aendern
moechte, kann das ueber Umgebungsvariablen tun (siehe unten).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Basis-Verzeichnis des Projekts (Ordner, in dem diese Datei liegt).
# Alle relativen Pfade (CSV, Modell, Logs) werden von hier aus aufgeloest,
# damit das Programm unabhaengig vom aktuellen Arbeitsverzeichnis laeuft.
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent


def _env_str(name: str, default: str) -> str:
    """Liest eine String-Umgebungsvariable mit Fallback-Wert.

    Args:
        name: Name der Umgebungsvariable.
        default: Wert, falls die Variable nicht gesetzt ist.

    Returns:
        Der Wert der Umgebungsvariable oder ``default``.
    """
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    """Liest eine Integer-Umgebungsvariable mit Fallback-Wert.

    Ungueltige Werte (z. B. nicht-numerischer Text) fuehren nicht zu einem
    Absturz, sondern fallen auf den Default zurueck.

    Args:
        name: Name der Umgebungsvariable.
        default: Wert, falls die Variable nicht gesetzt oder ungueltig ist.

    Returns:
        Der geparste Integer-Wert oder ``default``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class SerialConfig:
    """Konfiguration fuer eine serielle Verbindung.

    Attributes:
        port: Name des seriellen Ports (z. B. "COM5"). ``None`` bedeutet
            "automatisch anhand von port_hint suchen" (siehe
            serial_receiver.find_port_by_hint).
        baudrate: Baudrate der Verbindung.
        timeout: Lese-Timeout in Sekunden fuer serial.Serial.
        port_hint: Teilstring, nach dem in der Geraetebeschreibung gesucht
            wird, falls ``port`` nicht explizit gesetzt ist (z. B. "Arduino").
    """

    port: str | None
    baudrate: int
    timeout: float = 1.0
    port_hint: str = "Arduino"


@dataclass(frozen=True)
class Paths:
    """Alle Datei- und Verzeichnispfade des Projekts.

    Attributes:
        training_csv: CSV-Datei mit aufgezeichneten Trainingsdaten.
        model_path: Pfad zum serialisierten (besten) ML-Modell.
        log_file: Pfad zur Log-Datei.
    """

    training_csv: Path = BASE_DIR / "training_data.csv"
    model_path: Path = BASE_DIR / "trained_model.pkl"
    log_file: Path = BASE_DIR / "mindflex_bci.log"


@dataclass(frozen=True)
class AppConfig:
    """Gesamt-Konfiguration der Anwendung.

    Buendelt alle Teil-Konfigurationen und Konstanten, die fuer die
    Verarbeitung der EEG-Daten und das maschinelle Lernen relevant sind.

    Attributes:
        eeg_serial: Serielle Konfiguration fuer den EEG-Arduino.
        car_serial: Serielle Konfiguration fuer den RC-Auto-Arduino.
        paths: Datei-Pfade.
        num_eeg_values: Erwartete Anzahl an CSV-Werten pro EEG-Paket.
        invalid_signal_quality: Signal-Quality-Wert, der "kein Kontakt"
            bedeutet (ThinkGear-Konvention: 200 = kein Signal).
        history_seconds: Wie viele Sekunden an Rohdaten im Ringpuffer
            fuer die Anzeige/Feature-Extraktion vorgehalten werden.
        feature_window_seconds: Fenstergroesse (in Sekunden), die pro
            Vorhersage/Trainings-Sample an den Feature Extractor
            uebergeben wird.
        recent_mean_seconds: Fenstergroesse fuer das Feature
            "Mittelwert der letzten X Sekunden".
        min_samples_per_window: Mindestanzahl an Samples, die ein Fenster
            enthalten muss, damit Features berechnet werden (verhindert
            Features aus einem einzelnen, verrauschten Sample).
        default_training_duration: Default-Aufnahmedauer (Sekunden) im
            Trainingsmodus.
        prediction_interval_seconds: Wie oft (Sekunden) im Echtzeitbetrieb
            eine neue Vorhersage berechnet wird.
        random_state: Fixer Seed fuer reproduzierbares Training/Split.
        test_size: Anteil der Daten, der als Testset zurueckgehalten wird.
        labels: Erlaubte Fahrbefehl-Labels in fester Reihenfolge.
    """

    eeg_serial: SerialConfig = field(
        default_factory=lambda: SerialConfig(
            port=_env_str("MINDFLEX_EEG_PORT", "") or None,
            baudrate=_env_int("MINDFLEX_EEG_BAUD", 9600),
            port_hint="Arduino",
        )
    )
    car_serial: SerialConfig = field(
        default_factory=lambda: SerialConfig(
            port=_env_str("MINDFLEX_CAR_PORT", "") or None,
            baudrate=_env_int("MINDFLEX_CAR_BAUD", 9600),
            port_hint="Arduino",
        )
    )
    paths: Paths = field(default_factory=Paths)

    num_eeg_values: int = 11
    invalid_signal_quality: int = 200

    history_seconds: float = 60.0
    feature_window_seconds: float = 5.0
    recent_mean_seconds: float = 5.0
    min_samples_per_window: int = 3

    default_training_duration: float = 10.0

    prediction_interval_seconds: float = 1.0

    random_state: int = 42
    test_size: float = 0.25

    labels: tuple = ("STOP", "FORWARD", "BACKWARD", "LEFT", "RIGHT")

    # Angenommener Abstand (Sekunden) zwischen zwei aufgezeichneten CSV-
    # Zeilen. Die ThinkGear/Arduino-Brain-Library liefert Attention/
    # Meditation/Powerbaender nominell einmal pro Sekunde. Da
    # training_data.csv keine echten Zeitstempel enthaelt, wird dieser
    # Wert genutzt, um beim Laden der Trainingsdaten synthetische
    # Zeitstempel zu rekonstruieren (fuer das gleitende Zeitfenster).
    nominal_sample_interval: float = 1.0

    # Schrittweite (in Samples) beim Sliding-Window ueber aufgezeichnete
    # Trainingsdaten. 1 = maximale Ueberlappung/maximale Anzahl Trainings-
    # beispiele, groessere Werte reduzieren Redundanz zwischen Fenstern.
    window_stride_samples: int = 1


# Einzige Instanz, die im gesamten Projekt importiert werden soll:
#     from config import CONFIG
CONFIG = AppConfig()


# CSV-Spaltennamen fuer training_data.csv (Reihenfolge ist verbindlich).
TRAINING_CSV_COLUMNS: list[str] = [
    "Attention",
    "Meditation",
    "Delta",
    "Theta",
    "Alpha",
    "Beta",
    "Gamma",
    "Label",
]

# Mapping von Fahrbefehl-Label auf das Einzelzeichen, das an den
# RC-Auto-Arduino gesendet wird.
LABEL_TO_COMMAND: dict = {
    "STOP": "S",
    "FORWARD": "F",
    "BACKWARD": "B",
    "LEFT": "L",
    "RIGHT": "R",
}
