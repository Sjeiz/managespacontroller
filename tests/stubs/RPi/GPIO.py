"""Minimal stand-in for RPi.GPIO, so the controller runs without a Raspberry Pi.

Outputs are recorded in `pins` (pin -> level); inputs are read from `inputs`
(pin -> level, default 1). Tests set `inputs[pin]` to simulate a sensor.
"""

BCM = "BCM"
OUT = "OUT"
IN = "IN"
PUD_UP = "UP"
PUD_DOWN = "DOWN"
PUD_OFF = "OFF"

pins = {}
inputs = {}


def setmode(mode):
    pass


def setwarnings(flag):
    pass


def setup(pin, direction, initial=None, pull_up_down=None):
    if direction == OUT:
        pins[pin] = initial


def output(pin, value):
    pins[pin] = value


def input(pin):
    return inputs.get(pin, 1)
