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


Configuration
-------------

Example configuration file::

    [credentials]
    email = ...

    [device]
    serial = ...

    [oauth]
    cookie = ...
    login_hint = ...
    client_id = ...

    [units]
    temperature = f


The ``oauth`` section values currently need to be obtained manually by using devtools in Chrome...  Steps:

- Log out of your Nest account in Chrome
- Open a new tab, open devtools (ctrl+shift+i), and go to the Network tab
- Go to ``home.nest.com``, click ``Sign in with Google``, and log in
- In devtools, filter to ``issueToken``, click the ``iframerpc`` row, and examine the ``Request URL``.  The ``login_hint`` and ``client_id`` values can be extracted from the query parameters

  - Note: This is slightly different from the config used by badnest
- Filter to ``oauth2/iframe`` and click the last ``iframe`` row.  The ``cookie`` value is the entire ``cookie`` value from the ``Request Headers`` for this row

Thanks to the `badnest <https://github.com/therealryanbonham/badnest>`_ project for the OAuth login info
