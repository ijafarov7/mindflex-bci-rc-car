/*
 * rc_car_4wd.ino
 * ===============
 *
 * Motorsteuerung des RC-Autos mit VIER Motoren ueber das
 * L293D-Motor-Shield (HW-130) und Bluetooth-Empfang.
 *
 * Panzer-Lenkung: Zum Drehen laeuft die eine Fahrzeugseite vorwaerts,
 * die andere rueckwaerts, das Auto dreht sich auf der Stelle.
 *
 * Befehle:
 *   'F' = alle vier Raeder vorwaerts
 *   'B' = alle vier Raeder rueckwaerts
 *   'L' = links rueckwaerts, rechts vorwaerts  -> dreht nach links
 *   'R' = links vorwaerts, rechts rueckwaerts  -> dreht nach rechts
 *   'S' = Stopp
 *
 * Zusaetzlich zum Einrichten (nur ueber den Seriellen Monitor gedacht):
 *   '1'..'4' = nur diesen einen Motor kurz vorwaerts laufen lassen
 *   'T'      = alle vier Motoren nacheinander testen
 *
 * ---------------------------------------------------------------------
 * DREHRICHTUNGEN EINSTELLEN
 * ---------------------------------------------------------------------
 * Motoren, die sich gegenueberliegen, sind spiegelverkehrt eingebaut --
 * dieselbe Polaritaet dreht sie also gegenlaeufig. Ausserdem kann jeder
 * Motor beim Anklemmen andersherum erwischt worden sein.
 *
 * Deshalb hat jeder Motor unten seinen eigenen Schalter in INVERT[].
 *
 * Stand 2026-09-17: Am realen Aufbau ausgemessen, zuletzt nach dem Tausch
 * der Anschluesse von M2 und M3. Alle vier Motoren liefen bei 'F'
 * gemeinsam rueckwaerts -- sie sind also einheitlich gepolt, nur eben
 * andersherum als der Sketch annimmt. Deshalb steht ueberall true. Beim
 * Umbau oder Neuanklemmen bitte erneut mit 'T' pruefen.
 *
 * Vorgehen:
 *
 *   1. Auto aufbocken, sodass die Raeder frei drehen
 *   2. Sketch hochladen, Seriellen Monitor oeffnen (9600 Baud)
 *   3. 'T' eingeben -- die vier Motoren laufen nacheinander je 1,5 s
 *   4. Notieren, welche Raeder sich dabei NICHT vorwaerts drehen
 *   5. Fuer genau diese Motoren unten den Wert auf true setzen
 *   6. Neu hochladen, 'T' wiederholen -- jetzt muessen alle vorwaerts drehen
 *
 * "Vorwaerts" heisst: Das Rad dreht sich so, dass das Auto nach vorne
 * fahren wuerde. Am besten von der Seite draufschauen.
 *
 * ---------------------------------------------------------------------
 * BENOETIGTE BIBLIOTHEK
 * ---------------------------------------------------------------------
 * "Adafruit Motor Shield library" Version 1 (AFMotor).
 *
 * ---------------------------------------------------------------------
 * MOTORANSCHLUESSE AM SHIELD
 * ---------------------------------------------------------------------
 *   M1 -> vorne rechts     M3 -> hinten rechts
 *   M2 -> vorne links      M4 -> hinten links
 *
 * (So ist es am realen Aufbau verkabelt, Stand 2026-08-15. Die Seiten
 * wechseln sich am Shield ab -- rechts haengt an M1 und M3, links an
 * M2 und M4.)
 *
 * ---------------------------------------------------------------------
 * BLUETOOTH-MODUL
 * ---------------------------------------------------------------------
 *   Modul VCC -> 3,3V         Modul TXD -> Pin 10
 *   Modul GND -> GND          Modul RXD -> Pin A0
 *
 * Alle Leitungen direkt verbunden, ohne Widerstaende.
 *
 * Der RXD-Pin wird nur fuer den Rueckkanal gebraucht: Ueber ihn meldet
 * der Arduino seinen Batteriestand zurueck an den Rechner.
 *
 * Ohne den Rueckkanal faehrt das Auto genauso -- dann bleibt RXD offen
 * und es kommt nur keine Batterieanzeige.
 *
 * ACHTUNG bei Pin 11: In vielen Anleitungen im Netz haengt der RXD-Pin des
 * Funkmoduls an Pin 11. Das geht hier NICHT, denn auf dem HW-130-Shield ist
 * Pin 11 der PWM-Ausgang von Motor 1. SoftwareSerial und die Motor-
 * bibliothek wuerden gleichzeitig auf denselben Pin schreiben.
 *
 * Pinbelegung des Shields zur Orientierung:
 *   3, 5, 6, 11  PWM fuer M2, M3, M4, M1
 *   4, 7, 8, 12  Schieberegister fuer alle Motoren
 *   9, 10        Servo-Anschluesse, frei solange kein Servo steckt
 *   2, 13, A0-A5 frei
 *
 * ---------------------------------------------------------------------
 * SICHERHEITSFUNKTION
 * ---------------------------------------------------------------------
 * Kommt laenger als COMMAND_TIMEOUT_MS kein Befehl an, stoppen die
 * Motoren. Die Python-Seite wiederholt den aktuellen Befehl alle 0,4
 * Sekunden, sodass das im Normalbetrieb nie zuschlaegt -- es greift nur,
 * wenn die Funkverbindung tatsaechlich abreisst.
 */

