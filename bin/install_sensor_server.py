#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('lib').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
import os
from configparser import ConfigParser
from subprocess import check_call

from ds_tools.argparsing import ArgParser
from ds_tools.core.main import wrap_main
from ds_tools.logging import init_logging

log = logging.getLogger(__name__)


def parser():
    parser = ArgParser(description='Temperature Sensor Server Installer\n\nInstall temp_sensor.py via systemd')
    parser.add_argument('--name', '-n', default='temp_sensor_server', help='The name of the systemd service to create')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args()
    init_logging(args.verbose)

    systemd_dir = Path('/etc/systemd/system')
    if not systemd_dir.exists():
        raise RuntimeError(f'Could not find {systemd_dir.as_posix()} - {Path(__file__).name} only supports systemd')

    systemd_dir_stat = systemd_dir.stat()
    if systemd_dir_stat.st_uid != os.getuid() or systemd_dir_stat.st_gid != os.getgid():
        raise RuntimeError(f'You must run `sudo {Path(__file__).name}` to proceed')

    create_unit_file(args.name)
    enable_and_start(args.name)


def create_unit_file(name: str):
    config = ConfigParser()
    config.optionxform = str  # Preserve case

    server_path = PROJECT_ROOT.joinpath('bin', 'temp_sensor.sh')
    config['Unit'] = {'Description': 'Temperature & humidity sensor server', 'After': 'multi-user.target'}
    config['Service'] = {'Type': 'simple', 'ExecStart': f'/bin/bash {server_path.as_posix()}'}
    config['Install'] = {'WantedBy': 'multi-user.target'}

    unit_file_path = Path(f'/etc/systemd/system/{name}.service')
    if unit_file_path.exists():
        raise RuntimeError(f'Unit file {unit_file_path.as_posix()} already exists')

    log.info(f'Creating {unit_file_path.as_posix()}')
    with unit_file_path.open('w', encoding='utf-8') as f:
        config.write(f)
    unit_file_path.chmod(0o644)


def enable_and_start(name: str):
    log.info(f'Starting service={name}')
    check_call(['systemctl', 'start', name])
    log.info(f'Enabling service={name} to run at boot')
    check_call(['systemctl', 'enable', name])
    log.info(f'\n\nRun `sudo reboot` to reboot and ensure installation was successful')


if __name__ == '__main__':
    main()
