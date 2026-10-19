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


def _env_float(name: str, default: float) -> float:
    """Liest eine Float-Umgebungsvariable mit Fallback-Wert.

    Args:
        name: Name der Umgebungsvariable.
        default: Wert, falls die Variable nicht gesetzt oder ungueltig ist.

    Returns:
        Der geparste Float-Wert oder ``default``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
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


def _env_labels(name: str, default: tuple) -> tuple:
    """Liest eine kommagetrennte Label-Liste aus einer Umgebungsvariablen.

    Beispiel::

        MINDFLEX_ACTIVE_LABELS="STOP,FORWARD,LEFT,RIGHT"

    Args:
        name: Name der Umgebungsvariable.
        default: Wert, falls die Variable nicht gesetzt oder leer ist.

    Returns:
        Die geparsten Labels in Grossbuchstaben, oder ``default``.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    parsed = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return parsed or default


@dataclass(frozen=True)
class BleConfig:
    """Konfiguration der BLE-Funkstrecke zum RC-Auto.

    Das Funkmodul am RC-Auto-Arduino ist ein CC2541 (HM-10-Familie), also
    Bluetooth Low Energy und *nicht* das klassische SPP-Profil eines HC-05.
    Es erscheint deshalb weder in den Bluetooth-Einstellungen noch als
    serieller Port -- die Kommunikation laeuft ueber eine GATT-
    Charakteristik, deren Inhalt das Modul an seinem TXD-Pin ausgibt.

    Attributes:
        address: Adresse des Funkmoduls. Unter macOS ist das eine
            CoreBluetooth-UUID, die **pro Rechner** unterschiedlich ist --
            auf einem anderen Mac muss sie neu ermittelt werden
            (``python3 ble_scan.py``). Leerer String bedeutet: beim
            Verbinden anhand von ``service_uuid`` suchen.
        service_uuid: Service, an dem das Modul erkannt wird, falls keine
            Adresse konfiguriert ist.
        char_uuid: Charakteristik, in die Fahrbefehle geschrieben werden.
        scan_timeout: Wie lange beim Verbinden nach dem Modul gesucht wird.
        resend_interval: Abstand in Sekunden, in dem der zuletzt gesendete
            Fahrbefehl wiederholt wird. Der Arduino stoppt die Motoren,
            wenn laenger als eine Sekunde kein Befehl ankommt; da nur bei
            Aenderung gesendet wird, wuerde das Auto ohne diese
            Wiederholung nach einer Sekunde stehenbleiben. 0 schaltet die
            Wiederholung ab.
    """

    address: str
    service_uuid: str = "0000ffe0-0000-1000-8000-00805f9b34fb"
    char_uuid: str = "0000ffe1-0000-1000-8000-00805f9b34fb"
    scan_timeout: float = 15.0
    resend_interval: float = 0.4


# Oberordner fuer die Profile. Jede Person bekommt darin einen eigenen
# Unterordner mit ihren Trainingsdaten und ihrem Modell.
PROFIL_BASIS: Path = BASE_DIR / "profile"


def saubere_profilbezeichnung(name: str) -> str:
    """Macht aus einer Eingabe einen unbedenklichen Ordnernamen.

    Erlaubt sind Buchstaben, Ziffern, Bindestrich und Unterstrich.
    Alles andere wird zu einem Bindestrich, damit ein versehentlich
    eingegebener Schraegstrich nicht in einem fremden Verzeichnis landet.

    Args:
        name: Vom Benutzer eingegebener Profilname.

    Returns:
        Bereinigter Name, nie leer.
    """
    erlaubt = "".join(
        zeichen if (zeichen.isalnum() or zeichen in "-_") else "-"
        for zeichen in name.strip().lower()
    )
    erlaubt = erlaubt.strip("-")
    return erlaubt or "standard"


def vorhandene_profile() -> list[str]:
    """Listet alle angelegten Profile auf.

    Returns:
        Alphabetisch sortierte Namen. Enthaelt immer mindestens
        ``"standard"``, auch wenn noch nichts angelegt wurde.
    """
    namen = set()
    if PROFIL_BASIS.is_dir():
        namen = {p.name for p in PROFIL_BASIS.iterdir() if p.is_dir()}
    namen.add("standard")
    return sorted(namen)


@dataclass(frozen=True)
class Paths:
    """Alle Datei- und Verzeichnispfade des Projekts.

    Trainingsdaten und Modell haengen am **Profil**: EEG-Muster sind von
    Person zu Person verschieden, ein auf einen Menschen trainiertes
    Modell funktioniert bei einem anderen praktisch nicht. Jede Person
    bekommt deshalb ihren eigenen Ordner unter ``profile/``.

    Die Log-Datei ist bewusst gemeinsam -- sie protokolliert das Programm,
    nicht die Person.

    Attributes:
        profil: Name des aktiven Profils. Ueber ``MINDFLEX_PROFIL``
            setzbar, sonst ``"standard"``.
        log_file: Pfad zur gemeinsamen Log-Datei.
    """

    profil: str = field(default_factory=lambda: _env_str("MINDFLEX_PROFIL", "standard"))
    log_file: Path = BASE_DIR / "mindflex_bci.log"

    @property
    def profil_verzeichnis(self) -> Path:
        """Ordner des aktiven Profils."""
        return PROFIL_BASIS / saubere_profilbezeichnung(self.profil)

    @property
    def training_csv(self) -> Path:
        """Trainingsdaten des aktiven Profils."""
        return self.profil_verzeichnis / "training_data.csv"

    @property
    def model_path(self) -> Path:
        """Modelldatei des aktiven Profils."""
        return self.profil_verzeichnis / "trained_model.pkl"


