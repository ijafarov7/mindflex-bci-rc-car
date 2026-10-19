/*
 * rc_car_test.ino
 * ================
 *
 * TEST-Sketch fuer die Fahrbefehle des BCI-Projekts.
 *
 * Zweck: pruefen, ob die Steuerzeichen 'F','B','L','R','S' tatsaechlich am
 * Arduino ankommen -- ohne Motortreiber, ohne Transistor, ohne Motoren.
 * Statt die Motoren zu bewegen, wird jeder empfangene Befehl im Seriellen
 * Monitor der Arduino IDE protokolliert.
 *
 * Zwei Befehlsquellen gleichzeitig:
 *   [USB] Serieller Monitor (Tastatur) oder das Python-Programm ueber Kabel
 *   [BT ] Bluetooth-Modul (HC-05 / HC-06) an SoftwareSerial
 *
 * WICHTIG (Arduino Duemilanove): Der ATmega328 hat nur EINE echte serielle
 * Schnittstelle, und die haengt an Pin 0/1 am USB-Chip. Das Bluetooth-Modul
 * darf deshalb NICHT an Pin 0/1, sonst blockiert es Upload und Monitor.
 * Es laeuft hier ueber SoftwareSerial auf Pin 10/11.
 *
 * Verkabelung Bluetooth (HC-05 / HC-06):
 *   Modul VCC -> Arduino 3,3V
 *   Modul GND -> Arduino GND
 *   Modul TXD -> Arduino Pin 10          (BT sendet -> Arduino empfaengt)
 *   Modul RXD -> Arduino Pin 11, direkt verbunden
 *
 * Fuer diesen Test allein ist RXD nicht zwingend noetig (der Arduino muss
 * nichts zum Handy zuruecksenden) -- im Zweifel RXD einfach offen lassen.
 */

#include <SoftwareSerial.h>

// --- Bluetooth (SoftwareSerial) ---
const int PIN_BT_RX = 10;   // Arduino empfaengt hier <- Modul TXD
const int PIN_BT_TX = 11;   // Arduino sendet hier   -> Modul RXD
const long BT_BAUD  = 9600; // HC-06 Werkseinstellung; HC-05 im Datenmodus meist auch 9600

SoftwareSerial bluetooth(PIN_BT_RX, PIN_BT_TX);

// --- Motor-Pins (wie im echten Sketch, hier nur zur Anzeige) ---
const int PIN_ENA = 9;
const int PIN_IN1 = 8;
const int PIN_IN2 = 7;
const int PIN_IN3 = 5;
const int PIN_IN4 = 4;
const int PIN_ENB = 6;

const int PIN_LED = 13;     // Onboard-LED: leuchtet, sobald ein Fahrbefehl aktiv ist

// --- Konfiguration ---
const int MOTOR_SPEED = 200;

// Sicherheits-Timeout wie im Original. Fuer den Test standardmaessig AUS,
// sonst faellt der Zustand staendig auf STOP zurueck, waehrend man tippt.
const bool          WATCHDOG_ENABLED    = false;
const unsigned long COMMAND_TIMEOUT_MS  = 1000;

const unsigned long STATUS_INTERVAL_MS  = 3000;  // wie oft eine Statuszeile kommt

// --- Laufzeit-Zustand ---
char          currentState   = 'S';
unsigned long lastCommandTime = 0;
unsigned long lastStatusTime  = 0;
unsigned long countUsb        = 0;   // gezaehlte Bytes von USB
unsigned long countBt         = 0;   // gezaehlte Bytes von Bluetooth
unsigned long countValid      = 0;   // davon gueltige Fahrbefehle
unsigned long countIgnored    = 0;   // davon ignorierte Zeichen

void setup() {
  Serial.begin(9600);
  bluetooth.begin(BT_BAUD);

  pinMode(PIN_ENA, OUTPUT);
  pinMode(PIN_IN1, OUTPUT);
  pinMode(PIN_IN2, OUTPUT);
  pinMode(PIN_IN3, OUTPUT);
  pinMode(PIN_IN4, OUTPUT);
  pinMode(PIN_ENB, OUTPUT);
  pinMode(PIN_LED, OUTPUT);

  applyState('S');
  lastCommandTime = millis();
  lastStatusTime  = millis();

  Serial.println();
  Serial.println(F("========================================"));
  Serial.println(F(" RC-Auto Befehlstest (ohne Motortreiber)"));
  Serial.println(F("========================================"));
  Serial.println(F("Befehle: F=vor  B=zurueck  L=links  R=rechts  S=stopp"));
  Serial.print  (F("Bluetooth: SoftwareSerial Pin "));
  Serial.print  (PIN_BT_RX);
  Serial.print  (F("(RX)/"));
  Serial.print  (PIN_BT_TX);
  Serial.print  (F("(TX) @ "));
  Serial.print  (BT_BAUD);
  Serial.println(F(" Baud"));
  Serial.print  (F("Watchdog: "));
  Serial.println(WATCHDOG_ENABLED ? F("AN") : F("AUS (Testmodus)"));
  Serial.println(F("Tippe unten einen Buchstaben ein und druecke Enter."));
  Serial.println(F("----------------------------------------"));
}

