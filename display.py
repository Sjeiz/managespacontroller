"""LCD and buzzer. Gets its information from the Spa; runs in the main loop.

The LCD is optional: if it is missing or fails, the controller keeps running
without it and retries the connection periodically. The buzzer is a GPIO output
and does not depend on the LCD.
"""

import logging
import time

from spa import STANDBY

log = logging.getLogger(__name__)

RECONNECT_SECS = 60


class Display:
    def __init__(self, lcd_factory, buzzer, temperature_sensors, columns=20, lines=4):
        """lcd_factory: callable that creates and initialises the LCD (may raise)."""
        self._lcd_factory = lcd_factory
        self._buzzer = buzzer
        self._temperature_sensors = temperature_sensors[: lines - 1]
        self._columns = columns
        self._line_count = lines
        self._lcd = None
        self._reconnect_at = 0.0
        self._connect()

    def update(self, spa, now):
        """now: wall clock seconds, used for the buzzer pulse."""
        self._update_buzzer(spa.fault, now)
        if self._lcd is None:
            if time.monotonic() < self._reconnect_at:
                return
            self._connect()
            if self._lcd is None:
                return
        try:
            self._render(spa, now)
        except Exception as error:
            self._lost(error)

    def message(self, lines):
        """Show a fixed message, e.g. when the controller stops."""
        if self._lcd is None:
            return
        try:
            self._show()
            for line in range(self._line_count):
                self._print(line, lines[line] if line < len(lines) else "")
        except Exception as error:
            self._lost(error)

    def _connect(self):
        try:
            self._lcd = self._lcd_factory()
        except Exception as error:
            if self._reconnect_at == 0.0:
                log.warning("Display not available (%s); retrying every %d s", error, RECONNECT_SECS)
            self._lcd = None
            self._reconnect_at = time.monotonic() + RECONNECT_SECS
            return
        if self._reconnect_at != 0.0:
            log.info("Display connected")
        self._reconnect_at = 0.0
        self._lines = [None] * self._line_count  # what is currently on the LCD
        self._visible = True

    def _lost(self, error):
        log.warning("Display lost (%s); retrying every %d s", error, RECONNECT_SECS)
        self._lcd = None
        self._reconnect_at = time.monotonic() + RECONNECT_SECS

    def _render(self, spa, now):
        if spa.state == STANDBY and not spa.fault:
            self._hide()
            return
        self._show()
        if spa.fault:
            status = "WARNING: " + " ".join(spa.warnings())
        else:
            status = " ".join(o.short_name for o in spa.outputs.values() if o.is_on and o.short_name)
        # Blinking activity dot: shows the controller is alive
        activity = "*" if int(now) % 2 == 0 else " "
        self._print(0, status[: self._columns - 1].ljust(self._columns - 1) + activity)
        for line, sensor in enumerate(self._temperature_sensors, start=1):
            value = "-" if sensor.value is None else str(sensor.value)
            self._print_justified(line, sensor.name + ":", value)

    def _update_buzzer(self, fault, now):
        # Pulse 1 s on, 1 s off; never continuously on
        on = fault and int(now) % 2 == 0
        if on != self._buzzer.is_on:
            self._buzzer.write(on)

    def _print_justified(self, line, left, right):
        spaces = max(1, self._columns - len(left) - len(right))
        self._print(line, left + " " * spaces + right)

    def _print(self, line, text):
        text = text[: self._columns].ljust(self._columns)
        if self._lines[line] != text:
            self._lcd.printline(line, text)
            self._lines[line] = text

    def _show(self):
        if not self._visible:
            self._lcd.backlight()
            self._lcd.display()
            self._visible = True

    def _hide(self):
        if self._visible:
            self._lcd.noDisplay()
            self._lcd.noBacklight()
            self._visible = False
