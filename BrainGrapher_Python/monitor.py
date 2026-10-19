# Translated Monitor class (simplified - no ControlP5)
class Monitor:
    def __init__(self, sourceChannel, x, y, w, h):
        self.sourceChannel = sourceChannel
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.currentValue = 0
        self.targetValue = 0
        self.backgroundColor = color(255)

    def update(self):
        # In the original this toggled graph visibility via a checkbox; keep always visible
        self.sourceChannel.graphMe = True

    def draw(self):
        pushMatrix()
        translate(self.x, self.y)

        # Background
        noStroke()
        fill(self.backgroundColor)
        rect(0, 0, self.w, self.h)

        # Border line
        strokeWeight(1)
        stroke(220)
        line(self.w - 1, 0, self.w - 1, self.h)

        # Bar graph
        if len(self.sourceChannel.points) > 0:
            targetPoint = self.sourceChannel.points[-1]
            minv = self.sourceChannel.minValue
            maxv = self.sourceChannel.maxValue if self.sourceChannel.maxValue != self.sourceChannel.minValue else self.sourceChannel.maxValue + 1
            self.targetValue = round(map(targetPoint.value, minv, maxv, 0, self.h))

            if (scaleMode == "Global") and self.sourceChannel.allowGlobal:
                self.targetValue = int(map(targetPoint.value, 0, globalMax, 0, self.h))

            # easing
            self.currentValue = int(self.currentValue + ((float(self.targetValue - self.currentValue) * .08)))

            # Bar
            noStroke()
            fill(self.sourceChannel.drawColor)
            rect(0, self.h - self.currentValue, self.w, self.h)

        # Draw the checkbox matte (visual only)
        noStroke()
        fill(240, 150)
        rect(10, 10, self.w - 20, 40)

        popMatrix()

        # Draw label
        fill(0)
        textSize(12)
        textAlign(LEFT, TOP)
        text(self.sourceChannel.name.upper(), self.x + 12, self.y + 15)
