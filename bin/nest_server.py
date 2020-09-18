#!/usr/bin/env python

if __name__ == '__main__':
    from gevent import monkey
    monkey.patch_all()

import logging
import platform
import socket
import sys
from functools import cached_property
from pathlib import Path
from typing import Optional, Dict, Any, Union, List, Tuple

from flask import Flask, request, render_template, redirect, Response, url_for

from ds_tools.flasks.server import init_logging

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(BASE_DIR.joinpath('lib').as_posix())
from ds_tools.__version__ import __author_email__, __version__
from ds_tools.argparsing import ArgParser
from ds_tools.core import wrap_main
from rpi_hvac.constants import NEST_WHERE_MAP
from rpi_hvac.nest import NestWebClient, DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)
app = Flask(
    __name__,
    static_folder=BASE_DIR.joinpath('etc/static').as_posix(),
    template_folder=BASE_DIR.joinpath('etc/templates').as_posix()
)
app.config.update(PROPAGATE_EXCEPTIONS=True, JSONIFY_PRETTYPRINT_REGULAR=True)
nest = None  # type: Optional[NestWebClient]
static_info = None  # type: Optional[StaticInfo]


def parser():
    parser = ArgParser(description='Nest Thermostat Flask')

    parser.add_argument('--use_hostname', '-u', action='store_true', help='Use hostname instead of localhost/127.0.0.1')
    parser.add_argument('--port', '-p', type=int, default=10000, help='Port to use')

    parser.add_common_arg('--config', '-c', metavar='PATH', default=DEFAULT_CONFIG_PATH, help='Config file location')
    parser.add_common_arg('--reauth', '-A', action='store_true', help='Force re-authentication, even if a cached session exists')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args()
    init_logging(None, args.verbose, names_add=['rpi_hvac'])

    if platform.system() == 'Windows':
        from ds_tools.flasks.socketio_server import SocketIOServer as Server
    else:
        from ds_tools.flasks.gunicorn_server import GunicornServer as Server

    app.config['nest_config_path'] = args.config
    app.config['nest_reauth'] = args.reauth
    server = Server(app, args.port, socket.gethostname() if args.use_hostname else None)
    server.start_server()


@app.before_first_request
def init_nest():
    global nest, static_info
    nest = NestWebClient(config_path=app.config['nest_config_path'], reauth=app.config['nest_reauth'])
    static_info = StaticInfo()


class StaticInfo:
    @cached_property
    def device_info(self) -> Dict[str, Any]:
        return nest.app_launch(['device'])[nest.serial]['device']

    @cached_property
    def location(self) -> str:
        try:
            return NEST_WHERE_MAP[self.device_info['where_id']]
        except KeyError:
            return 'Unknown'


@app.route('/')
def root():
    status = nest.get_state()
    render_dict = {
        'location': static_info.location,
        'mode': status['current_schedule_mode'],
        'humidity': status['current_humidity'],
        'temperature': status['current_temperature'],
        'target': status['target_temperature'],
        'target_low': status['target_temperature_low'],
        'target_high': status['target_temperature_high'],
    }
    return render_template('nest.tmpl', **render_dict)


# TODO:
"""
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
"""


if __name__ == '__main__':
    main()