#include <AFMotor.h>
#include <SoftwareSerial.h>

// --- Bluetooth ---
const int PIN_BT_RX = 10;   // Arduino empfaengt hier <- Modul TXD
const int PIN_BT_TX = A0;   // Arduino sendet hier -> Modul RXD
                            // (NICHT Pin 11 -- das ist Motor 1)
const long BT_BAUD = 9600;

SoftwareSerial bluetooth(PIN_BT_RX, PIN_BT_TX);

// --- Motoren am Shield ---
AF_DCMotor motor1(1);   // vorne links
AF_DCMotor motor2(2);   // hinten links
AF_DCMotor motor3(3);   // vorne rechts
AF_DCMotor motor4(4);   // hinten rechts

AF_DCMotor* motors[4] = {&motor1, &motor2, &motor3, &motor4};

// Klartextnamen, nur fuer die Ausgabe im Seriellen Monitor.
const char* MOTOR_NAMES[4] = {
  "M1 vorne rechts", "M2 vorne links", "M3 hinten links", "M4 hinten rechts"
};

// Welche Seite ist welcher Motor? Wird nur fuers Drehen gebraucht -- auf
// Vorwaerts und Rueckwaerts hat es keinen Einfluss.
//
// Am realen Aufbau ausgemessen (2026-09-17, nach dem erneuten Umstecken).
// Rechts haengt an M1 und M4, links an M2 und M3. Die vorderen Raeder
// sitzen an M1/M2, die hinteren an M3/M4.
const bool IS_LEFT[4] = {false, true, true, false};

// ---------------------------------------------------------------------
// HIER die Drehrichtungen einstellen -- siehe Anleitung oben.
// true bedeutet: dieser Motor laeuft verkehrt herum und wird umgedreht.
// Startwert: die rechte Seite ist gespiegelt eingebaut, deshalb dort true.
// ---------------------------------------------------------------------
const bool INVERT[4] = {
  false,   // M1 vorne rechts   -- lief im Test rueckwaerts, deshalb gedreht
  true,    // M2 vorne links
  false,   // M3 hinten links   -- lief im Test rueckwaerts, deshalb gedreht
  true     // M4 hinten rechts
};

// --- Konfiguration ---
const int MOTOR_SPEED = 200;   // Geradeausfahrt, 0-255
const int TURN_SPEED  = 255;   // Drehen braucht mehr Kraft, weil die Raeder
                               // dabei seitlich ueber den Boden schleifen

const unsigned long COMMAND_TIMEOUT_MS = 1000;
const unsigned long TEST_RUN_MS = 1500;   // Laufzeit je Motor im Testmodus

const int PIN_LED = 13;        // leuchtet, solange ein Fahrbefehl aktiv ist