void loop() {
  // Quelle 1: USB / Serieller Monitor
  while (Serial.available() > 0) {
    countUsb++;
    handleByte(Serial.read(), "USB");
  }

  // Quelle 2: Bluetooth-Modul
  while (bluetooth.available() > 0) {
    countBt++;
    handleByte(bluetooth.read(), "BT ");
  }

  // Sicherheits-Timeout (im Testmodus abgeschaltet)
  if (WATCHDOG_ENABLED && currentState != 'S' &&
      millis() - lastCommandTime > COMMAND_TIMEOUT_MS) {
    Serial.println(F("[!] WATCHDOG: seit 1000 ms kein Befehl -> STOPP"));
    applyState('S');
  }

  // Regelmaessige Statuszeile, damit man sieht, dass der Sketch laeuft
  if (millis() - lastStatusTime > STATUS_INTERVAL_MS) {
    printStatus();
    lastStatusTime = millis();
  }
}

/**
 * Verarbeitet ein einzelnes empfangenes Byte und protokolliert es.
 *
 * @param raw    Das empfangene Byte.
 * @param source Kurzname der Quelle ("USB" oder "BT ").
 */
void handleByte(int raw, const char* source) {
  char c = (char) raw;

  // Python sendet Grossbuchstaben; beim Tippen im Monitor sind Kleinbuchstaben
  // die naheliegende Eingabe. Beides wird als derselbe Befehl akzeptiert.
  char cmd = toupper(c);

  Serial.print(F("["));
  Serial.print(millis());
  Serial.print(F(" ms]["));
  Serial.print(source);
  Serial.print(F("] empfangen: "));

  // Steuerzeichen wie \r und \n haben keine lesbare Darstellung
  if (raw >= 32 && raw <= 126) {
    Serial.print(F("'"));
    Serial.print(c);
    Serial.print(F("'"));
  } else if (raw == 10) {
    Serial.print(F("\\n"));
  } else if (raw == 13) {
    Serial.print(F("\\r"));
  } else {
    Serial.print(F("(nicht druckbar)"));
  }
  Serial.print(F("  ASCII="));
  Serial.print(raw);

  if (isCommand(cmd)) {
    countValid++;
    Serial.print(F("  -> "));
    Serial.println(commandName(cmd));
    applyState(cmd);
    lastCommandTime = millis();
    printPins();
  } else {
    countIgnored++;
    Serial.println(F("  -> kein Fahrbefehl, ignoriert"));
  }
}

/**
 * @return true, wenn das Zeichen einer der fuenf Fahrbefehle ist.
 */
bool isCommand(char c) {
  return c == 'F' || c == 'B' || c == 'L' || c == 'R' || c == 'S';
}

/**
 * @return Klartextname des Fahrbefehls.
 */
const __FlashStringHelper* commandName(char c) {
  switch (c) {
    case 'F': return F("VORWAERTS");
    case 'B': return F("RUECKWAERTS");
    case 'L': return F("LINKS DREHEN");
    case 'R': return F("RECHTS DREHEN");
    case 'S': return F("STOPP");
  }
  return F("?");
}

/**
 * Setzt die Motor-Pins so, wie es der echte Sketch tun wuerde.
 *
 * Ohne angeschlossenen Motortreiber passiert dabei physisch nichts ausser
 * dass die Pins ihren Pegel wechseln -- nachmessbar mit Multimeter oder
 * einer LED mit Vorwiderstand.
 */
void applyState(char c) {
  currentState = c;

  bool in1, in2, in3, in4;
  int  speed = MOTOR_SPEED;

  switch (c) {
    case 'F': in1 = HIGH; in2 = LOW;  in3 = HIGH; in4 = LOW;  break;
    case 'B': in1 = LOW;  in2 = HIGH; in3 = LOW;  in4 = HIGH; break;
    case 'L': in1 = LOW;  in2 = HIGH; in3 = HIGH; in4 = LOW;  break;
    case 'R': in1 = HIGH; in2 = LOW;  in3 = LOW;  in4 = HIGH; break;
    default:  in1 = LOW;  in2 = LOW;  in3 = LOW;  in4 = LOW;  speed = 0; break;
  }

  digitalWrite(PIN_IN1, in1);
  digitalWrite(PIN_IN2, in2);
  digitalWrite(PIN_IN3, in3);
  digitalWrite(PIN_IN4, in4);
  analogWrite(PIN_ENA, speed);
  analogWrite(PIN_ENB, speed);

  digitalWrite(PIN_LED, c == 'S' ? LOW : HIGH);
}

/**
 * Gibt die aktuellen Pegel der Motor-Pins aus.
 */
void printPins() {
  Serial.print(F("        Pins: IN1(8)="));
  Serial.print(digitalRead(PIN_IN1));
  Serial.print(F(" IN2(7)="));
  Serial.print(digitalRead(PIN_IN2));
  Serial.print(F(" IN3(5)="));
  Serial.print(digitalRead(PIN_IN3));
  Serial.print(F(" IN4(4)="));
  Serial.print(digitalRead(PIN_IN4));
  Serial.print(F(" | PWM="));
  Serial.print(currentState == 'S' ? 0 : MOTOR_SPEED);
  Serial.print(F(" | LED13="));
  Serial.println(currentState == 'S' ? F("aus") : F("an"));
}

/**
 * Gibt eine zusammenfassende Statuszeile aus.
 */
void printStatus() {
  Serial.print(F("--- Status: Zustand="));
  Serial.print(commandName(currentState));
  Serial.print(F(" | Bytes USB="));
  Serial.print(countUsb);
  Serial.print(F(" BT="));
  Serial.print(countBt);
  Serial.print(F(" | gueltig="));
  Serial.print(countValid);
  Serial.print(F(" ignoriert="));
  Serial.print(countIgnored);
  Serial.println(F(" ---"));
}
