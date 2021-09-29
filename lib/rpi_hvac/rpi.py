"""
Utilities for working with the Raspberry Pi's SenseHat

:author: Doug Skrypa
"""

import array
import logging
import time

from adafruit_dht import DHT22
from board import D4  # noqa
try:
    from psutil import sensors_temperatures
except ImportError:
    sensors_temperatures = lambda: {}
try:
    from sense_hat import SenseHat
except ImportError:
    class SenseHat:
        pass

__all__ = ['EnvSensor', 'Dht22Sensor']
log = logging.getLogger(__name__)
READ_DELAY = 2


class Dht22Sensor:
    def __init__(self, max_retries: int = 4):
        self.sensor = DHT22(D4, False)
        self.max_retries = max_retries
        self._last = 0

    def measure(self) -> tuple[float, float]:
        to_wait = READ_DELAY - (time.monotonic() - self._last)
        if to_wait > 0:
            log.debug(f'Waiting {to_wait:.3f} s before reading sensor')
            time.sleep(to_wait)

        pulses = self.sensor._get_pulses_bitbang()
        self._last = time.monotonic()
        if len(pulses) < 10:  # Probably a connection issue
            raise SensorReadFailed('DHT sensor not found - check wiring')
        elif len(pulses) < 80:  # We got *some* data just not 81 bits
            raise SensorReadFailed(f'A full buffer was not returned - only received {len(pulses)} bits - try again')

        buf = array.array('B')
        for byte_start in range(0, 80, 16):
            buf.append(self.sensor._pulses_to_binary(pulses, byte_start, byte_start + 16))

        if sum(buf[0:4]) & 0xFF != buf[4]:
            raise SensorReadFailed('Checksum did not validate - try again')

        humidity = ((buf[0] << 8) | buf[1]) / 10
        # temperature is 2 bytes; MSB is sign, bits 0-14 are magnitude)
        temperature = (((buf[2] & 0x7F) << 8) | buf[3]) / 10
        if buf[2] & 0x80:
            temperature = -temperature

        if not 0 < humidity < 100:
            raise RuntimeError(f'Received implausible data ({temperature=}, {humidity=}) - try again')

        return humidity, temperature

    def read(self) -> tuple[float, float]:
        if (retries := self.max_retries) < 0:
            retries = 1

        while True:
            try:
                return self.measure()
            except SensorReadFailed:
                retries -= 1
                if retries <= 0:
                    raise

    def read_old(self) -> tuple[float, float]:
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
    def __init__(self):
        self._sh = SenseHat()
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
