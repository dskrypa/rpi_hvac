#!/usr/bin/env python
"""
Flask server for providing temperature and humidity info

:author: Doug Skrypa
"""

import argparse
import logging
import platform
import socket
import sys
import traceback
from pathlib import Path

import Adafruit_DHT as dht
from flask import Flask, request, Response, jsonify
from werkzeug.http import HTTP_STATUS_CODES as codes

flask_dir = Path(__file__).resolve().parent
sys.path.append(flask_dir.parents[1].as_posix())
from ds_tools.logging import init_logging

log = logging.getLogger(__name__)

socketio = None
shutdown_pw = None
stopped = False
server_port = None
app = Flask(__name__)
app.config.update(PROPAGATE_EXCEPTIONS=True, JSONIFY_PRETTYPRINT_REGULAR=True)


def main():
    parser = argparse.ArgumentParser('Temp Sensor Flask Server')
    parser.add_argument('--use_hostname', '-u', action='store_true', help='Use hostname instead of localhost/127.0.0.1')
    parser.add_argument('--port', '-p', type=int, help='Port to use', required=True)
    parser.add_argument('--verbose', '-v', action='count',
                        help='Print more verbose log info (may be specified multiple times to increase verbosity)')
    args = parser.parse_args()
    init_logging(args.verbose, names=None, log_path=None)

    flask_logger = logging.getLogger('flask.app')
    for handler in logging.getLogger().handlers:
        if handler.name == 'stderr':
            flask_logger.addHandler(handler)
            break

    if platform.system() == 'Windows':
        from ds_tools.flasks.socketio_server import SocketIOServer as Server
    else:
        from ds_tools.flasks.gunicorn_server import GunicornServer as Server

    host = socket.gethostname() if args.use_hostname else None
    server = Server(app, args.port, host)
    server.start_server()


@app.route('/read')
def read_sensors():
    humidity, temp = dht.read_retry(dht.DHT22, 4)
    return jsonify({'humidity': humidity, 'temperature': temp})


class ResponseException(Exception):
    def __init__(self, code, reason):
        super().__init__()
        self.code = code
        self.reason = reason
        if isinstance(reason, Exception):
            log.error(traceback.format_exc())
        log.error(self.reason)

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