// --- Batterieanzeige (Rueckkanal zum Rechner) ---
//
// Ohne Zusatzteile misst der Chip seine eigene Betriebsspannung ueber die
// interne 1,1-V-Referenz. Das ist die geregelte 5-V-Schiene, nicht die
// Batterie -- der Wert faellt erst, wenn die Batterie so schwach wird,
// dass die Regelung zusammenbricht. Als Warnung taugt das, als
// Fuellstandsanzeige nicht.
//
// Mit Spannungsteiler an A1 wird die Batteriespannung direkt gemessen:
//
//   Vin --[ 100 kOhm ]--+-- A1
//                       |
//                   [ 47 kOhm ]
//                       |
//                      GND
const bool  SPANNUNGSTEILER_VORHANDEN = false;   // auf true, wenn verbaut
const int   PIN_BATTERIE = A1;
const float TEILER_FAKTOR = (100.0 + 47.0) / 47.0;
const int   DIODEN_OFFSET_MV = 400;              // Verpolungsschutz am Vin

const unsigned long BATTERIE_INTERVALL_MS = 5000;

unsigned long lastCommandTime = 0;
unsigned long letzteBatteriemeldung = 0;
char currentState = 'S';

void setup() {
  Serial.begin(9600);
  bluetooth.begin(BT_BAUD);

  pinMode(PIN_LED, OUTPUT);

  stopMotors();
  lastCommandTime = millis();

  Serial.println();
  Serial.println(F("RC-Auto 4WD bereit."));
  Serial.println(F("Fahren:  F=vor  B=zurueck  L=links  R=rechts  S=stopp"));
  Serial.println(F("Testen:  1-4 = einzelner Motor,  T = alle nacheinander"));
  Serial.println(F("--------------------------------------"));
}

void loop() {
  while (bluetooth.available() > 0) {
    handleByte(bluetooth.read(), "BT ");
  }

  while (Serial.available() > 0) {
    handleByte(Serial.read(), "USB");
  }

  if (currentState != 'S' && millis() - lastCommandTime > COMMAND_TIMEOUT_MS) {
    Serial.println(F("[!] Kein Befehl mehr empfangen -> STOPP"));
    stopMotors();
  }

  if (millis() - letzteBatteriemeldung >= BATTERIE_INTERVALL_MS) {
    letzteBatteriemeldung = millis();
    sendeBatteriestand();
  }
}

/**
 * Misst die Betriebsspannung des Chips ueber die interne 1,1-V-Referenz.
 *
 * @return Betriebsspannung in Millivolt.
 */
long messeVersorgung() {
  ADMUX = _BV(REFS0) | _BV(MUX3) | _BV(MUX2) | _BV(MUX1);
  delay(2);                                   // Referenz einschwingen lassen
  ADCSRA |= _BV(ADSC);
  while (bit_is_set(ADCSRA, ADSC)) {
    ;
  }
  long roh = ADCL | (ADCH << 8);
  if (roh == 0) {
    return 0;
  }
  return 1125300L / roh;
}

/**
 * Misst die Batteriespannung ueber den Spannungsteiler an A1.
 *
 * @return Batteriespannung in Millivolt.
 */
long messeBatterie() {
  long roh = analogRead(PIN_BATTERIE);
  long versorgung = messeVersorgung();
  long amPin = roh * versorgung / 1023;
  return (long) (amPin * TEILER_FAKTOR) + DIODEN_OFFSET_MV;
}

/**
 * Meldet den Batteriestand ueber Bluetooth an den Rechner.
 *
 * Format: BAT,<millivolt>,<modus>  --  modus 0 = Versorgung, 1 = Batterie.
 *
 * Waehrend SoftwareSerial sendet, sind die Interrupts kurz gesperrt, es
 * koennte also ein eingehendes Befehlszeichen verlorengehen. Das ist
 * unkritisch: Die Python-Seite wiederholt den aktuellen Fahrbefehl alle
 * 0,4 Sekunden, ein verlorenes Zeichen ist nach dem naechsten Takt
 * wieder ausgeglichen.
 */
void sendeBatteriestand() {
  long millivolt = SPANNUNGSTEILER_VORHANDEN ? messeBatterie() : messeVersorgung();

  bluetooth.print(F("BAT,"));
  bluetooth.print(millivolt);
  bluetooth.print(F(","));
  bluetooth.println(SPANNUNGSTEILER_VORHANDEN ? 1 : 0);

  // Zur Kontrolle auch ueber USB, falls der Monitor offen ist.
  Serial.print(F("[BAT] "));
  Serial.print(millivolt);
  Serial.println(F(" mV"));
}

