HVAC Control via Raspberry Pi
=============================

Utilities for displaying temperature/humidity/etc information on a Raspberry Pi, and for controlling a Nest thermostat
remotely from it.


Installation
------------

If installing on Linux, you should run the following first::

    $ sudo apt-get install python3-dev


Regardless of OS, setuptools is required::

    $ pip3 install setuptools


All of the other requirements are handled in setup.py, which will be run when you install like this::

    $ pip3 install git+git://github.com/dskrypa/rpi_hvac
