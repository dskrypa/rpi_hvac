import logging
from pathlib import Path

from ..__version__ import __author_email__, __version__
from ds_tools.argparsing import ArgParser
from ds_tools.core.main import wrap_main
from ds_tools.logging import init_logging
from ds_tools.output import Printer, Table, colored, SimpleColumn

log = logging.getLogger(__name__)
SHOW_ITEMS = ('energy', 'weather', 'buckets', 'bucket_names', 'schedule')


def parser():
    parser = ArgParser(description='Nest Thermostat Manager')

    status_parser = parser.add_subparser('action', 'status', 'Show current status')
    status_parser.add_argument('--format', '-f', default='yaml', choices=Printer.formats, help='Output format')
    status_parser.add_argument('--details', '-d', action='store_true', help='Show more detailed information')

    temp_parser = parser.add_subparser('action', 'temp', 'Set a new temperature')
    temp_parser.add_argument('temp', type=float, help='The temperature to set')
    temp_parser.add_argument('--only_set', '-s', action='store_true', help='Only set the temperature - do not force it to run if the delta is < 0.5 degrees')

    range_parser = parser.add_subparser('action', 'range', 'Set a new temperature range')
    range_parser.add_argument('low', type=float, help='The low temperature to set')
    range_parser.add_argument('high', type=float, help='The high temperature to set')

    mode_parser = parser.add_subparser('action', 'mode', 'Change the current mode')
    mode_parser.add_argument('mode', choices=('cool', 'heat', 'range', 'off'), help='The mode to set')

    fan_parser = parser.add_subparser('action', 'fan', 'Turn the fan on or off')
    fan_parser.add_argument('state', choices=('on', 'off'), help='The fan state to change to')
    fan_parser.add_argument('--duration', '-d', type=int, default=1800, help='Time (in seconds) for the fan to run (ignored if setting state to off)')

    show_parser = parser.add_subparser('action', 'show', 'Show information')
    show_parser.add_argument('item', choices=SHOW_ITEMS, help='The information to show')
    show_parser.add_argument('buckets', nargs='*', help='The buckets to show (only applies to item=buckets)')
    show_parser.add_argument('--format', '-f', choices=Printer.formats, help='Output format')
    show_parser.add_argument('--raw', '-r', action='store_true', help='Show the full raw response instead of the processed response (only applies to item=buckets)')

    with parser.add_subparser('action', 'schedule', 'Update the schedule') as schd_parser:
        schd_add = schd_parser.add_subparser('sub_action', 'add', 'Add entries with the specified schedule')
        schd_add.add_argument('cron', help='Cron-format schedule to use')
        schd_add.add_argument('temp', type=float, help='The temperature to set at the specified time')

        schd_rem = schd_parser.add_subparser('sub_action', 'remove', 'Remove entries with the specified schedule')
        schd_rem.add_argument('cron', help='Cron-format schedule to use')
        schd_rem.add_constant('temp', None)

        schd_save = schd_parser.add_subparser('sub_action', 'save', 'Save the current schedule to a file')
        schd_save.add_argument('path', help='The path to a file in which the current schedule should be saved')
        schd_save.add_argument('--overwrite', '-W', action='store_true', help='Overwrite the file if it already exists')

        schd_load = schd_parser.add_subparser('sub_action', 'load', 'Load a schedule from a file')
        schd_load.add_argument('path', help='The path to a file containing the schedule that should be loaded')

        schd_show = schd_parser.add_subparser('sub_action', 'show', 'Show the current schedule')
        schd_show.add_argument('--format', '-f', choices=Printer.formats, help='Output format')

        schd_parser.add_common_arg('--dry_run', '-D', action='store_true', help='Print actions that would be taken instead of taking them')

    full_status_parser = parser.add_subparser('action', 'full_status', 'Show/save the full device+shared status')
    full_status_parser.add_argument('--path', '-p', help='Location to store status info')
    full_status_parser.add_argument('--diff', '-d', action='store_true', help='Print a diff of the current status compared to the previous most recent status')

    cfg_parser = parser.add_subparser('action', 'config', 'Manage configuration')
    cfg_show_parser = cfg_parser.add_subparser('sub_action', 'show', 'Show the config file contents')
    cfg_set_parser = cfg_parser.add_subparser('sub_action', 'set', 'Set configs')
    cfg_set_parser.add_argument('section', choices=('credentials', 'device', 'oauth', 'units'), help='The section to modify')
    cfg_set_parser.add_argument('key', help='The key within the specified section to modify')
    cfg_set_parser.add_argument('value', help='The new value for the specified section and key')

    parser.add_common_arg('--config', '-c', metavar='PATH', default='~/.config/nest.cfg', help='Config file location')
    parser.add_common_arg('--reauth', '-A', action='store_true', help='Force re-authentication, even if a cached session exists')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args(req_subparser_value=True)
    init_logging(args.verbose, log_path=None, names_add=['rpi_hvac'])

    from rpi_hvac.nest.client import NestWebClient
    from rpi_hvac.nest.schedule import NestSchedule
    nest = NestWebClient(config_path=args.config, reauth=args.reauth)

    if (action := args.action) == 'status':
        show_status(nest, args.details, args.format)
    elif action == 'temp':
        nest.set_temp(args.temp, force_run=not args.only_set)
    elif action == 'range':
        nest.set_temp_range(args.low, args.high)
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
        show_item(nest, args.item, args.format, args.buckets, args.raw)
    elif action == 'schedule':
        if args.sub_action in ('add', 'remove'):
            schedule = nest.get_schedule()
            schedule.update(args.cron, args.sub_action, args.temp, args.dry_run)
        elif args.sub_action == 'save':
            schedule = nest.get_schedule()
            schedule.save(args.path, args.overwrite, args.dry_run)
        elif args.sub_action == 'load':
            schedule = NestSchedule.from_file(nest, args.path)
            schedule.push(args.dry_run)
        elif args.sub_action == 'show':
            nest.get_schedule().print(args.format or 'table')
    elif action == 'full_status':
        show_full_status(nest, args.path, args.diff)
    elif action == 'config':
        if args.sub_action == 'show':
            log.warning(
                'WARNING: The [oauth] section contains credentials that should be kept secret - do not share this'
                ' output with anyone\n',
                extra={'color': 'red'},
            )
            with nest.config.path.open('r') as f:
                print(f.read())
        elif args.sub_action == 'set':
            nest.config.maybe_set(args.section, args.key, args.value)
        else:
            raise ValueError(f'Unexpected config sub-action={args.sub_action!r}')
    else:
        raise ValueError(f'Unexpected {action=!r}')


