"""
Utilities for working with the Raspberry Pi's SenseHat

:author: Doug Skrypa
"""

import logging
from array import array
from time import sleep, monotonic
from typing import Sequence

try:
    from itertools import pairwise
except ImportError:  # added in 3.10
    from itertools import tee

    def pairwise(iterable):
        a, b = tee(iterable)
        next(b, None)
        return zip(a, b)

from adafruit_dht import DHT22
from board import D4  # noqa
from digitalio import DigitalInOut, Pull, Direction
try:
    from psutil import sensors_temperatures
except ImportError:
    sensors_temperatures = lambda: {}  # noqa
try:
    from sense_hat import SenseHat
except ImportError:
    SenseHat = None

__all__ = ['EnvSensor', 'Dht22Sensor', 'SensorReadFailed']
log = logging.getLogger(__name__)
READ_DELAY = 2


class Dht22Sensor:
    """Represents an Adafruit DHT22 sensor"""

    def __init__(self, max_retries: int = 4, pin=D4, min_temp: float = 0, max_temp: float = 50):
        self.sensor = DHT22(pin, False)
        self.max_retries = max_retries
        self.min_temp = min_temp
        self.max_temp = max_temp
        self._last = 0

    def measure(self) -> tuple[float, float]:
        """
        Mostly copied from :meth:`adafruit_dht.DHTBase.measure`.  Adds a delay instead of returning immediately if it is
        called before the read delay has elapsed.  Prevents returning stale data, and returns both humidity and
        temperature in the same call instead of storing them and returning nothing.
        """
        to_wait = READ_DELAY - (monotonic() - self._last)
        if to_wait > 0:
            log.debug(f'Waiting {to_wait:.3f} s before reading sensor')
            sleep(to_wait)

        # pulses = self.sensor._get_pulses_bitbang()
        pulses = self._get_pulses_bitbang()
        # log.debug(f'Pulses ({len(pulses)}): {pulses}')
        self._last = monotonic()
        if len(pulses) < 10:  # Probably a connection issue
            raise SensorReadFailed('DHT sensor not found - check wiring')
        # elif len(pulses) < 80:  # We got *some* data just not 81 bits
        #     raise SensorReadFailed(f'A full buffer was not returned - only received {len(pulses)} bits - try again')

        buf = pulses_to_binary(pulses)
        # log.debug(f'Converted buffer ({len(buf)}): {buf}')
        if len(buf) < 5:
            raise SensorReadFailed(f'A full buffer was not returned - only received {len(pulses)} bits - try again')
        elif sum(buf[0:4]) & 0xFF != buf[4]:
            raise SensorReadFailed(f'Checksum did not validate - try again (received {len(pulses)} pulses)')

        humidity = ((buf[0] << 8) | buf[1]) / 10
        # temperature is 2 bytes; MSB is sign, bits 0-14 are magnitude)
        temperature = (((buf[2] & 0x7F) << 8) | buf[3]) / 10
        if buf[2] & 0x80:
            temperature = -temperature

        if not (0 < humidity < 100 and self.min_temp < temperature < self.max_temp):
            raise SensorReadFailed(f'Received implausible data ({temperature=}, {humidity=}) - try again')

        return humidity, temperature

    def _get_pulses_bitbang(self) -> array:
        """
        _get_pulses implements the communication protocol for DHT11 and DHT22 type devices.  It sends a start signal
        of a specific length and listens and measures the return signal lengths.

        return pulses (array uint16) contains alternating high and low transition times starting with a low transition
        time.  Normally pulses will have 81 elements for the DHT11/22 type devices.
        """
        trig_wait = self.sensor._trig_wait / 1_000_000
        with DigitalInOut(self.sensor._pin) as dht_pin:
            # transitions = []
            transitions = array('d')
            add_transition = transitions.append
            # Signal by setting pin high, then low, and releasing
            dht_pin.direction = Direction.OUTPUT

            dht_pin.value = True
            sleep(0.1)

            dht_pin.value = False
            sleep(trig_wait)  # Using the time to pull-down the line according to DHT Model

            dht_val = True  # start with dht pin true because its pulled up
            timestamp = monotonic()
            dht_pin.direction = Direction.INPUT
            try:
                dht_pin.pull = Pull.UP
            except NotImplementedError:
                # blinka.microcontroller.generic_linux.libgpiod_pin does not support internal pull resistors.
                dht_pin.pull = None

            ts = monotonic()
            while ts - timestamp < 0.25:
                if dht_val != dht_pin.value:
                    dht_val = not dht_val  # we toggled
                    ts = monotonic()
                    add_transition(ts)
                else:
                    ts = monotonic()

            # while monotonic() - timestamp < 0.25:
            #     if dht_val != dht_pin.value:
            #         dht_val = not dht_val  # we toggled
            #         add_transition(monotonic())  # save the timestamp

        log.debug(f'Transitions ({len(transitions)}): {transitions}')
        pulses = transitions_to_pulses(transitions)
        return pulses

    def read(self) -> tuple[float, float]:
        if (retries := self.max_retries) < 0:
            retries = 1

        while True:
            try:
                return self.measure()
            except SensorReadFailed as e:
                retries -= 1
                if retries <= 0:
                    raise
                log.debug(f'Retrying due read failure: {e}')

    def read_old(self) -> tuple[float, float]:
        """Old read method.  May return stale data."""
        if (retries := self.max_retries) < 0:
            retries = 1
        sensor = self.sensor
        while True:
            try:
                sensor.measure()
            except RuntimeError as e:
                retries -= 1
                if retries <= 0:
                    raise SensorReadFailed(f'Failed to read sensor - {e}')
            else:
                if sensor._humidity is None:
                    retries -= 1
                    if retries <= 0:
                        raise SensorReadFailed(f'Failed to read sensor - invalid values')
                return sensor._humidity, sensor._temperature


