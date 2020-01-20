
from pathlib import Path
from setuptools import setup

project_root = Path(__file__).resolve().parent

with project_root.joinpath('readme.rst').open('r', encoding='utf-8') as f:
    long_description = f.read()


setup(
    name='rpi_hvac',
    version='2020.01.20',
    author='Doug Skrypa',
    author_email='dskrypa@gmail.com',
    description='HVAC control via Raspberry Pi',
    long_description=long_description,
    url='https://github.com/dskrypa/rpi_hvac',
    packages=['lib/...'],
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8'
    ],
    python_requires='~=3.5',
    install_requires=[
        'requests_client @ git+git://github.com/dskrypa/requests_client',
        'tz_aware_dt @ git+git://github.com/dskrypa/tz_aware_dt',
        'keyring', 'psutil', 'eventlet', 'flask', 'flask_socketio', 'requests', 'werkzeug'
    ],
    extras_require={'sensehat': ['sense_hat'], 'DHT': ['Adafruit_DHT']}
)
