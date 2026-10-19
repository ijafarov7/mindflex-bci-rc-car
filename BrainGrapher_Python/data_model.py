from collections import deque
import time

class Channel:
    def __init__(self, name, color=(0,0,0), maxlen_seconds=60):
        self.name = name
        self.color = color
        self.points = deque()
        self.maxlen_seconds = maxlen_seconds
        self.minValue = 0
        self.maxValue = 1
        self.allowGlobal = True

    def append_point(self, timestamp, value):
        self.points.append((timestamp, int(value)))
        # trim old points
        cutoff = timestamp - self.maxlen_seconds
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()

    def get_points_since(self, t0):
        xs = []
        ys = []
        for ts, v in self.points:
            if ts >= t0:
                xs.append(ts)
                ys.append(v)
        return xs, ys

    def get_latest_value(self):
        if not self.points:
            return None
        return self.points[-1][1]
