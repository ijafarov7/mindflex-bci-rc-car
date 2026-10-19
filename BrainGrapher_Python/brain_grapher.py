# Translated main controller from BrainGrapher.pde to Processing.py (Python mode)
# This sketch is a functional translation that omits ControlP5 and provides
# simple keyboard controls and a mock-data generator. If you have a serial
# device, see README.md for how to enable serial input.

from channel import Channel
from monitor import Monitor
from graph import Graph
from connection_light import ConnectionLight
from point import Point

packetCount = 0
globalMax = 0
scaleMode = "Local"

channels = [None] * 11
monitors = []
graph = None
connectionLight = None

# Settings for mock data (used if no serial is configured)
USE_MOCK_DATA = True
MOCK_UPDATE_MS = 200
_last_mock_time = 0


def setup():
    global channels, monitors, graph, connectionLight
    size(1024, 768)
    frameRate(60)
    smooth()
    surface.setTitle("Processing Brain Grapher (Python)")

    # Create the channel objects
    channels[0] = Channel("Signal Quality", color(0), "")
    channels[1] = Channel("Attention", color(100), "")
    channels[2] = Channel("Meditation", color(50), "")
    channels[3] = Channel("Delta", color(219, 211, 42), "Dreamless Sleep")
    channels[4] = Channel("Theta", color(245, 80, 71), "Drowsy")
    channels[5] = Channel("Low Alpha", color(237, 0, 119), "Relaxed")
    channels[6] = Channel("High Alpha", color(212, 0, 149), "Relaxed")
    channels[7] = Channel("Low Beta", color(158, 18, 188), "Alert")
    channels[8] = Channel("High Beta", color(116, 23, 190), "Alert")
    channels[9] = Channel("Low Gamma", color(39, 25, 159), "Multi-sensory processing")
    channels[10] = Channel("High Gamma", color(23, 26, 153), "???")

    # Manual override for a couple of limits.
    channels[0].minValue = 0
    channels[0].maxValue = 200
    channels[1].minValue = 0
    channels[1].maxValue = 100
    channels[2].minValue = 0
    channels[2].maxValue = 100
    channels[0].allowGlobal = False
    channels[1].allowGlobal = False
    channels[2].allowGlobal = False

    # Set up the monitors, skip the signal quality
    for i in range(1, len(channels)):
        w_segment = width // 10
        m = Monitor(channels[i], (i - 1) * w_segment, height // 2, w_segment, height // 2)
        monitors.append(m)

    # adjust last monitor width
    monitors[-1].w += width % len(monitors)

    # Set up the graph
    graph = Graph(0, 0, width, height // 2)

    # Set up the connection light
    connectionLight = ConnectionLight(width - 140, 10, 20)


def draw():
    global globalMax, _last_mock_time, packetCount

    # Keep track of global maxima
    if scaleMode == "Global" and (len(channels) > 3):
        for i in range(3, len(channels)):
            if channels[i].maxValue > globalMax:
                globalMax = channels[i].maxValue

    # Clear the background
    background(255)

    # Update and draw the main graph
    graph.update()
    graph.draw()

    # Update and draw the connection light
    connectionLight.update()
    connectionLight.draw()

    # Update and draw the monitors
    for m in monitors:
        m.update()
        m.draw()

    # Mock data generator: produce a packet every MOCK_UPDATE_MS
    if USE_MOCK_DATA and (millis() - _last_mock_time > MOCK_UPDATE_MS):
        _last_mock_time = millis()
        packet = _generate_mock_packet()
        _handle_incoming_packet(packet)
        packetCount += 1


def _generate_mock_packet():
    # Produce 11 comma-separated integers similar to the Arduino Brain Library
    # Format: signalQuality, attention, meditation, delta, theta, lowAlpha, highAlpha, lowBeta, highBeta, lowGamma, highGamma
    import math
    t = millis() / 1000.0
    from random import randint
    signal = 0 if int(t) % 5 != 0 else 200
    attention = int((math.sin(t * 0.7) * 0.5 + 0.5) * 100)
    meditation = int((math.cos(t * 0.5) * 0.5 + 0.5) * 100)
    # EEG bands: small varying values
    bands = [int((math.sin(t * (0.3 + i * 0.1)) * 0.5 + 0.5) * (20 + i * 10)) for i in range(3, 11)]
    vals = [signal, attention, meditation] + bands
    return vals


def _handle_incoming_packet(values):
    # This mirrors serialEvent: values is a list of ints
    for i, v in enumerate(values):
        newValue = int(v)
        # Zero EEG power values if we don't have a signal
        if (int(values[0]) == 200) and (i > 2):
            newValue = 0
        channels[i].add_data_point(newValue)


def keyPressed():
    # Simple controls:
    # r -> cycle render mode
    # g -> toggle scale mode (Local/Global)
    # + / - -> change pixels per second
    global graph, scaleMode
    if key == 'r' or key == 'R':
        graph.cycle_render_mode()
    elif key == 'g' or key == 'G':
        graph.toggle_scale_mode()
    elif key == '+' or key == '=':
        graph.pixelsPerSecond = min(300, graph.pixelsPerSecond + 10)
    elif key == '-' or key == '_':
        graph.pixelsPerSecond = max(10, graph.pixelsPerSecond - 10)


# Map and constrain helpers (long support)
def mapLong(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / float(in_max - in_min) + out_min) if (in_max - in_min) != 0 else out_min


def constrainLong(value, min_value, max_value):
    return min(max(value, min_value), max_value)
