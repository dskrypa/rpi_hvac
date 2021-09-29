"""
Nest config

:author: Doug Skrypa
"""

import logging
from configparser import ConfigParser, NoSectionError
from functools import cached_property
from pathlib import Path
from typing import Optional, Mapping

try:
    import keyring
except ImportError:
    keyring = None

from ds_tools.input import get_input

__all__ = ['NestConfig', 'DEFAULT_CONFIG_PATH']
log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = '~/.config/nest.cfg'


class NestConfig:
    def __init__(self, path: str, overrides: Mapping[str, Optional[str]] = None):
        self.path = Path(path or DEFAULT_CONFIG_PATH).expanduser()
        self._overrides = overrides or {}

    @cached_property
    def _data(self) -> ConfigParser:
        config = ConfigParser()
        if self.path.exists():
            with self.path.open('r', encoding='utf-8') as f:
                config.read_file(f)
        return config

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, section, key, name=None, new_value=None, save=False, required=False):
        name = name or key
        cfg_value = self._data.get(section, key)
        if cfg_value and new_value:
            msg = f'Found {name}={cfg_value!r} in {self.path} - overwrite with {name}={new_value!r}?'
            if get_input(msg, skip=save):
                self.set(section, key, new_value)
        elif required and not cfg_value and not new_value:
            try:
                new_value = input(f'Please enter your Nest {name}: ').strip()
            except EOFError as e:
                raise RuntimeError('Unable to read stdin (this is often caused by piped input)') from e
            if not new_value:
                raise ValueError(f'Invalid {name}')
            self.set(section, key, new_value)
        return new_value or cfg_value

    def set(self, section, key, value):
        try:
            self._data.set(section, key, value)
        except NoSectionError:
            self._data.add_section(section)
            self._data.set(section, key, value)
        with self.path.open('w', encoding='utf-8') as f:
            self._data.write(f)

    @cached_property
    def email(self) -> str:
        return self.get('credentials', 'email', 'email address', self._overrides.get('email'), required=True)

    @property
    def serial(self) -> Optional[str]:
        return self.get('device', 'serial', 'thermostat serial number', self._overrides.get('serial'))

    @property
    def oauth_cookie(self):
        return self.get('oauth', 'cookie', 'OAuth Cookie', required=True)

    @property
    def oauth_login_hint(self):
        return self.get('oauth', 'login_hint', 'OAuth login_hint', required=True)

    @property
    def oauth_client_id(self):
        return self.get('oauth', 'client_id', 'OAuth client_id', required=True)
