/*
 * mindflex_eeg.ino
 * =================
 *
 * Liest das MindFlex-EEG-Headset aus und gibt die Messwerte als
 * CSV-Zeile weiter -- wahlweise ueber das USB-Kabel oder ueber ein
 * BLE-Funkmodul.
 *
 * Laeuft auf dem Arduino Uno. Gegenstueck auf dem Rechner ist
 * serial_receiver.py (Kabel) bzw. ble_serial_receiver.py (Funk).
 *
 * Beruht auf dem Beispiel BrainSerialTest der Arduino-Brain-Bibliothek
 * von Eric Mika. Gegenueber dem Original ist nur die Ausgabe
 * konfigurierbar gemacht worden, damit sie sich fuer die Funkstrecke
 * aufraeumen laesst.
 *
 * ---------------------------------------------------------------------
 * WIE DIE DATEN FLIESSEN
 * ---------------------------------------------------------------------
 * Der ThinkGear-Chip im Headset sendet dauerhaft. Sein T-Pin haengt am
 * RX-Pin (Pin 0) des Arduino. Die Bibliothek zerlegt diesen Bytestrom
 * und legt die Werte als fertige CSV-Zeile auf den TX-Pin (Pin 1).
 *
 * Beide Richtungen laufen also ueber dieselbe Hardware-Schnittstelle,
 * stoeren sich aber nicht: Pin 0 ist Eingang, Pin 1 ist Ausgang.
 *
 * Ausgabeformat, elf durch Kommas getrennte Werte:
 *
 *   SignalQuality, Attention, Meditation, Delta, Theta,
 *   LowAlpha, HighAlpha, LowBeta, HighBeta, LowGamma, HighGamma
 *
 * SignalQuality laeuft nach ThinkGear-Konvention andersherum als man
 * erwartet: 0 bedeutet perfekten Kontakt, 200 gar keinen. Die Python-
 * Seite rechnet das in Prozent um.
 *
 * ---------------------------------------------------------------------
 * VARIANTE 1 -- UEBER USB-KABEL
 * ---------------------------------------------------------------------
 *   MindFlex T-Pin -> Pin 0 (RX)
 *   MindFlex GND   -> GND
 *   Arduino        -> USB-Kabel zum Rechner
 *
 * Starten mit:
 *   MINDFLEX_EEG_PORT=/dev/cu.usbmodemXXXX python3 main.py
 *
 * ---------------------------------------------------------------------
 * VARIANTE 2 -- UEBER BLUETOOTH
 * ---------------------------------------------------------------------
 * Zusaetzlich zur Verkabelung oben kommt ein BLE-Modul der HM-10-Familie
 * (CC2541) an den Sendepin:
 *
 *   Modul VCC -> 3,3V
 *   Modul GND -> GND
 *   Modul RXD -> Pin 1 (TX)
 *   Modul TXD -> nicht anschliessen
 *
 * Alle Leitungen direkt verbunden, ohne Widerstaende.
 *
 * Am Sketch aendert sich dafuer nichts: Das Modul hoert einfach mit,
 * was ohnehin auf Pin 1 hinausgeht. Der Serielle Monitor am USB-Kabel
 * funktioniert parallel weiter, weil Pin 1 ein Ausgang ist und beide
 * Empfaenger dieselben Bytes bekommen.
 *
 * Adresse des Moduls ermitteln (mit ausgeschaltetem RC-Auto, sonst sind
 * die beiden Module nicht auseinanderzuhalten):
 *   python3 ble_scan.py
 *
 * Starten mit:
 *   MINDFLEX_EEG_TRANSPORT=ble MINDFLEX_EEG_BLE_ADDRESS=<UUID> python3 main.py
 *
 * ---------------------------------------------------------------------
 * BEIM HOCHLADEN
 * ---------------------------------------------------------------------
 * Der ThinkGear-Chip sendet in Pin 0 hinein und stoert dadurch den
 * Upload. Vor dem Hochladen das Headset ausschalten oder die Leitung
 * am RX-Pin abziehen.
 */

// ---------------------------------------------------------------------
// BATTERIEANZEIGE
// ---------------------------------------------------------------------
// Es gibt zwei Betriebsarten, umschaltbar ueber SPANNUNGSTEILER_VORHANDEN.
//
// OHNE ZUSATZTEILE (Standard):
//   Der ATmega328 misst seine eigene Betriebsspannung ueber die interne
//   1,1-V-Referenz. Gemessen wird damit die geregelte 5-V-Schiene, nicht
//   die Batterie. Solange der Regler genug Eingangsspannung hat, stehen
//   dort stur 5,00 V -- der Wert faellt erst, wenn die Batterie unter
//   etwa 7 V kommt und die Regelung zusammenbricht. Das ist also eine
//   Warnlampe, kein Tankanzeiger.
//
// MIT SPANNUNGSTEILER (genauer):
//   Zwei Widerstaende von Vin auf einen Analogeingang messen die
//   Batteriespannung direkt:
//
//     Vin --[ 100 kOhm ]--+-- A1
//                         |
//                     [ 47 kOhm ]
//                         |
//                        GND
//
//   Bei 9,6 V liegen dann 3,07 V am Eingang, also sicher unter 5 V.
//   Achtung: Ueber Vin liegt noch die Verpolungsschutz-Diode des Arduino
//   dazwischen, die Messung faellt rund 0,4 V zu niedrig aus. Wer es
//   genau will, greift direkt an der Batterie ab oder rechnet den Wert
//   in DIODEN_OFFSET_MV heraus.

