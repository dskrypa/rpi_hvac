"""
Library for interacting with the Nest thermostat via the cloud API

:author: Doug Skrypa
"""

import calendar
import json
import logging
import pickle
import time
from collections import defaultdict
from configparser import ConfigParser, NoSectionError
from contextlib import contextmanager
from datetime import datetime
from functools import cached_property
from getpass import getpass
from pathlib import Path
from threading import RLock
from typing import List, Dict, Any, Union, Optional
from urllib.parse import urlparse

try:
    import keyring
except ImportError:
    keyring = None

from ds_tools.core.filesystem import get_user_cache_dir
from ds_tools.input import get_input
from ds_tools.output import Printer, SimpleColumn, Table
from requests_client import RequestsClient, USER_AGENT_CHROME
from tz_aware_dt import datetime_with_tz, localize, format_duration, TZ_LOCAL, now, TZ_UTC
from .cron import NestCronSchedule
from .utils import celsius_to_fahrenheit as c2f, fahrenheit_to_celsius as f2c, secs_to_wall, wall_to_secs

__all__ = ['NestWebClient']
log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = '~/.config/nest.cfg'
JWT_URL = 'https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt'
KEYRING_URL = 'https://pypi.org/project/keyring/'
NEST_API_KEY = 'AIzaSyAdkSIMNc51XGNEAYWasX9UOWkS5P6sZE4'  # public key from Nest's website
NEST_URL = 'https://home.nest.com'
OAUTH_URL = 'https://accounts.google.com/o/oauth2/iframerpc'


