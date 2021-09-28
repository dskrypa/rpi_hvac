#!/usr/bin/env python

import platform
from pathlib import Path
from setuptools import setup

project_root = Path(__file__).resolve().parent

with project_root.joinpath('readme.rst').open('r', encoding='utf-8') as f:
    long_description = f.read()

about = {}
with project_root.joinpath('lib', 'rpi_hvac', '__version__.py').open('r', encoding='utf-8') as f:
    exec(f.read(), about)


requirements = [
    'ds_tools@ git+git://github.com/dskrypa/ds_tools',
    'requests_client@ git+git://github.com/dskrypa/requests_client',
    'tz_aware_dt @ git+git://github.com/dskrypa/tz_aware_dt',
    'adafruit-circuitpython-dht',
    'flask',
    'gevent',
    'keyring',
    'psutil',
    'requests',
    'werkzeug',
    'flask_socketio' if platform.system() == 'Windows' else 'gunicorn',
]


setup(
    name=about['__title__'],
    version=about['__version__'],
    author=about['__author__'],
    author_email=about['__author_email__'],
    description=about['__description__'],
    long_description=long_description,
    url=about['__url__'],
    project_urls={'Source': about['__url__']},
    packages=['lib/...'],
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
    ],
    python_requires='~=3.8',
    install_requires=requirements,
    extras_require={'sensehat': ['sense_hat'], 'DHT': ['Adafruit_DHT'], 'dev': ['pre-commit', 'ipython']},
)