def show_status(nest, details: bool, out_fmt: str):
    status = nest.get_state()
    if details:
        Printer(out_fmt).pprint(status)
    else:
        mode = status['current_schedule_mode'].upper()
        tbl = Table(
            SimpleColumn('Humidity'),
            SimpleColumn('Mode', len(mode)),
            SimpleColumn('Fan', 7),
            SimpleColumn('Target', display=mode != 'RANGE'),
            SimpleColumn('Target (low)', display=mode == 'RANGE'),
            SimpleColumn('Target (high)', display=mode == 'RANGE'),
            SimpleColumn('Temperature'),
            fix_ansi_width=True,
        )

        """
        While heating:
            hvac_ac_state: false
            hvac_fan_state: true
            hvac_heater_state: true
        """

        current = status['current_temperature']
        target = status['target_temperature']
        target_lo = status['target_temperature_low']
        target_hi = status['target_temperature_high']
        status_table = {
            'Mode': colored(mode, 14 if mode == 'COOL' else 13 if mode == 'RANGE' else 9),
            'Humidity': status['current_humidity'],
            'Temperature': colored('{:>11.1f}'.format(current), 11),
            'Fan': colored('OFF', 8) if status['fan_current_speed'] == 'off' else colored('RUNNING', 10),
            'Target (low)': colored('{:>12.1f}'.format(target_lo), 14 if target_lo < current else 9),
            'Target (high)': colored('{:>13.1f}'.format(target_hi), 14 if target_hi < current else 9),
            'Target': colored('{:>6.1f}'.format(target), 14 if target < current else 9),
        }
        tbl.print_rows([status_table])


def show_item(nest, item: str, out_fmt: str = None, buckets=None, raw: bool = False):
    if item == 'energy':
        data = nest.get_energy_usage_history()
    elif item == 'weather':
        data = nest.get_weather()
    elif item == 'buckets':
        data = nest.session.app_launch(buckets).json() if raw else nest.app_launch(buckets)
    elif item == 'bucket_names':
        data = nest.bucket_types
    elif item == 'schedule':
        return nest.get_schedule().print(out_fmt or 'table')
    else:
        raise ValueError(f'Unexpected {item=!r}')

    Printer(out_fmt or 'yaml').pprint(data)


def show_full_status(nest, path: str = None, diff: bool = False):
    import json
    import time
    path = Path(path or '~/etc/nest/status').expanduser()
    if path.exists() and not path.is_dir():
        raise ValueError(f'Invalid {path=} - it must be a directory')
    elif not path.exists():
        path.mkdir(parents=True)

    data = nest.app_launch(['device', 'shared'])
    status_path = path.joinpath(f'status_{int(time.time())}.json')
    log.info(f'Saving status to {status_path}')
    with status_path.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=4, sort_keys=True)

    if diff:
        from ds_tools.utils.diff import cdiff
        latest = max((p for p in path.iterdir() if p != status_path), key=lambda p: p.stat().st_mtime)
        cdiff(latest.as_posix(), status_path.as_posix())
