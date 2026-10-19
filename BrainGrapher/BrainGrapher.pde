// Main controller / model file for the the Processing Brain Grapher.

// See README.markdown for more info.
// See http://frontiernerds.com/brain-hack for a tutorial on getting started with the Arduino Brain Library and this Processing Brain Grapher.

// Latest source code is on https://github.com/kitschpatrol/Processing-Brain-Grapher
// Created by Eric Mika in Fall 2010, updates Spring 2012 and again in early 2014.

import processing.serial.*;
import processing.net.*;
import controlP5.*;

ControlP5 controlP5;

// ---------------------------------------------------------------------
// Verbindungsart
// ---------------------------------------------------------------------
// true  = Daten kommen per Bluetooth Low Energy vom Funkmodul am Arduino.
//         Processing selbst kann kein BLE -- deshalb laeuft daneben das
//         Skript ble_bridge.py, das die Funkstrecke bedient und die
//         Zeilen ueber eine lokale Netzwerkverbindung durchreicht.
//         WICHTIG: Erst die Bruecke starten, dann diesen Sketch.
//
// false = Daten kommen wie frueher direkt ueber das USB-Kabel.
boolean USE_BLE = true;

// Muessen zu HOST und PORT in ble_bridge.py passen.
String BRIDGE_HOST = "127.0.0.1";
int BRIDGE_PORT = 5204;

// Nur fuer USE_BLE = false: Welcher serielle Anschluss? Beim Start wird
// die Liste ausgegeben, den passenden Index hier eintragen.
int SERIAL_INDEX = 0;

Serial serial;
Client bridge;

// Ueber das Netz kommen die Daten als Bytestrom an, nicht zeilenweise.
// Angefangene Zeilen werden hier gesammelt, bis ein Umbruch kommt.
String bridgeBuffer = "";

Channel[] channels = new Channel[11];
Monitor[] monitors = new Monitor[10];
Graph graph;
ConnectionLight connectionLight;

int packetCount = 0;
int globalMax = 0;
String scaleMode;

void setup() {
  // Set up window
  size(1024, 768);
  frameRate(60);
  smooth();
  surface.setTitle("Processing Brain Grapher");  

  if (USE_BLE) {
    // Verbindung zur BLE-Bruecke. Laeuft sie noch nicht, schlaegt das hier
    // fehl -- deshalb erst ble_bridge.py starten, dann diesen Sketch.
    println("Verbinde mit der BLE-Bruecke auf " + BRIDGE_HOST + ":" + BRIDGE_PORT + " ...");
    bridge = new Client(this, BRIDGE_HOST, BRIDGE_PORT);

    if (bridge.active()) {
      println("Verbunden. Warte auf Daten vom Headset.");
    }
    else {
      println("FEHLER: Keine Verbindung zur Bruecke.");
      println("Laeuft ble_bridge.py? Starte es in einem Terminal mit:");
      println("  python3 ble_bridge.py");
    }
  }
  else {
    // Alter Weg ueber das USB-Kabel.
    println("Find your Arduino in the list below, note its [index]:\n");

    for (int i = 0; i < Serial.list().length; i++) {
      println("[" + i + "] " + Serial.list()[i]);
    }

    serial = new Serial(this, Serial.list()[SERIAL_INDEX], 9600);
    serial.bufferUntil(10);
  }

  // Set up the ControlP5 knobs and dials
  controlP5 = new ControlP5(this);
  controlP5.setColorCaptionLabel(color(0));    
  controlP5.setColorBackground(color(0));
  controlP5.disableShortcuts(); 
  controlP5.setMoveable(false);

  // Create the channel objects
  channels[0] = new Channel("Signal Quality", color(0), "");
  channels[1] = new Channel("Attention", color(100), "");
  channels[2] = new Channel("Meditation", color(50), "");
  channels[3] = new Channel("Delta", color(219, 211, 42), "Dreamless Sleep");
  channels[4] = new Channel("Theta", color(245, 80, 71), "Drowsy");
  channels[5] = new Channel("Low Alpha", color(237, 0, 119), "Relaxed");
  channels[6] = new Channel("High Alpha", color(212, 0, 149), "Relaxed");
  channels[7] = new Channel("Low Beta", color(158, 18, 188), "Alert");
  channels[8] = new Channel("High Beta", color(116, 23, 190), "Alert");
  channels[9] = new Channel("Low Gamma", color(39, 25, 159), "Multi-sensory processing");
  channels[10] = new Channel("High Gamma", color(23, 26, 153), "???");

  // Manual override for a couple of limits.
  channels[0].minValue = 0;
  channels[0].maxValue = 200;
  channels[1].minValue = 0;
  channels[1].maxValue = 100;
  channels[2].minValue = 0;
  channels[2].maxValue = 100;
  channels[0].allowGlobal = false;
  channels[1].allowGlobal = false;
  channels[2].allowGlobal = false;

  // Set up the monitors, skip the signal quality
  for (int i = 0; i < monitors.length; i++) {
    monitors[i] = new Monitor(channels[i + 1], i * (width / 10), height / 2, width / 10, height / 2);
  }

  monitors[monitors.length - 1].w += width % monitors.length;

  // Set up the graph
  graph = new Graph(0, 0, width, height / 2);

  // Set yup the connection light
  connectionLight = new ConnectionLight(width - 140, 10, 20);
}

