#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging

sys.path.append(PROJECT_ROOT.joinpath('lib').as_posix())
from ds_tools.__version__ import __author_email__, __version__
from ds_tools.argparsing import ArgParser
from ds_tools.core import wrap_main
from ds_tools.logging import init_logging
from ds_tools.output import Printer
from rpi_hvac.nest import NestWebClient, DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)


def parser():
    parser = ArgParser(description='Nest Thermostat Manager')

    status_parser = parser.add_subparser('action', 'status', 'Show current status')
    status_parser.add_argument('--format', '-f', default='yaml', choices=Printer.formats, help='Output format')
    status_parser.add_argument('--celsius', '-C', action='store_true', help='Output temperatures in Celsius (default: Fahrenheit)')

    set_parser = parser.add_subparser('action', 'set', 'Set a new temperature')
    set_parser.add_argument('temp', type=float, help='The temperature to set')
    set_parser.add_argument('unit', nargs='?', default='f', choices=('f', 'c'), help='Unit (Celsius or Fahrenheit)')
    set_parser.add_argument('--mode', '-m', choices=('cool', 'heat', 'range', 'off'), help='Change the current mode')

    fan_parser = parser.add_subparser('action', 'fan', 'Turn the fan on or off')
    fan_parser.add_argument('state', choices=('on', 'off'), help='The fan state to change to')
    fan_parser.add_argument('--duration', '-d', type=int, default=1800, help='Time (in seconds) for the fan to run (ignored if setting state to off)')

    parser.add_common_arg('--config', '-c', metavar='PATH', default=DEFAULT_CONFIG_PATH, help='Config file location')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args()
    init_logging(args.verbose, log_path=None)

    nest = NestWebClient(config_path=args.config)

    action = args.action
    if action == 'status':
        Printer(args.format).pprint(nest.get_state(fahrenheit=not args.celsius))
    elif action == 'set':
        if args.mode:
            nest.set_mode(args.mode)
        nest.set_temp(args.temp, unit=args.unit)
    elif action == 'fan':
        if args.state == 'on':
            nest.start_fan(args.duration)
        elif args.state == 'off':
            nest.stop_fan()
        else:
            raise ValueError(f'Unexpected {args.state=!r}')
    else:
        raise ValueError(f'Unexpected {action=!r}')


if __name__ == '__main__':
    main()
