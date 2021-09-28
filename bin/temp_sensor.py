#!/usr/bin/env python
"""
Flask server for providing temperature and humidity info

:author: Doug Skrypa
"""

if __name__ == '__main__':
    from gevent import monkey
    monkey.patch_all()

import argparse
import logging
import platform
import socket
import sys
from getpass import getuser
from pathlib import Path

from flask import Flask, jsonify
from werkzeug.http import HTTP_STATUS_CODES as codes

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(BASE_DIR.joinpath('lib').as_posix())
from ds_tools.logging import init_logging
from rpi_hvac.rpi import Dht22Sensor

log = logging.getLogger(__name__)

app = Flask(__name__)
app.config.update(PROPAGATE_EXCEPTIONS=True, JSONIFY_PRETTYPRINT_REGULAR=True)
SENSOR: Dht22Sensor = None  # noqa


def main():
    parser = argparse.ArgumentParser('Temp Sensor Flask Server')
    parser.add_argument('--use_hostname', '-u', action='store_true', help='Use hostname instead of localhost/127.0.0.1')
    parser.add_argument('--port', '-p', type=int, help='Port to use', required=True)
    parser.add_argument('--verbose', '-v', action='count', help='Print more verbose log info (may be specified multiple times to increase verbosity)')
    parser.add_argument('--max_retries', '-r', type=int, default=4, help='Maximum read retries allowed for a given request')
    args = parser.parse_args()
    init_logging(args.verbose or 2, names=None, log_path=None)

    if platform.system() == 'Windows':
        from ds_tools.flasks.socketio_server import SocketIOServer as Server
    else:
        from ds_tools.flasks.gunicorn_server import GunicornServer as Server

    global SENSOR
    SENSOR = Dht22Sensor(args.max_retries)

    log_cfg = {
        'log_path': f'/var/tmp/{getuser()}/temp_sensor_logs/server-{{pid}}.log',
        'verbose': args.verbose,
        'pid': True,
    }

    host = socket.gethostname() if args.use_hostname else None
    server = Server(app, args.port, host, log_cfg=log_cfg)
    server.start_server()


@app.route('/read')
def read_sensors():
    # humidity, temp = dht.read_retry(dht.DHT22, 4)
    humidity, temp = SENSOR.read()
    return jsonify({'humidity': humidity, 'temperature': temp})


class ResponseException(Exception):
    def __init__(self, code, reason):
        super().__init__()
        self.code = code
        self.reason = reason
        log.error(self.reason, exc_info=isinstance(reason, Exception))

    def __repr__(self):
        return '<{}({}, {!r})>'.format(type(self).__name__, self.code, self.reason)

    def __str__(self):
        return '{}: [{}] {}'.format(type(self).__name__, self.code, self.reason)

    def as_response(self):
        resp = jsonify({'error_code': codes[self.code], 'error': self.reason})
        resp.status_code = self.code
        return resp


@app.errorhandler(ResponseException)
def handle_response_exception(err):
    return err.as_response()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print()
