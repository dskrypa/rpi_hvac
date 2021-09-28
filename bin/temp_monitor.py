#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
import time
from requests import Session

sys.path.append(PROJECT_ROOT.joinpath('lib').as_posix())
from rpi_hvac.__version__ import __author_email__, __version__
from ds_tools.argparsing import ArgParser
from ds_tools.core.main import wrap_main
from ds_tools.logging import init_logging, ENTRY_FMT_DETAILED

log = logging.getLogger(__name__)


def parser():
    parser = ArgParser(description='Temperature Monitor / Nest Controller')
    parser.add_argument('server', metavar='HOST:PORT', help='The host:port that is running temp_sensor.py')
    parser.add_argument('--delay', '-d', type=int, default=15, help='Delay between checks')
    parser.add_argument('--celsius', '-C', action='store_true', help='Output temperatures in Celsius (default: Fahrenheit)')
    parser.add_argument('--config', '-c', metavar='PATH', default='~/.config/nest.cfg', help='Config file location')
    parser.add_argument('--reauth', '-A', action='store_true', help='Force re-authentication, even if a cached session exists')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args(req_subparser_value=True)
    init_logging(args.verbose, names_add=['rpi_hvac'], entry_fmt=ENTRY_FMT_DETAILED)

    from rpi_hvac.nest.client import NestWebClient
    nest = NestWebClient(config_path=args.config, reauth=args.reauth)

    delay = args.delay
    url = f'http://{args.server}/read'
    session = Session()
    last_temp = 100
    last_nest_check = 0
    nest_check_freq = 180  # 3 minutes
    # nest_status = None
    mode, running, current, target = None, False, None, None
    while True:
        log.debug(f'GET -> {url}')
        resp = session.get(url)
        log.debug(f'Response: {resp} - {resp.text}')
        if not resp.ok:
            log.error(f'Error reading temperature from server: {resp} - {resp.text}')
        else:
            data = resp.json()
            humidity, temp_c = data['humidity'], data['temperature']
            temp_f = temp_c * 9/5 + 32
            increasing = temp_c > last_temp
            last_temp = temp_c

            next_nest_check = last_nest_check + nest_check_freq - time.monotonic()
            if increasing and next_nest_check <= 0:
                next_nest_check = 'updated'
                last_nest_check = time.monotonic()
                nest_status = nest.get_state(fahrenheit=not args.celsius)
                running = nest_status['fan_current_speed'] == 'off'
                mode = nest_status['current_schedule_mode'].upper()
                current = nest_status['current_temperature']
                target = nest_status['target_temperature']

            message = f'{temp_f=:.2f} F / {temp_c=:.2f} C - {increasing=} | {humidity=:.2f}%'
            if mode is not None:
                message += f' | Nest {mode=} {running=} {current=:.2f} {target=:.2f} (next check in: {next_nest_check} s)'
            else:
                message += ' | Nest mode=? running=? current=? target=? (next check in: ? s)'

            log.info(message)

        time.sleep(delay)


if __name__ == '__main__':
    main()