class NestWebClient(RequestsClient):
    def __init__(
        self, email=None, serial=None, no_store_prompt=False, update_password=False, config_path=None, reauth=False
    ):
        """
        :param str email: The email address to be used for login
        :param str serial: The serial number of the thermostat to be managed by this client
        :param bool no_store_prompt: Do not prompt to store the password securely
        :param bool update_password: Prompt to update the stored password, even if one already exists
        :param str config_path: The config path to use
        :param bool reauth: Force reauth, even if a cached session exists
        """
        super().__init__(NEST_URL, user_agent_fmt=USER_AGENT_CHROME, headers={'Referer': NEST_URL})
        self._reauth = reauth
        self._lock = RLock()
        self._cache_dir = Path(get_user_cache_dir('nest'))
        self._config_path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser()
        self._no_store_pw = no_store_prompt
        self._update_pw = update_password
        self._email = self._get_config('credentials', 'email', 'email address', email, required=True)
        self._session_info = None
        self._session_expiry = None
        self._user_id = None
        self._nest_host_port = ('home.nest.com', None)
        self._serial = self._get_config('device', 'serial', 'thermostat serial number', serial)

    @property
    def user_id(self):
        if self._user_id is None:
            self._maybe_refresh_login()
        return self._user_id

    @user_id.setter
    def user_id(self, value):
        self._user_id = value

    @cached_property
    def _config(self) -> ConfigParser:
        config = ConfigParser()
        if self._config_path.exists():
            with self._config_path.open('r', encoding='utf-8') as f:
                config.read_file(f)
        return config

    def _get_config(self, section, key, name=None, new_value=None, save=False, required=False):
        name = name or key
        cfg_value = self._config.get(section, key)
        if cfg_value and new_value:
            msg = f'Found {name}={cfg_value!r} in {self._config_path} - overwrite with {name}={new_value!r}?'
            if get_input(msg, skip=save):
                self._set_config(section, key, new_value)
        elif required and not cfg_value and not new_value:
            try:
                new_value = input(f'Please enter your Nest {name}: ').strip()
            except EOFError as e:
                raise RuntimeError('Unable to read stdin (this is often caused by piped input)') from e
            if not new_value:
                raise ValueError(f'Invalid {name}')
            self._set_config(section, key, new_value)
        return new_value or cfg_value

    def _set_config(self, section, key, value):
        try:
            self._config.set(section, key, value)
        except NoSectionError:
            self._config.add_section(section)
            self._config.set(section, key, value)
        with self._config_path.open('w', encoding='utf-8') as f:
            self._config.write(f)

    def _maybe_refresh_login(self):
        with self._lock:
            if self._session_expiry is None or self._session_expiry < TZ_LOCAL.localize(datetime.now()):
                for key in ('_service_urls', '_transport_host_port'):
                    try:
                        del self.__dict__[key]
                    except KeyError:
                        pass

                if 'oauth' in self._config:
                    try:
                        self._load_cached_session()
                    except SessionExpired as e:
                        log.debug(e)
                        self._login_via_google()
                else:
                    self._login_via_nest()

    @property
    def _cached_session_path(self):
        return self._cache_dir.joinpath('session.pickle')

    def _get_oauth_token(self):
        headers = {
            'Sec-Fetch-Mode': 'cors',
            'X-Requested-With': 'XmlHttpRequest',
            'Referer': 'https://accounts.google.com/o/oauth2/iframe',
            'cookie': self._get_config('oauth', 'cookie', 'OAuth Cookie', required=True)
        }
        # token_url = self._get_config('oauth', 'token_url', 'OAuth Token URL', required=True)
        # resp = self.session.get(token_url, headers=headers)
        login_hint = self._get_config('oauth', 'login_hint', 'OAuth login_hint', required=True)
        client_id = self._get_config('oauth', 'client_id', 'OAuth client_id', required=True)
        params = {
            'action': ['issueToken'], 'response_type': ['token id_token'],
            'login_hint': [login_hint], 'client_id': [client_id], 'origin': [NEST_URL],
            'scope': ['openid profile email https://www.googleapis.com/auth/nest-account'], 'ss_domain': [NEST_URL],
        }
        resp = self.session.get(OAUTH_URL, params=params, headers=headers).json()
        log.log(9, 'Received OAuth response: {}'.format(json.dumps(resp, indent=4, sort_keys=True)))
        return resp['access_token']

    def _load_cached_session(self):
        if self._reauth:
            raise SessionExpired('Forced reauth')

        path = self._cached_session_path
        if path.exists():
            with path.open('rb') as f:
                try:
                    expiry, userid, jwt_token, cookies = pickle.load(f)
                except (TypeError, ValueError) as e:
                    raise SessionExpired(f'Found a cached session, but encountered an error loading it: {e}')

            if expiry < now(as_datetime=True) or any(cookie.expires < time.time() for cookie in cookies):
                raise SessionExpired('Found a cached session, but it expired')

            self._register_session(expiry, userid, jwt_token, cookies)
            log.debug(f'Loaded session for user={userid} with expiry={localize(expiry)}')
        else:
            raise SessionExpired('No cached session was found')

    def _register_session(self, expiry, userid, jwt_token, cookies=None, save=False):
        self._session_expiry = expiry
        self.user_id = userid
        self.session.headers['Authorization'] = f'Basic {jwt_token}'
        if cookies is not None:
            for cookie in cookies:
                self.session.cookies.set_cookie(cookie)

        if save:
            path = self._cached_session_path
            log.debug(f'Saving session info in cache: {path}')
            with path.open('wb') as f:
                pickle.dump((expiry, userid, jwt_token, list(self.session.cookies)), f)

    def _login_via_google(self):
        token = self._get_oauth_token()
        headers = {'Authorization': f'Bearer {token}', 'x-goog-api-key': NEST_API_KEY}
        params = {
            'embed_google_oauth_access_token': True, 'expire_after': '3600s',
            'google_oauth_access_token': token, 'policy_id': 'authproxy-oauth-policy',
        }
        resp = self.session.post(JWT_URL, params=params, headers=headers).json()
        log.log(9, 'Initialized session; response: {}'.format(json.dumps(resp, indent=4, sort_keys=True)))
        claims = resp['claims']
        expiry = datetime_with_tz(claims['expirationTime'], '%Y-%m-%dT%H:%M:%S.%fZ', TZ_UTC).astimezone(TZ_LOCAL)
        self._register_session(expiry, claims['subject']['nestId']['id'], resp['jwt'], save=True)
        log.debug(f'Initialized session for user={self.user_id!r} with expiry={localize(expiry)}')

    def _login_via_nest(self):
        """This login method is deprecated"""
        log.debug(f'Initializing session for email={self._email!r}')
        resp = self.post('session', json={'email': self._email, 'password': self.__password})
        info = resp.json()
        self.user_id = info['userid']
        self.session.headers['Authorization'] = 'Basic {}'.format(info['access_token'])
        self.session.headers['X-nl-user-id'] = self.user_id
        self._session_expiry = expiry = datetime_with_tz(info['expires_in'], '%a, %d-%b-%Y %H:%M:%S %Z')
        log.debug(f'Initialized legacy session for user={self.user_id!r} with expiry={localize(expiry)}')
        transport_url = urlparse(info['urls']['transport_url'])
        self.__dict__['_transport_host_port'] = (transport_url.hostname, transport_url.port)

    @cached_property
    def __password(self):
        if keyring is not None:
            password = keyring.get_password(type(self).__name__, self._email)
            if self._update_pw and password:
                keyring.delete_password(type(self).__name__, self._email)
                password = None
            if password is None:
                password = getpass()
                if not self._no_store_pw and get_input(f'Store password in keyring ({KEYRING_URL})?'):
                    keyring.set_password(type(self).__name__, self._email, password)
                    log.info('Stored password in keyring')
            else:
                log.debug('Using password from keyring')
        else:
            password = getpass()
        return password

    @cached_property
    def _service_urls(self):
        resp = self._app_launch([])
        return resp['service_urls']

    @cached_property
    def _transport_host_port(self):
        transport_url = urlparse(self._service_urls['urls']['transport_url'])
        return transport_url.hostname, transport_url.port

    @contextmanager
    def transport_url(self):
        with self._lock:
            self._maybe_refresh_login()
            log.debug('Using host:port={}:{}'.format(*self._transport_host_port))
            self.host, self.port = self._transport_host_port
            yield self

    @contextmanager
    def nest_url(self):
        with self._lock:
            self._maybe_refresh_login()
            log.debug('Using host:port={}:{}'.format(*self._nest_host_port))
            self.host, self.port = self._nest_host_port
            yield self

    @cached_property
    def serial(self):
        if not self._serial:
            resp = self.app_launch(['device'])
            if len(resp) > 1:
                serials = ', '.join(sorted(resp.keys()))
                raise RuntimeError(
                    f'A device serial number must be provided in {self._config_path} or at init - found multiple '
                    f'devices: {serials}'
                )
            elif not resp:
                raise RuntimeError('No devices were found')
            self._serial = next(iter(resp))
            self._set_config('device', 'serial', self._serial)
        return self._serial

    def _app_launch(self, bucket_types: List[str], raw=False):
        with self.nest_url():
            payload = {'known_bucket_types': bucket_types, 'known_bucket_versions': []}
            resp = self.post(f'api/0.1/user/{self.user_id}/app_launch', json=payload)
            return resp if raw else resp.json()

    def app_launch(self, bucket_types=None):
        """
        Interesting info by section::\n
            {
                '{serial}': {
                    'shared': {
                        "target_temperature_type": "cool",
                        "target_temperature_high": 24.0, "target_temperature_low": 20.0,
                        "current_temperature": 20.84, "target_temperature": 22.59464,
                    },
                    'device': {
                        "current_humidity": 51, "backplate_temperature": 20.84, "fan_current_speed": "off",
                        "target_humidity": 35.0, "current_schedule_mode": "COOL", "leaf_threshold_cool": 23.55441,
                        "weave_device_id": "...", "backplate_serial_number": "...", "serial_number": "...",
                        "local_ip": "...", "mac_address": "...", "postal_code": "...",
                    },
                    'schedule': {
                        "ver": 2, "schedule_mode": "COOL", "name": "Current Schedule",
                        "days": {
                            "4": {
                                "4": {
                                    "touched_by": 4, "temp": 22.9444, "touched_tzo": -14400,
                                    "touched_user_id": "user....", "touched_at": 1501809124, "time": 18900,
                                    "type": "COOL", "entry_type": "setpoint"
                                }, ...
                            }, ...
            }}}}

        :param list bucket_types: The bucket_types to retrieve (such as device, shared, schedule, etc.)
        :return dict: Mapping of {serial:{bucket_type:{bucket['value']}}}
        """
        resp = self._app_launch(bucket_types or ['device', 'shared', 'schedule'])
        info = defaultdict(dict)
        for bucket in resp['updated_buckets']:
            bucket_type, serial = bucket['object_key'].split('.')
            info[serial][bucket_type] = bucket['value']
        return info

    @cached_property
    def bucket_types(self):
        resp = self._app_launch(['buckets'])
        buckets = resp['updated_buckets'][0]['value']['buckets']
        types = defaultdict(set)
        for bucket in buckets:
            bucket_type, category = bucket.split('.', 1)
            types[category].add(bucket_type)
        return types

    @cached_property
    def device_capabilities(self):
        resp = self.app_launch(['device', 'shared'])
        return {serial: self._filter_capabilities(info) for serial, info in resp.items()}

    def _filter_capabilities(self, info):
        capabilities = {
            key.split('_', 1)[1]: val for key, val in info['device'].items() if key.startswith('has_')
        }
        capabilities['fan_capabilities'] = info['device']['fan_capabilities']
        return capabilities

    def get_state(self, fahrenheit=True):
        resp = self.app_launch(['device', 'shared'])
        info = resp[self.serial]
        capabilities = self._filter_capabilities(info)
        # fmt: off
        temps = {
            'shared': (
                'target_temperature_high', 'target_temperature_low', 'target_temperature', 'current_temperature',
            ),
            'device': ('backplate_temperature', 'leaf_threshold_cool')
        }
        non_temps = {
            'shared': [
                'target_temperature_type', 'compressor_lockout_enabled', 'compressor_lockout_timeout',
                'hvac_ac_state', 'hvac_heater_state', 'target_change_pending',
            ],
            'device': (
                'current_humidity', 'fan_current_speed', 'target_humidity', 'current_schedule_mode', 'fan_capabilities',
                'time_to_target', 'fan_cooling_enabled', 'fan_cooling_readiness', 'fan_cooling_state',
                'fan_current_speed', 'fan_schedule_speed', 'fan_timer_duration', 'fan_timer_speed', 'fan_timer_timeout',
            )
        }

        opt_keys = (
            'hvac_alt_heat_state', 'hvac_alt_heat_x2_state', 'hvac_aux_heater_state', 'hvac_cool_x2_state',
            'hvac_cool_x3_state', 'hvac_emer_heat_state', 'hvac_fan_state', 'hvac_heat_x2_state', 'hvac_heat_x3_state',
        )
        for key in opt_keys:
            parts = key.split('_')[1:-1]
            if parts[-1].startswith('x'):
                parts = parts[-1:] + parts[:-1]
            cap_key = '_'.join(parts)
            if capabilities.get(cap_key):
                non_temps['shared'].append(key)

        # fmt: on
        state = {}
        for section, keys in temps.items():
            for key in keys:
                state[key] = c2f(info[section][key]) if fahrenheit else info[section][key]
        for section, keys in non_temps.items():
            for key in keys:
                state[key] = info[section][key]

        return state

    def get_mobile_info(self):
        with self.transport_url():
            return self.get('v2/mobile/user.{}'.format(self.user_id)).json()

    def _post_put(self, value: Any, obj_key: Optional[str] = None, op: Optional[str] = None):
        obj_key = obj_key or f'shared.{self.serial}'
        with self.transport_url():
            payload = {'objects': [{'object_key': obj_key, 'op': op or 'MERGE', 'value': value}]}
            return self.post('v5/put', json=payload)

    def get_mode(self):
        resp = self.app_launch(['shared'])
        return resp[self._serial]['shared']['target_temperature_type']

    def set_temp_range(self, low, high, unit='f'):
        """
        :param float low: Minimum temperature to maintain in Celsius (heat will turn on if the temp drops below this)
        :param float high: Maximum temperature to allow in Celsius (air conditioning will turn on above this)
        :param str unit: Either 'f' or 'c' for fahrenheit/celsius
        :return: The parsed response
        """
        unit = unit.lower()
        if unit[0] == 'f':
            low = f2c(low)
            high = f2c(high)
        elif unit[0] != 'c':
            raise ValueError('Unit must be either \'f\' or \'c\' for fahrenheit/celsius')
        resp = self._post_put({'target_temperature_low': low, 'target_temperature_high': high})
        return resp.json()

    def set_temp(self, temp, unit='f'):
        """
        :param float temp: The target temperature to maintain in Celsius
        :param str unit: Either 'f' or 'c' for fahrenheit/celsius
        :return: The parsed response
        """
        unit = unit.lower()
        if unit[0] == 'f':
            temp = f2c(temp)
        elif unit[0] != 'c':
            raise ValueError('Unit must be either \'f\' or \'c\' for fahrenheit/celsius')
        resp = self._post_put({'target_temperature': temp})
        return resp.json()

    def set_mode(self, mode):
        """
        :param str mode: One of 'cool', 'heat', 'range', or 'off'
        :return: The parsed response
        """
        mode = mode.lower()
        if mode not in ('cool', 'heat', 'range', 'off'):
            raise ValueError(f'Invalid mode: {mode!r}')
        resp = self._post_put({'target_temperature_type': mode})
        return resp.json()

    def start_fan(self, duration=1800):
        """
        :param int duration: Number of seconds for which the fan should run
        :return: The parsed response
        """
        timeout = int(time.time()) + duration
        fmt = 'Submitting fan start request with duration={} => end time of {}'
        log.debug(fmt.format(format_duration(duration), timeout))
        resp = self._post_put({'fan_timer_timeout': timeout})
        return resp.json()

    def stop_fan(self):
        resp = self._post_put({'fan_timer_timeout': 0})
        return resp.json()

    def get_energy_usage_history(self):
        """
        Response example::
            {
                "objects": [{
                    "object_revision": 1, "object_timestamp": 1, "object_key": "energy_latest.{serial}",
                    "value": {
                        "recent_max_used": 39840,
                        "days": [{
                            "day": "2019-09-19", "device_timezone_offset": -14400, "total_heating_time": 0,
                            "total_cooling_time": 25860, "total_fan_cooling_time": 2910,
                            "total_humidifier_time": 0, "total_dehumidifier_time": 0,
                            "leafs": 0, "whodunit": -1, "recent_avg_used": 32060, "usage_over_avg": -6200,
                            "cycles": [{"start": 0, "duration": 3180, "type": 65792},...],
                            "events": [{
                                "start": 0, "end": 899, "type": 1, "touched_by": 4, "touched_when": 1557106673,
                                "touched_timezone_offset": -14400, "touched_where": 1, "touched_id": "...@gmail.com",
                                "cool_temp": 20.333, "event_touched_by": 0, "continuation": true
                            },...],
                            "rates": [], "system_capabilities": 2817, "incomplete_fields": 0
                        }, ...]
                    }
                }]
            }

        :return: The parsed response
        """
        with self.transport_url():
            payload = {'objects': [{'object_key': f'energy_latest.{self.serial}'}]}
            resp = self.post('v5/subscribe', json=payload)
            return resp.json()

    def get_weather(self, zip_code=None, country_code='US'):
        """
        Get the weather forecast.  Response format::
            {
              "display_city":"...", "city":"...",
              "forecast":{
                "hourly":[{"time":1569769200, "temp":74.0, "humidity":55},...],
                "daily":[{
                  "conditions":"Partly Cloudy", "date":1569729600, "high_temperature":77.0, "icon":"partlycloudy",
                  "low_temperature":60.0
                },...]
              },
              "now":{
                "station_id":"unknown", "conditions":"Mostly Cloudy", "current_humidity":60, "current_temperature":22.8,
                "current_wind":12, "gmt_offset":"-04.00", "icon":"mostlycloudy", "sunrise":1569754260,
                "sunset":1569796920, "wind_direction":"N"
              }
            }

        :param int|str zip_code: A 5-digit zip code
        :param str country_code: A 2-letter country code (such as 'US')
        :return dict: The parsed response
        """
        if zip_code is None:
            resp = self._app_launch([])
            location = next(iter(resp['weather_for_structures'].values()))['location']
            zip_code = location['zip']
            country_code = country_code or location['country']

        with self.nest_url():
            resp = self.get('api/0.1/weather/forecast/{},{}'.format(zip_code, country_code))
            return resp.json()

    def get_schedule(self) -> 'NestSchedule':
        raw = self._app_launch(['schedule'])['updated_buckets']
        return NestSchedule(self, raw)


