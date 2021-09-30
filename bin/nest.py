#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('lib').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

from rpi_hvac.nest.cli import main


if __name__ == '__main__':
    main()
