"""
Library for interacting with the Nest thermostat via the cloud API

:author: Doug Skrypa
"""

import logging
import math

__all__ = ['celsius_to_fahrenheit', 'fahrenheit_to_celsius', 'estimate_ambient', 'secs_to_wall', 'wall_to_secs']
log = logging.getLogger(__name__)


def celsius_to_fahrenheit(deg_c):
    return (deg_c * 9 / 5) + 32


def fahrenheit_to_celsius(deg_f):
    return (deg_f - 32) * 5 / 9


def secs_to_wall(seconds: int):
    hour, minute = divmod(seconds // 60, 60)
    return f'{hour:02d}:{minute:02d}'


def wall_to_secs(wall: str):
    hour, minute = map(int, wall.split(':'))
    return (hour * 60 + minute) * 60


# ----------------------------------------------------------------------------------------------------------------------


def estimate_ambient(cpu_temp, sensor_temp, **kwargs):
    """
    :param float cpu_temp: The temperature of the CPU in degrees Celsius
    :param float sensor_temp: The temperature measured by the SenseHat's temperature sensor
    :param kwargs: Additional keyword args to pass to :func:`delta_from_cpu`
    :return float: The estimated ambient temperature
    """
    return round(sensor_temp - delta_from_cpu(cpu_temp, **kwargs), 1)


def delta_from_cpu(cpu_temp, k=0.49, x0=40.24, L=8.31, y0=1.15):
    """
    Estimate the ambient temperature sensor reading's delta from the CPU temperature based on the logistic function that
    seems to be the best match for the collected historical temperature data.  The best-fitting values have been set as
    defaults instead of hard-coded in case they need to change.

    Logistic function formula taken from https://en.wikipedia.org/wiki/Logistic_function

    :param float cpu_temp: The temperature of the CPU in degrees Celsius
    :param float k: The logistic growth rate or steepness of the curve
    :param float x0: The x-value (CPU) of the sigmoid's midpoint
    :param float L: The curve's maximum value (max sensor-true_ambient delta)
    :param float y0: The curve's minimum value (min sensor-true_ambient delta)
    :return float: The estimated delta between the sensor's reading and the true ambient temperature
    """
    return y0 + (L / (1 + math.e ** (-k * (cpu_temp - x0))))
