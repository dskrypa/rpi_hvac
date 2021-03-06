"""
Utilities for working with the Raspberry Pi's SenseHat

:author: Doug Skrypa
"""

import logging

try:
    from psutil import sensors_temperatures
except ImportError:
    sensors_temperatures = lambda: {}
try:
    from sense_hat import SenseHat
except ImportError:
    class SenseHat:
        pass

__all__ = ['EnvSensor']
log = logging.getLogger(__name__)


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