class NestSchedule:
    def __init__(self, nest: NestWebClient, raw_schedules: List[Dict[str, Any]]):
        """
        .. important::
            Nest represents days as 0=Monday ~ 6=Sunday.  This class uses the same values as cron, i.e., 0=Sunday ~
            6=Saturday, and automatically converts between them where necessary.

        :param NestWebClient nest: The :class:`NestWebClient` from which this schedule originated
        :param list raw_schedules: The result of NestWebClient._app_launch(['schedule'])['updated_buckets']
        """
        self._nest = nest
        self.object_key = f'schedule.{self._nest.serial}'
        self.user_id = f'user.{self._nest.user_id}'
        for entry in raw_schedules:
            if entry.get('object_key') == self.object_key:
                self._raw = entry
                break
        else:
            raise ValueError(f'Unable to find an entry for {self.object_key=!r} in the provided raw_schedule')
        info = self._raw['value']
        self._ver = info['ver']
        self._schedule_mode = info['schedule_mode']
        self._name = info['name']
        self._schedule = {
            int(day): [entry for i, entry in sorted(sched.items())] for day, sched in sorted(info['days'].items())
        }

    @classmethod
    def from_file(cls, nest: NestWebClient, path: Union[str, Path]) -> 'NestSchedule':
        path = Path(path)
        if not path.is_file():
            raise ValueError(f'Invalid schedule path: {path}')

        with path.open('r', encoding='utf-8') as f:
            schedule = json.load(f)

        return cls.from_dict(nest, schedule)

    @classmethod
    def from_dict(cls, nest: NestWebClient, schedule: Dict[str, Any]) -> 'NestSchedule':
        user_id = f'user.{nest.user_id}'
        meta = schedule['meta']
        user_num = meta['user_nums'][user_id]
        _days = schedule['days']
        convert = meta['unit'] == 'f'
        days = {}
        for day_num, day in enumerate(calendar.day_name):
            if day_schedule := _days.get(day):
                days[day_num] = {
                    i: {
                        'temp': f2c(temp) if convert else temp,
                        'touched_by': user_num,
                        'time': wall_to_secs(tod_str),
                        'touched_tzo': -14400,
                        'type': meta['mode'],
                        'entry_type': 'setpoint',
                        'touched_user_id': user_id,
                        'touched_at': int(time.time()),
                    }
                    for i, (tod_str, temp) in enumerate(day_schedule.items())
                }
            else:
                days[day_num] = {}

        raw_schedule = {
            'object_key': f'schedule.{nest.serial}',
            'value': {'ver': meta['ver'], 'schedule_mode': meta['mode'], 'name': meta['name'], 'days': days},
        }
        return cls(nest, [raw_schedule])

    def to_dict(self, unit='f'):
        schedule = {
            'meta': {
                'ver': self._ver,
                'mode': self._schedule_mode,
                'name': self._name,
                'unit': unit,
                'user_nums': self.user_nums,
            },
            'days': self.as_day_time_temp_map(unit),
        }
        return schedule

    def save(self, path: Union[str, Path], unit='f', overwrite: bool = False, dry_run: bool = False):
        path = Path(path)
        if path.is_file() and not overwrite:
            raise ValueError(f'Path already exists: {path}')
        elif not path.parent.exists() and not dry_run:
            path.parent.mkdir(parents=True)

        prefix = '[DRY RUN] Would save' if dry_run else 'Saving'
        log.info(f'{prefix} schedule to {path}')
        with path.open('w', encoding='utf-8', newline='\n') as f:
            json.dump(self.to_dict(unit), f, indent=4, sort_keys=False)

    def update(self, cron_str: str, action: str, temp: float, unit: str = 'c', dry_run: bool = False):
        cron = NestCronSchedule.from_cron(cron_str)
        changes_made = 0
        if action == 'remove':
            for dow, tod_seconds in cron:
                try:
                    self.remove(dow, tod_seconds)
                except TimeNotFound as e:
                    log.debug(e)
                    pass
                else:
                    log.debug(f'Removed time={secs_to_wall(tod_seconds)} from {dow=}')
                    changes_made += 1
        elif action == 'add':
            for dow, tod_seconds in cron:
                self.insert(dow, tod_seconds, temp, unit)
                changes_made += 1
        else:
            raise ValueError(f'Unexpected {action=!r}')

        if changes_made:
            past, tf = ('Added', 'to') if action == 'add' else ('Removed', 'from')
            log.info(f'{past} {changes_made} entries {tf} {self._schedule_mode} schedule with name={self._name!r}')
            self.push(dry_run)
        else:
            log.info(f'No changes made')

    def insert(self, day: int, time_of_day: Union[str, int], temp: float, unit: str = 'c'):
        if unit not in ('f', 'c'):
            raise ValueError(f'Unexpected temperature {unit=!r}')
        elif not 0 <= day < 7:
            raise ValueError(f'Invalid {day=!r} - Expected 0=Sunday ~ 6=Saturday')
        temp = f2c(temp) if unit == 'f' else temp
        time_of_day = wall_to_secs(time_of_day) if isinstance(time_of_day, str) else time_of_day
        if not 0 <= time_of_day < 86400:
            raise ValueError(f'Invalid {time_of_day=!r} ({secs_to_wall(time_of_day)}) - must be > 0 and < 86400')

        entry = {
            'temp': temp,
            'touched_by': self._user_num,
            'time': time_of_day,
            'touched_tzo': -14400,
            'type': self._schedule_mode,
            'entry_type': 'setpoint',
            'touched_user_id': self.user_id,
            'touched_at': int(time.time()),
        }
        day_schedule = self._schedule.setdefault(_previous_day(day), [])
        for i, existing in enumerate(day_schedule):
            if existing['time'] == time_of_day:
                day_schedule[i] = entry
                break
        else:
            day_schedule.append(entry)
        self._update_continuations()

    def remove(self, day: int, time_of_day: Union[str, int]):
        if not 0 <= day < 7:
            raise ValueError(f'Invalid {day=!r} - Expected 0=Sunday ~ 6=Saturday')
        time_of_day = wall_to_secs(time_of_day) if isinstance(time_of_day, str) else time_of_day
        if not 0 < time_of_day < 86400:
            raise ValueError(f'Invalid {time_of_day=!r} ({secs_to_wall(time_of_day)}) - must be > 0 and < 86400')

        day_entries = self._schedule.setdefault(_previous_day(day), [])
        index = next((i for i, entry in enumerate(day_entries) if entry['time'] == time_of_day), None)
        if index is None:
            times = ', '.join(sorted(secs_to_wall(e['time']) for e in day_entries))
            raise TimeNotFound(
                f'Invalid {time_of_day=!r} ({secs_to_wall(time_of_day)}) - not found in {day=} with times: {times}'
            )
        day_entries.pop(index)
        self._update_continuations()

    def _update_mode(self, dry_run: bool = False):
        mode = self._nest.get_mode().lower()
        sched_mode = self._schedule_mode.lower()
        if sched_mode != mode:
            prefix = '[DRY RUN] Would update' if dry_run else 'Updating'
            log.info(f'{prefix} mode from {mode} to {sched_mode}')
            if not dry_run:
                self._nest.set_mode(sched_mode)

    def push(self, dry_run: bool = False):
        self._update_mode(dry_run)
        days = {
            str(day): {str(i): entry for i, entry in enumerate(entries)}
            for day, entries in sorted(self._schedule.items())
        }
        log.info('New schedule to be pushed:\n{}'.format(self.format()))
        log.debug('Full schedule to be pushed: {}'.format(json.dumps(days, indent=4, sort_keys=True)))
        prefix = '[DRY RUN] Would push' if dry_run else 'Pushing'
        log.info(f'{prefix} changes to {self._schedule_mode} schedule with name={self._name!r}')
        if not dry_run:
            value = {
                'ver': self._ver, 'schedule_mode': self._schedule_mode, 'name': self._name, 'days': days
            }
            resp = self._nest._post_put(value, self.object_key, 'OVERWRITE')
            log.debug('Push response: {}'.format(json.dumps(resp.json(), indent=4, sort_keys=True)))

    def as_day_time_temp_map(self, unit='f'):
        day_names = calendar.day_name[-1:] + calendar.day_name[:-1]
        day_time_temp_map = {day: None for day in day_names}
        convert = unit == 'f'
        for day, (day_num, day_schedule) in zip(calendar.day_name, sorted(self._schedule.items())):
            day_time_temp_map[day] = {
                secs_to_wall(entry['time']): c2f(entry['temp']) if convert else entry['temp']
                for i, entry in enumerate(day_schedule)
            }
        return day_time_temp_map

    def format(self, output_format='table', unit='f'):
        schedule = self.as_day_time_temp_map(unit)
        if output_format == 'table':
            times = set()
            rows = []
            for day, time_temp_map in schedule.items():
                times.update(time_temp_map)
                row = time_temp_map.copy()
                row['Day'] = day
                rows.append(row)

            columns = [SimpleColumn('Day')]
            columns.extend(SimpleColumn(_time, ftype='.1f') for _time in sorted(times))
            table = Table(*columns, update_width=True)
            return table.format_rows(rows, True)
        else:
            return Printer(output_format).pformat(schedule, sort_keys=False)

    def print(self, output_format='table', unit='f'):
        print(self.format(output_format, unit))

    @cached_property
    def user_nums(self):
        return {
            e['touched_user_id']: e['touched_by']
            for d, entries in self._schedule.items()
            for e in entries if 'touched_user_id' in e
        }

    @cached_property
    def _user_num(self):
        return self.user_nums[self.user_id]

    def _find_last(self, day: int):
        while (prev_day := _previous_day(day)) != day:
            if entries := self._schedule.setdefault(prev_day, []):
                entries.sort(key=lambda e: e['time'])
                return entries[-1].copy()
        return None

    def _update_continuations(self):
        for day in range(7):
            today = self._schedule.setdefault(day, [])
            today.sort(key=lambda e: e['time'])
            if continuation := self._find_last(day):
                if today[0]['entry_type'] == 'continuation' and today[0]['temp'] != continuation['temp']:
                    log.debug(f'Updating continuation entry for {day=}')
                    continuation.pop('touched_user_id', None)
                    continuation.update(touched_by=1, time=0, entry_type='continuation')
                    today[0] = continuation
                else:
                    log.debug(f'The continuation entry for {day=} is already correct')
            else:
                # this is a new schedule - update every day to continue the last entry from today & break
                continuation = today[-1].copy()
                continuation.pop('touched_user_id', None)
                continuation.update(touched_by=1, time=0, entry_type='continuation')
                for _day in range(7):
                    log.debug(f'Adding continuation entry for day={_day}')
                    day_sched = self._schedule.setdefault(_day, [])
                    if not any(e['time'] == 0 for e in day_sched):
                        day_sched.insert(0, continuation)
                break


def _previous_day(day: int):
    return 6 if day == 0 else day - 1


def _next_day(day: int):
    return 0 if day == 6 else day + 1


def _continuation_day(day: int) -> int:
    days = list(range(7))
    candidates = days[day+1:] + days[:day]
    return candidates[0]


class SessionExpired(Exception):
    pass


class TimeNotFound(ValueError):
    pass
