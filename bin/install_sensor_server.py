#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
from configparser import ConfigParser

sys.path.append(PROJECT_ROOT.joinpath('lib').as_posix())
from ds_tools.argparsing import ArgParser
from ds_tools.core.main import wrap_main
from ds_tools.logging import init_logging

log = logging.getLogger(__name__)


def parser():
    parser = ArgParser(description='Temperature Sensor Server Installer\n\nInstall temp_sensor.py via systemd')
    parser.include_common_args('verbosity')
    return parser


@wrap_main
def main():
    args = parser().parse_args()
    init_logging(args.verbose)


def create_unit_file():
    config = ConfigParser()
    config.optionxform = str  # Preserve case

    # for section in ('Unit', 'Service', 'Install'):
    #     config.add_section(section)

    server_path = PROJECT_ROOT.joinpath('bin', 'temp_sensor.py')


    config['Unit'] = {'Description': 'Temperature & humidity sensor server', 'After': 'multi-user.target'}
    config['Service'] = {'Type': 'simple', 'ExecStart': None}
    config['Install'] = {'WantedBy': 'multi-user.target'}



if __name__ == '__main__':
    main()
