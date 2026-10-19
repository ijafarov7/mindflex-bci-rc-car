# Translated Graph class (simplified GUI controls via keyboard)
class Graph:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.pixelsPerSecond = 50
        self.gridColor = color(0)
        self.gridSeconds = 1
        self.scrollGrid = False
        self.originalW = w
        self.originalX = x
        self.renderModes = ["Lines", "Curves", "Shaded", "Triangles"]
        self.renderModeIndex = 0
        self.renderMode = self.renderModes[self.renderModeIndex]
        self.scaleModes = ["Local", "Global"]
        self.scaleModeIndex = 0

    def cycle_render_mode(self):
        self.renderModeIndex = (self.renderModeIndex + 1) % len(self.renderModes)
        self.renderMode = self.renderModes[self.renderModeIndex]

    def toggle_scale_mode(self):
        global scaleMode
        self.scaleModeIndex = (self.scaleModeIndex + 1) % len(self.scaleModes)
        scaleMode = self.scaleModes[self.scaleModeIndex]

    def update(self):
        # Smooth drawing kludge
        self.w = self.originalW
        self.x = self.originalX

        self.w += (self.pixelsPerSecond * 2)
        self.x -= self.pixelsPerSecond

        # Figure out the left and right time bounds of the graph
        self.rightTime = millis()
        self.leftTime = self.rightTime - ((self.w / float(self.pixelsPerSecond)) * 1000)

    def draw(self):
        pushMatrix()
        translate(self.x, self.y)

        # Background
        fill(220)
        rect(0, 0, self.w, self.h)

        # Draw grid
        strokeWeight(1)
        stroke(255)

        if self.scrollGrid:
            gridTime = (self.rightTime // (1000 * self.gridSeconds)) * (1000 * self.gridSeconds)
        else:
            gridTime = self.rightTime

        while gridTime >= self.leftTime:
            gridX = int(map(gridTime, self.leftTime, self.rightTime, 0, self.w))
            line(gridX, 0, gridX, self.h)
            gridTime -= int(1000 * self.gridSeconds)

        gridY = self.h
        while gridY >= 0:
            gridY -= self.pixelsPerSecond * self.gridSeconds
            line(0, gridY, self.w, gridY)

        # Draw each channel
        noFill()
        if self.renderMode in ["Shaded", "Triangles"]:
            noStroke()
        if self.renderMode in ["Curves", "Lines"]:
            strokeWeight(2)

        for i in range(len(channels)):
            thisChannel = channels[i]

            if thisChannel.graphMe:
                if self.renderMode in ["Lines", "Curves"]:
                    stroke(thisChannel.drawColor)
                if self.renderMode in ["Shaded", "Triangles"]:
                    noStroke()
                    fill(thisChannel.drawColor, 120)

                if self.renderMode == "Triangles":
                    beginShape(TRIANGLES)
                else:
                    beginShape()

                if self.renderMode in ["Curves", "Shaded"]:
                    vertex(0, self.h)

                for p in thisChannel.points:
                    if (p.time >= self.leftTime) and (p.time <= self.rightTime):
                        pointX = int(map(p.time, self.leftTime, self.rightTime, 0, self.w))

                        if (scaleMode == "Global") and (i > 2):
                            pointY = int(map(p.value, 0, globalMax, self.h, 0))
                        else:
                            # Avoid division by zero
                            minv = thisChannel.minValue
                            maxv = thisChannel.maxValue if thisChannel.maxValue != thisChannel.minValue else thisChannel.maxValue + 1
                            pointY = int(map(p.value, minv, maxv, self.h, 0))

                        if self.renderMode == "Curves":
                            curveVertex(pointX, pointY)
                        else:
                            vertex(pointX, pointY)

                if self.renderMode in ["Curves", "Shaded"]:
                    vertex(self.w, self.h)

                if self.renderMode in ["Lines", "Curves", "Triangles"]:
                    endShape()
                if self.renderMode == "Shaded":
                    endShape(CLOSE)

        popMatrix()

        # GUI background matte
        noStroke()
        fill(255, 150)
        rect(10, 10, 195, 81)

        # Draw simple status
        fill(0)
        textSize(12)
        textAlign(LEFT, TOP)
        text("Render: %s" % self.renderMode, 12, 12)
        text("Scale: %s" % scaleMode, 12, 28)
        text("Pixels/s: %d" % self.pixelsPerSecond, 12, 44)