void draw() {
  // Bei BLE holen wir die Daten aktiv ab. Ueber das Kabel erledigt das
  // serialEvent() von selbst, sobald eine Zeile vollstaendig ist.
  if (USE_BLE) {
    readBridge();
  }

  // Keep track of global maxima
  if (scaleMode == "Global" && (channels.length > 3)) {
    for (int i = 3; i < channels.length; i++) {
      if (channels[i].maxValue > globalMax) globalMax = channels[i].maxValue;
    }
  }

  // Clear the background
  background(255);

  // Update and draw the main graph
  graph.update();
  graph.draw();

  // Update and draw the connection light
  connectionLight.update();
  connectionLight.draw();

  // Update and draw the monitors
  for (int i = 0; i < monitors.length; i++) {
    monitors[i].update();
    monitors[i].draw();
  }
}

// Holt neue Zeichen von der BLE-Bruecke und verarbeitet jede
// vollstaendige Zeile. Wird bei USE_BLE in jedem Frame aufgerufen.
void readBridge() {
  if (bridge == null || bridge.available() <= 0) return;

  bridgeBuffer += bridge.readString();

  // Es koennen mehrere Zeilen auf einmal angekommen sein, ebenso eine
  // angefangene. Die bleibt im Puffer, bis ihr Umbruch nachkommt.
  int nl;
  while ((nl = bridgeBuffer.indexOf('\n')) >= 0) {
    String line = bridgeBuffer.substring(0, nl).trim();
    bridgeBuffer = bridgeBuffer.substring(nl + 1);

    if (line.length() > 0) {
      processPacket(line);
    }
  }
}


void serialEvent(Serial p) {
  processPacket(p.readString().trim());
}


// Zerlegt eine CSV-Zeile und traegt die Werte in die Kanaele ein.
// Gemeinsam genutzt von beiden Verbindungsarten, damit das Datenformat
// nur an einer Stelle steht.
// See https://github.com/kitschpatrol/Arduino-Brain-Library/blob/master/README for information on the CSV packet format
void processPacket(String incomingString) {
  print("Received string: ");
  println(incomingString);

  String[] incomingValues = split(incomingString, ',');

  // Der Arduino sendet nicht nur EEG-Zeilen, sondern etwa auch
  // Batteriemeldungen im Format "BAT,8123,1". Die haben ebenfalls Kommas,
  // wuerden also die alte Pruefung auf "mehr als ein Wert" bestehen und
  // dann beim Umwandeln in Zahlen eine Exception werfen. Deshalb wird hier
  // auf die genaue Anzahl Kanaele geprueft und zusaetzlich abgesichert.
  if (incomingValues.length != channels.length) {
    return;
  }

  // Verify that the packet looks legit
  if (incomingValues.length > 1) {
    packetCount++;

    // Wait till the third packet or so to start recording to avoid initialization garbage.
    if (packetCount > 3) {

      // Eine kaputte Zeile -- etwa durch ein verlorenes Funkpaket -- darf
      // den Sketch nicht beenden. Sie wird verworfen, der naechste Wert
      // kommt eine Sekunde spaeter ohnehin.
      try {
        int signalQuality = Integer.parseInt(incomingValues[0].trim());

        for (int i = 0; i < incomingValues.length; i++) {
          int newValue = Integer.parseInt(incomingValues[i].trim());

          // Zero the EEG power values if we don't have a signal.
          // Can be useful to leave them in for development.
          if ((signalQuality == 200) && (i > 2)) {
            newValue = 0;
          }

          channels[i].addDataPoint(newValue);
        }
      }
      catch (NumberFormatException e) {
        println("Zeile verworfen (keine Zahlen): " + incomingString);
        packetCount--;
      }
    }
  }
}


// Utilities

// Extend Processing's built-in map() function to support the Long datatype
long mapLong(long x, long in_min, long in_max, long out_min, long out_max) { 
  return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

// Extend Processing's built-in constrain() function to support the Long datatype
long constrainLong(long value, long min_value, long max_value) {
  if (value > max_value) return max_value;
  if (value < min_value) return min_value;
  return value;
}
