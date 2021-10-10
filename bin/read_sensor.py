#!/usr/bin/env python

import logging
import sys
from pathlib import Path

sys.path.append(Path(__file__).resolve().parents[1].joinpath('lib').as_posix())
from ds_tools.argparsing.argparser import ArgParser
from ds_tools.core.main import wrap_main
from ds_tools.logging import init_logging
from rpi_hvac.rpi import Dht22Sensor
from rpi_hvac.utils import celsius_to_fahrenheit as c2f

log = logging.getLogger(__name__)


@wrap_main
def main():
    parser = ArgParser('Temp Sensor Reader')
    parser.add_argument('--max_retries', '-r', type=int, default=4, help='Maximum read retries allowed')
    parser.add_argument('--unit', '-u', choices=('f', 'c'), default='c', help='Temperature unit for output')
    parser.include_common_args('verbose')
    args = parser.parse_args()
    init_logging(args.verbose, names=None, log_path=None)

    sensor = Dht22Sensor(args.max_retries)
    humidity, temperature = sensor.read()
    unit = args.unit.upper()
    temperature = c2f(temperature) if unit == 'F' else temperature
    print(f'{humidity=:.2f}% {temperature=:.2f}\u00b0{unit}')


if __name__ == '__main__':
    main()
