"""
Library for interacting with the Nest thermostat via the cloud API

:author: Doug Skrypa
"""

import logging
import math

__all__ = ['celsius_to_fahrenheit', 'fahrenheit_to_celsius', 'estimate_ambient', 'get_input']
log = logging.getLogger(__name__)


def celsius_to_fahrenheit(deg_c):
    return (deg_c * 9 / 5) + 32


def fahrenheit_to_celsius(deg_f):
    return (deg_f - 32) * 5 / 9


def get_input(prompt):
    """
    Prompt the user for input, and parse the results.

    :param str prompt: The prompt for user input
    :return bool: True if the user entered ``Y``, False if the user entered ``N``
    :raises: ValueError if the user did not enter a string that began with ``Y`` / ``N``
    """
    suffix = ' ' if not prompt.endswith(' ') else ''
    try:
        user_input = input(prompt + suffix).strip()
    except EOFError as e:
        raise ValueError('Unable to read stdin (this is often caused by piped input)') from e
    else:
        try:
            first_char = user_input[0].upper()
        except IndexError as e:
            raise ValueError('No input was provided') from e
        else:
            if first_char in ('Y', 'N'):
                return first_char == 'Y'
    raise ValueError('Expected "yes"/"y" or "no"/"n"')


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
