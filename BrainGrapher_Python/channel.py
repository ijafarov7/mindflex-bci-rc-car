# Translated Channel class
from point import Point

class Channel:
    def __init__(self, name, drawColor, description):
        self.name = name
        self.drawColor = drawColor
        self.description = description
        self.graphMe = True
        self.relative = False
        self.maxValue = 0
        self.minValue = 0
        self.points = []
        self.allowGlobal = True

    def add_data_point(self, value):
        time = millis()
        if value > self.maxValue:
            self.maxValue = value
        if value < self.minValue:
            self.minValue = value
        self.points.append(Point(time, int(value)))

    def get_latest_point(self):
        if len(self.points) > 0:
            return self.points[-1]
        else:
            return Point(0, 0)