#include <Brain.h>

// Die Bibliothek liest den Chip auf derselben Hardware-Schnittstelle,
// auf der sie auch ausgibt.
Brain brain(Serial);

// Muss zur Baudrate in config.py passen (CONFIG.eeg_serial.baudrate).
const long BAUD = 9600;

// Die Bibliothek kann zusaetzlich eine Fehlerzeile ausgeben. Am Kabel
// stoert die nicht, ueber Funk verdoppelt sie die uebertragene Menge --
// und die Python-Seite verwirft sie ohnehin, weil sie nicht elf Werte
// enthaelt. Zum Suchen von Kontaktproblemen kurz auf true stellen und
// den Seriellen Monitor mitlesen.
const bool FEHLER_MITSENDEN = false;

// --- Batterieanzeige ---
const bool SPANNUNGSTEILER_VORHANDEN = false;  // auf true, wenn verbaut
const int  PIN_BATTERIE = A1;
const float TEILER_FAKTOR = (100.0 + 47.0) / 47.0;   // 100k / 47k
const int  DIODEN_OFFSET_MV = 400;                   // Verpolungsschutz am Vin

// Abstand zwischen zwei Batteriemeldungen. Die Spannung aendert sich
// langsam, oefter als alle paar Sekunden braucht es nicht.
const unsigned long BATTERIE_INTERVALL_MS = 5000;

unsigned long letzteBatteriemeldung = 0;

void setup() {
    Serial.begin(BAUD);
}

void loop() {
    // Liefert etwa einmal pro Sekunde true, wenn ein vollstaendiges
    // Paket vom Headset angekommen ist.
    if (brain.update()) {
        if (FEHLER_MITSENDEN) {
            Serial.println(brain.readErrors());
        }
        Serial.println(brain.readCSV());
    }

    if (millis() - letzteBatteriemeldung >= BATTERIE_INTERVALL_MS) {
        letzteBatteriemeldung = millis();
        sendeBatteriestand();
    }
}

/**
 * Misst die Betriebsspannung des Chips ueber die interne 1,1-V-Referenz.
 *
 * Braucht keinerlei zusaetzliche Bauteile.
 *
 * @return Betriebsspannung in Millivolt.
 */
long messeVersorgung() {
    ADMUX = _BV(REFS0) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
    delay(2);                                   // Referenz einschwingen lassen
    ADCSRA |= _BV(ADSC);
    while (bit_is_set(ADCSRA, ADSC)) {
        ;                                       // auf das Ergebnis warten
    }
    long roh = ADCL | (ADCH << 8);
    if (roh == 0) {
        return 0;
    }
    return 1125300L / roh;                      // 1,1 V * 1023 * 1000
}

/**
 * Misst die Batteriespannung ueber den Spannungsteiler an Vin.
 *
 * @return Batteriespannung in Millivolt.
 */
long messeBatterie() {
    long roh = analogRead(PIN_BATTERIE);
    long versorgung = messeVersorgung();
    // Der Analogeingang misst gegen die Betriebsspannung, deshalb wird
    // sie hier mit eingerechnet -- sonst waere die Messung falsch,
    // sobald die 5 V selbst nicht mehr genau stimmen.
    long amPin = roh * versorgung / 1023;
    return (long) (amPin * TEILER_FAKTOR) + DIODEN_OFFSET_MV;
}

/**
 * Schickt den Batteriestand als eigene Zeile zum Rechner.
 *
 * Format: BAT,<millivolt>,<modus>
 *   modus 0 = gemessene Betriebsspannung (ohne Spannungsteiler)
 *   modus 1 = gemessene Batteriespannung (mit Spannungsteiler)
 *
 * Die Zeile hat bewusst ein eigenes Format: Der EEG-Parser auf der
 * Gegenseite erwartet elf Zahlen und laesst alles andere liegen, die
 * Messdaten werden dadurch also nicht gestoert.
 */
void sendeBatteriestand() {
    long millivolt = SPANNUNGSTEILER_VORHANDEN ? messeBatterie() : messeVersorgung();
    Serial.print(F("BAT,"));
    Serial.print(millivolt);
    Serial.print(F(","));
    Serial.println(SPANNUNGSTEILER_VORHANDEN ? 1 : 0);
}