def pulses_to_binary(pulses: Sequence[int]) -> array:
    return array('B', (pulse_to_binary(pulses[i:i + 16]) for i in range(0, 80, 16)))


def pulse_to_binary(pulses: Sequence[int]) -> int:
    b = 0
    for pulse in pulses[1::2]:
        b = b << 1 | (pulse > 51)
    return b


def transitions_to_pulses(transitions: Sequence[float], max_pulses: int = 81) -> array:
    start = max(0, len(transitions) - max_pulses - 1)
    log.debug(f'Converting transitions to pulses with {start=}')
    return array('H', (min(int(1_000_000 * (b - a)), 65535) for a, b in pairwise(transitions[start:])))


class EnvSensor:
    """Represents the temperature/humidity sensors in a Sense Hat"""

    def __init__(self):
        if SenseHat is None:
            raise RuntimeError('Missing sense_hat dependency')
        self._sh = SenseHat()  # noqa
        self.get_humidity = self._sh.get_humidity   # Relative humidity (%)
        self.get_pressure = self._sh.get_pressure   # Pressure in Millibars

    def get_temps(self):
        cpu_temp = sensors_temperatures()['cpu-thermal'][0].current
        sh = self._sh
        temp_a = sh.get_temperature()
        temp_b = sh.get_temperature_from_humidity()
        temp_c = sh.get_temperature_from_pressure()
        return cpu_temp, temp_a, temp_b, temp_c

    def get_temperature(self):
        try:
            cpu_temp = sensors_temperatures()['cpu-thermal'][0].current
        except (KeyError, IndexError, AttributeError):
            cpu_temp = None

        sh = self._sh
        temp_a = sh.get_temperature()
        temp_b = sh.get_temperature_from_humidity()
        temp_c = sh.get_temperature_from_pressure()
        log.debug(f'Temps: cpu={cpu_temp} temp={temp_a} from_humidity={temp_b} from_pressure={temp_c}')
        return (temp_a + temp_b + temp_c) / 3


class SensorReadFailed(Exception):
    """Exception to be raised when an attempt to read a given sensor fails"""