@dataclass(frozen=True)
class AppConfig:
    """Gesamt-Konfiguration der Anwendung.

    Buendelt alle Teil-Konfigurationen und Konstanten, die fuer die
    Verarbeitung der EEG-Daten und das maschinelle Lernen relevant sind.

    Attributes:
        eeg_serial: Serielle Konfiguration fuer den EEG-Arduino.
        car_serial: Serielle Konfiguration fuer den RC-Auto-Arduino.
            Wird nur noch benutzt, wenn der Auto-Arduino zum Testen per
            USB-Kabel angeschlossen ist (``MINDFLEX_CAR_TRANSPORT=serial``).
        eeg_ble: BLE-Konfiguration fuer den EEG-Arduino. Nur relevant,
            wenn das Headset per Funk angebunden ist
            (``MINDFLEX_EEG_TRANSPORT=ble``). Die Adresse muss ueber
            ``MINDFLEX_EEG_BLE_ADDRESS`` gesetzt werden, weil beide
            Funkmodule denselben Dienst bewerben und sonst nicht
            auseinanderzuhalten waeren.
        car_ble: BLE-Konfiguration fuer den RC-Auto-Arduino (Standardweg).
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
        labels: Vollstaendiges Vokabular aller Fahrbefehle, die das System
            kennt und an den Arduino senden kann. Bleibt absichtlich
            vollstaendig -- LEFT und RIGHT sind als Reserve erhalten, falls
            sich spaeter doch ein Weg findet, Richtungen zuverlaessig zu
            unterscheiden.
        active_labels: Teilmenge von ``labels``, die aktuell trainiert und
            vorhergesagt wird: stehenbleiben, geradeaus fahren sowie auf
            der Stelle nach links oder rechts drehen (Panzer-Lenkung).
            Damit laesst sich jede Richtung und jede Ausrichtung in der
            Ebene erreichen, ohne Rueckwaertsgang.

            Ob sich links und rechts aus dem Signal ueberhaupt trennen
            lassen, ist offen: Das MindFlex misst mit einer einzelnen
            Stirnelektrode (Fp1), waehrend der Unterschied zwischen
            Links- und Rechtsbewegung ueber dem motorischen Kortex
            (C3/C4) entsteht -- dort misst das Headset nicht. Die beiden
            Drehrichtungen sind deshalb bewusst als Versuch enthalten und
            koennen jederzeit ueber die Umgebungsvariable
            ``MINDFLEX_ACTIVE_LABELS`` reduziert werden, z. B. auf
            ``"STOP,FORWARD,LEFT"``.
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
    eeg_ble: BleConfig = field(
        default_factory=lambda: BleConfig(
            address=_env_str(
                "MINDFLEX_EEG_BLE_ADDRESS", "076EFF65-8F8C-7941-795A-11DB6D8CFE9C"
            ),
            resend_interval=0.0,  # der Empfaenger sendet nichts, nur lesen
        )
    )
    car_ble: BleConfig = field(
        default_factory=lambda: BleConfig(
            address=_env_str(
                "MINDFLEX_CAR_BLE_ADDRESS", "95F44EE1-426A-D939-25DD-1A3F14527B4F"
            ),
            resend_interval=_env_float("MINDFLEX_CAR_RESEND", 0.4),
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

    active_labels: tuple = field(
        default_factory=lambda: _env_labels(
            "MINDFLEX_ACTIVE_LABELS",
            ("STOP", "FORWARD", "BACKWARD"),
        )
    )

    # Nach so vielen Sekunden ohne neues Paket gilt das Signal als tot.
    #
    # Sicherheitsrelevant: Wird das Headset ausgeschaltet oder reisst die
    # Funkverbindung ab, kommen einfach keine Pakete mehr -- der zuletzt
    # empfangene Messwert bleibt dabei stehen und sieht weiterhin gueltig
    # aus. Ohne dieses Zeitlimit wuerde der Klassifikator auf veralteten
    # Daten weiter vorhersagen und das RC-Auto mit dem letzten Fahrbefehl
    # weiterfahren, bis der Akku leer ist.
    #
    # Das Headset liefert nominell ein Paket je Sekunde; drei Sekunden
    # lassen also Raum fuer einen Aussetzer, ohne gleich abzuschalten.
    signal_timeout_seconds: float = 3.0

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
