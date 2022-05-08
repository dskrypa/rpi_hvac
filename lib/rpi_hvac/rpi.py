"""
Utilities for working with the Raspberry Pi's SenseHat

:author: Doug Skrypa
"""

import logging
from array import array
from time import sleep, monotonic

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

    def __init__(self, max_retries: int = 4, pin=D4):
        self.sensor = DHT22(pin, False)
        self.max_retries = max_retries
        self._last = 0
        # self._last_temp = None

    def measure(self) -> tuple[float, float]:
        """
        Mostly copied from :meth:`adafruit_dht.DHTBase.measure`.  Adds a delay instead of returning immediately if it is
        called before the read delay has elapsed.  Prevents returning stale data, and returns both humidity and
        temperature in the same call instead of storing them and returning nothing.
        """
        # last_measure = self._last
        to_wait = READ_DELAY - (monotonic() - self._last)
        if to_wait > 0:
            log.debug(f'Waiting {to_wait:.3f} s before reading sensor')
            sleep(to_wait)

        # pulses = self.sensor._get_pulses_bitbang()
        pulses = self._get_pulses_bitbang()
        log.debug(f'Pulses ({len(pulses)}): {pulses}')
        self._last = monotonic()
        if len(pulses) < 10:  # Probably a connection issue
            raise SensorReadFailed('DHT sensor not found - check wiring')
        elif len(pulses) < 80:  # We got *some* data just not 81 bits
            raise SensorReadFailed(f'A full buffer was not returned - only received {len(pulses)} bits - try again')

        buf = array('B')
        for byte_start in range(0, 80, 16):
            # buf.append(self.sensor._pulses_to_binary(pulses, byte_start, byte_start + 16))
            buf.append(self._pulses_to_binary(pulses, byte_start, byte_start + 16))

        log.debug(f'Converted buffer ({len(buf)}): {buf}')

        if sum(buf[0:4]) & 0xFF != buf[4]:
            raise SensorReadFailed('Checksum did not validate - try again')

        humidity = ((buf[0] << 8) | buf[1]) / 10
        # temperature is 2 bytes; MSB is sign, bits 0-14 are magnitude)
        temperature = (((buf[2] & 0x7F) << 8) | buf[3]) / 10
        if buf[2] & 0x80:
            temperature = -temperature

        if not (0 < humidity < 100 and 0 < temperature < 50):
            raise SensorReadFailed(f'Received implausible data ({temperature=}, {humidity=}) - try again')
        # if self._last_temp is not None:
        #     delta = abs(self._last_temp - temperature)
        #     if delta > 5 and (monotonic() - last_measure) < 180:
        #         raise SensorReadFailed(f'Received implausible data ({temperature=}, {humidity=}) - try again')
        #
        # self._last_temp = temperature

        return humidity, temperature

    def _get_pulses_bitbang(self) -> array:
        """
        _get_pulses implements the communication protocol for DHT11 and DHT22 type devices.  It sends a start signal
        of a specific length and listens and measures the return signal lengths.

        return pulses (array uint16) contains alternating high and low transition times starting with a low transition
        time.  Normally pulses will have 81 elements for the DHT11/22 type devices.
        """
        pulses = array('H')
        with DigitalInOut(self.sensor._pin) as dhtpin:
            # we will bitbang if no pulsein capability
            transitions = []
            # Signal by setting pin high, then low, and releasing
            dhtpin.direction = Direction.OUTPUT
            dhtpin.value = True
            sleep(0.1)
            dhtpin.value = False
            # Using the time to pull-down the line according to DHT Model
            sleep(self.sensor._trig_wait / 1_000_000)
            timestamp = monotonic()  # take timestamp
            dhtval = True  # start with dht pin true because its pulled up
            dhtpin.direction = Direction.INPUT

            try:
                dhtpin.pull = Pull.UP
            except NotImplementedError:
                # blinka.microcontroller.generic_linux.libgpiod_pin does not support internal pull resistors.
                dhtpin.pull = None

            while monotonic() - timestamp < 0.25:
                if dhtval != dhtpin.value:
                    dhtval = not dhtval  # we toggled
                    transitions.append(monotonic())  # save the timestamp

            log.debug(f'Transitions ({len(transitions)}): {transitions}')

            # convert transitions to microsecond delta pulses - use last 81 pulses:
            transition_start = max(1, len(transitions) - self.sensor._max_pulses)
            log.debug(f'Converting transitions to pulses with {transition_start=}')
            for i in range(transition_start, len(transitions)):
                pulses_micro_sec = int(1_000_000 * (transitions[i] - transitions[i - 1]))
                pulses.append(min(pulses_micro_sec, 65535))
        return pulses

    def _pulses_to_binary(self, pulses: array, start: int, stop: int) -> int:
        """
        Takes pulses, a list of transition times, and converts them to a 1's or 0's.  The pulses array contains the
        transition times. `pulses` starts with a low transition time followed by a high transition time, then a low
        followed by a high, and so on.  The low transition times are ignored.  Only the high transition times are used.
        If the high transition time is greater than __hiLevel, that counts as a bit=1, if the high transition time is
        less that __hiLevel, that counts as a bit=0.

        :param pulses: The pulses (transition times)
        :param start: the starting index in pulses to start converting
        :param stop: the index to convert up to, but not including
        :return: integer containing the converted 1 and 0 bits
        """
        high_level = 51
        binary = 0
        hi_sig = False
        for bit_idx in range(start, stop):
            if hi_sig:
                bit = 0
                if pulses[bit_idx] > high_level:
                    bit = 1
                binary = binary << 1 | bit

            hi_sig = not hi_sig

        log.debug(f'Converted pulses[{start}:{stop}]={pulses[start:stop]} to {binary:b} ({binary})')
        return binary

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
