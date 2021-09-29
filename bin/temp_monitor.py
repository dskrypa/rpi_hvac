#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
import time
from datetime import datetime, timedelta
from requests import Session, RequestException

sys.path.append(PROJECT_ROOT.joinpath('lib').as_posix())
from rpi_hvac.__version__ import __author_email__, __version__
from rpi_hvac.nest.client import NestWebClient
from tz_aware_dt.tz_aware_dt import TZ_LOCAL
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
    parser.add_argument('--force_check_freq', '-f', type=int, help='Frequency in minutes to force a Nest status check, even if temp is not increasing')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args(req_subparser_value=True)
    init_logging(args.verbose, names_add=['rpi_hvac'], entry_fmt=ENTRY_FMT_DETAILED)

    monitor = TempMonitor(
        args.server, args.delay, args.config, args.reauth, celsius=args.celsius, force_check_freq=args.force_check_freq
    )
    monitor.run()


class TempMonitor:
    def __init__(
        self,
        server: str,
        delay: int,
        nest_config,
        nest_reauth,
        nest_check_freq: int = 180,
        force_check_freq: int = None,
        celsius: bool = False,
    ):
        self.url = f'http://{server}/read'
        self.session = Session()
        self.nest = NestWebClient(config_path=nest_config, reauth=nest_reauth)
        self.nest_check_freq = timedelta(seconds=nest_check_freq)
        if force_check_freq and force_check_freq < 1:
            raise ValueError('--force_check_freq / -f must be a positive integer')
        self.force_check_freq = timedelta(minutes=force_check_freq) if force_check_freq else None
        self.last_temp = 100
        init_td = max(self.nest_check_freq, self.force_check_freq) if force_check_freq else self.nest_check_freq
        self.last_nest_check = datetime.now(TZ_LOCAL) - init_td - timedelta(seconds=1)
        self.delay = delay
        self.celsius = celsius
        self.nest_mode = None
        self.nest_running = False
        self.nest_current = None
        self.nest_target = None

    def read_sensor(self):
        log.debug(f'GET -> {self.url}')
        try:
            resp = self.session.get(self.url, timeout=10)
        except RequestException as e:
            raise ReadRequestError(f'Error reading temperature from server: {e}') from e
        else:
            log.debug(f'Response: {resp} - {resp.text}')
            if resp.ok:
                return resp.json()
            raise ReadRequestError(f'Error reading temperature from server: {resp} - {resp.text}')

    def update_nest_status(self):
        nest_status = self.nest.get_state(fahrenheit=not self.celsius)
        self.last_nest_check = datetime.now(TZ_LOCAL)
        self.nest_running = nest_status['fan_current_speed'] != 'off'
        self.nest_mode = nest_status['current_schedule_mode'].upper()
        self.nest_current = nest_status['current_temperature']
        self.nest_target = nest_status['target_temperature']

    def maybe_update_nest_status(self, increasing: bool):
        last_td = datetime.now(TZ_LOCAL) - self.last_nest_check
        forced_freq = self.force_check_freq
        # TODO: On dramatic change (i.e., AC just started), trigger update
        # TODO: Calculate rate of change; base "dramatic change" on empirical rate
        if (increasing and last_td >= self.nest_check_freq) or (forced_freq is not None and last_td >= forced_freq):
            self.update_nest_status()

    def _process_status(self, data):
        humidity, temp_c = data['humidity'], data['temperature']
        temp_f = temp_c * 9 / 5 + 32
        increasing = temp_c > self.last_temp
        self.last_temp = temp_c
        self.maybe_update_nest_status(increasing)
        message = f'{temp_f=:.2f} F / {temp_c=:.2f} C - {increasing=} | {humidity=:.2f}%'
        if self.nest_mode is not None:
            last = self.last_nest_check.strftime('%Y-%m-%d %H:%M:%S %Z')
            message += (
                f' | Nest mode={self.nest_mode} running={self.nest_running} current={self.nest_current:.2f}'
                f' target={self.nest_target:.2f} (last check: {last})'
            )
        else:
            message += ' | Nest mode=? running=? current=? target=? (last check: -)'

        log.info(message)

    def run(self):
        while True:
            try:
                data = self.read_sensor()
            except ReadRequestError as e:
                log.error(e)
            else:
                self._process_status(data)
            time.sleep(self.delay)


class ReadRequestError(Exception):
    """Exception to be raised when a read request could not be completed"""


if __name__ == '__main__':
    main()
