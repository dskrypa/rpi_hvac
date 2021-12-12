HVAC Control via Raspberry Pi
=============================

Utilities for displaying temperature/humidity/etc information on a Raspberry Pi, and for controlling a Nest thermostat
remotely from it.


Installation
------------

To add rpi_hvac as a dependency, add the following to requirements.txt or ``install_requires`` in ``setup.py``::

    rpi_hvac@ git+git://github.com/dskrypa/rpi_hvac


To install it directly, use the following::

    $ pip install git+git://github.com/dskrypa/rpi_hvac


Alternatively, you can also do::

    $ git clone https://github.com/dskrypa/rpi_hvac.git
    $ cd rpi_hvac
    $ python -m venv venv
    $ . venv/bin/activate  # note: venv/Scripts/activate on Windows
    $ pip install -e .

