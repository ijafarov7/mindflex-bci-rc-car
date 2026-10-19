# Translated ConnectionLight class (simplified - no ControlP5)
class ConnectionLight:
    def __init__(self, x, y, diameter):
        self.x = x
        self.y = y
        self.diameter = diameter
        self.latestConnectionValue = 0
        self.currentColor = color(0)
        self.goodColor = color(0, 255, 0)
        self.badColor = color(255, 255, 0)
        self.noColor = color(255, 0, 0)

    def update(self):
        # Show red if no packets yet
        if len(channels[0].points) == 0:
            self.latestConnectionValue = 200
        else:
            self.latestConnectionValue = channels[0].get_latest_point().value

        if self.latestConnectionValue == 200:
            self.currentColor = self.noColor
        elif self.latestConnectionValue < 200:
            self.currentColor = self.badColor
        if self.latestConnectionValue == 0:
            self.currentColor = self.goodColor

    def draw(self):
        pushMatrix()
        translate(self.x, self.y)

        noStroke()
        fill(255, 150)
        rect(0, 0, 132, 50)

        noStroke()
        fill(self.currentColor)
        ellipseMode(CORNER)
        ellipse(5, 4, self.diameter, self.diameter)

        fill(0)
        textSize(12)
        textAlign(LEFT, TOP)
        text("CONNECTION QUALITY", 32, 11)
        textAlign(LEFT, TOP)
        text("PACKETS RECEIVED: " + str(packetCount), 5, 35)

        popMatrix()