/**
 * Verarbeitet ein empfangenes Byte.
 *
 * @param raw    Das empfangene Byte.
 * @param source Kurzname der Quelle, nur fuer die Ausgabe.
 */
void handleByte(int raw, const char* source) {
  char command = toupper((char) raw);

  // Testbefehle: einzelner Motor bzw. alle nacheinander
  if (command >= '1' && command <= '4') {
    testMotor(command - '1');
    return;
  }
  if (command == 'T') {
    testAll();
    return;
  }

  switch (command) {
    case 'F': driveForward();  break;
    case 'B': driveBackward(); break;
    case 'L': turnLeft();      break;
    case 'R': turnRight();     break;
    case 'S': stopMotors();    break;
    default:
      // Zeilenumbrueche und Stoerzeichen ignorieren, aber nicht stoppen.
      return;
  }

  lastCommandTime = millis();

  Serial.print(F("["));
  Serial.print(source);
  Serial.print(F("] "));
  Serial.println(command);
}

/**
 * Setzt einen einzelnen Motor.
 *
 * @param i         Index 0-3 (M1-M4).
 * @param vorwaerts True, wenn sich das Rad in Fahrtrichtung drehen soll.
 * @param speed     Geschwindigkeit 0-255.
 */
void setMotor(uint8_t i, bool vorwaerts, int speed) {
  // INVERT dreht die Bedeutung um, falls der Motor verkehrt angeklemmt ist.
  uint8_t richtung = (vorwaerts != INVERT[i]) ? FORWARD : BACKWARD;
  motors[i]->setSpeed(speed);
  motors[i]->run(richtung);
}

void driveForward() {
  currentState = 'F';
  for (uint8_t i = 0; i < 4; i++) setMotor(i, true, MOTOR_SPEED);
  digitalWrite(PIN_LED, HIGH);
}

void driveBackward() {
  currentState = 'B';
  for (uint8_t i = 0; i < 4; i++) setMotor(i, false, MOTOR_SPEED);
  digitalWrite(PIN_LED, HIGH);
}

void turnLeft() {
  // Linke Seite rueckwaerts, rechte vorwaerts -> Drehung nach links
  currentState = 'L';
  for (uint8_t i = 0; i < 4; i++) setMotor(i, !IS_LEFT[i], TURN_SPEED);
  digitalWrite(PIN_LED, HIGH);
}

void turnRight() {
  // Linke Seite vorwaerts, rechte rueckwaerts -> Drehung nach rechts
  currentState = 'R';
  for (uint8_t i = 0; i < 4; i++) setMotor(i, IS_LEFT[i], TURN_SPEED);
  digitalWrite(PIN_LED, HIGH);
}

void stopMotors() {
  currentState = 'S';
  for (uint8_t i = 0; i < 4; i++) motors[i]->run(RELEASE);
  digitalWrite(PIN_LED, LOW);
}

/**
 * Laesst einen einzelnen Motor kurz vorwaerts laufen.
 *
 * Zum Einrichten der Drehrichtungen: Man sieht sofort, welches Rad zu
 * welchem Anschluss gehoert und ob es richtig herum dreht.
 *
 * @param i Index 0-3 (M1-M4).
 */
void testMotor(uint8_t i) {
  if (i > 3) return;

  Serial.print(F("Test "));
  Serial.print(MOTOR_NAMES[i]);
  Serial.print(F("  (INVERT="));
  Serial.print(INVERT[i] ? F("true") : F("false"));
  Serial.println(F(") -> sollte VORWAERTS drehen"));

  stopMotors();
  setMotor(i, true, MOTOR_SPEED);
  delay(TEST_RUN_MS);
  stopMotors();

  lastCommandTime = millis();
}

/**
 * Testet alle vier Motoren nacheinander, mit Pause dazwischen.
 */
void testAll() {
  Serial.println(F("--- Motortest, alle vier nacheinander ---"));
  for (uint8_t i = 0; i < 4; i++) {
    testMotor(i);
    delay(700);
  }
  Serial.println(F("--- fertig. Welche Raeder liefen rueckwaerts? ---"));
  Serial.println(F("    Fuer die INVERT[] im Sketch umstellen."));
}
