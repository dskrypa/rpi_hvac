#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
from collections import defaultdict

sys.path.append(PROJECT_ROOT.joinpath('lib').as_posix())
from ds_tools.__version__ import __author_email__, __version__
from ds_tools.argparsing import ArgParser
from ds_tools.core import wrap_main
from ds_tools.logging import init_logging
from ds_tools.output import Printer, SimpleColumn, Table
from rpi_hvac.nest import NestWebClient, DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)
SHOW_ITEMS = ('energy', 'weather', 'buckets', 'bucket_names', 'schedule')


def parser():
    parser = ArgParser(description='Nest Thermostat Manager')

    status_parser = parser.add_subparser('action', 'status', 'Show current status')
    status_parser.add_argument('--format', '-f', default='yaml', choices=Printer.formats, help='Output format')
    status_parser.add_argument('--celsius', '-C', action='store_true', help='Output temperatures in Celsius (default: Fahrenheit)')

    temp_parser = parser.add_subparser('action', 'temp', 'Set a new temperature')
    temp_parser.add_argument('temp', type=float, help='The temperature to set')
    temp_parser.add_argument('unit', nargs='?', default='f', choices=('f', 'c'), help='Unit (Celsius or Fahrenheit)')

    range_parser = parser.add_subparser('action', 'range', 'Set a new temperature range')
    range_parser.add_argument('low', type=float, help='The low temperature to set')
    range_parser.add_argument('high', type=float, help='The high temperature to set')
    range_parser.add_argument('unit', nargs='?', default='f', choices=('f', 'c'), help='Unit (Celsius or Fahrenheit)')

    mode_parser = parser.add_subparser('action', 'mode', 'Change the current mode')
    mode_parser.add_argument('mode', choices=('cool', 'heat', 'range', 'off'), help='The mode to set')

    fan_parser = parser.add_subparser('action', 'fan', 'Turn the fan on or off')
    fan_parser.add_argument('state', choices=('on', 'off'), help='The fan state to change to')
    fan_parser.add_argument('--duration', '-d', type=int, default=1800, help='Time (in seconds) for the fan to run (ignored if setting state to off)')

    show_parser = parser.add_subparser('action', 'show', 'Show information')
    show_parser.add_argument('item', choices=SHOW_ITEMS, help='The information to show')
    show_parser.add_argument('buckets', nargs='*', help='The buckets to show (only applies to item=buckets)')
    show_parser.add_argument('--format', '-f', choices=Printer.formats, help='Output format')
    show_parser.add_argument('--unit', '-u', nargs='?', default='f', choices=('f', 'c'), help='Unit (Celsius or Fahrenheit) for functions that support it')

    parser.add_common_arg('--config', '-c', metavar='PATH', default=DEFAULT_CONFIG_PATH, help='Config file location')
    parser.add_common_arg('--reauth', '-A', action='store_true', help='Force re-authentication, even if a cached session exists')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args()
    init_logging(args.verbose, log_path=None, names_add=['rpi_hvac'])

    nest = NestWebClient(config_path=args.config, reauth=args.reauth)

    action = args.action
    if action == 'status':
        Printer(args.format).pprint(nest.get_state(fahrenheit=not args.celsius))
    elif action == 'temp':
        nest.set_temp(args.temp, unit=args.unit)
    elif action == 'range':
        nest.set_temp_range(args.low, args.high, unit=args.unit)
    elif action == 'mode':
        nest.set_mode(args.mode)
    elif action == 'fan':
        if args.state == 'on':
            nest.start_fan(args.duration)
        elif args.state == 'off':
            nest.stop_fan()
        else:
            raise ValueError(f'Unexpected {args.state=!r}')
    elif action == 'show':
        item = args.item
        if item == 'energy':
            data = nest.get_energy_usage_history()
        elif item == 'weather':
            data = nest.get_weather()
        elif item == 'buckets':
            data = nest.app_launch(args.buckets)
        elif item == 'bucket_names':
            data = nest.bucket_types
        elif item == 'schedule':
            return show_schedule(nest.get_schedule(args.unit), args.format or 'table')
        else:
            raise ValueError(f'Unexpected {item=!r}')

        Printer(args.format or 'yaml').pprint(data)
    else:
        raise ValueError(f'Unexpected {action=!r}')


def show_schedule(schedule, output_format):
    if output_format == 'table':
        times = set()
        rows = []
        for day, time_temp_map in schedule.items():
            times.update(time_temp_map)
            row = defaultdict(str)
            row.update(time_temp_map)
            row['Day'] = day
            rows.append(row)

        columns = [SimpleColumn('Day')]
        columns.extend(SimpleColumn(_time, ftype='.1f') for _time in sorted(times))
        table = Table(*columns, update_width=True)
        table.print_rows(rows)
    else:
        Printer(output_format).pprint(schedule, sort_keys=False)


if __name__ == '__main__':
    main()
