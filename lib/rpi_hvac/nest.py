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
from typing import List
from urllib.parse import urlparse

try:
    import keyring
except ImportError:
    keyring = None

from ds_tools.core.filesystem import get_user_cache_dir
from ds_tools.input import get_input
from requests_client import RequestsClient, USER_AGENT_CHROME
from tz_aware_dt import datetime_with_tz, localize, format_duration, TZ_LOCAL, now, TZ_UTC
from .utils import celsius_to_fahrenheit as c2f, fahrenheit_to_celsius as f2c

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
        self._userid = None
        self._nest_host_port = ('home.nest.com', None)
        self.serial = self._get_config('device', 'serial', 'thermostat serial number', serial)

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
        self._userid = userid
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
        log.debug(f'Initialized session for user={self._userid!r} with expiry={localize(expiry)}')

    def _login_via_nest(self):
        """This login method is deprecated"""
        log.debug(f'Initializing session for email={self._email!r}')
        resp = self.post('session', json={'email': self._email, 'password': self.__password})
        info = resp.json()
        self._userid = info['userid']
        self.session.headers['Authorization'] = 'Basic {}'.format(info['access_token'])
        self.session.headers['X-nl-user-id'] = self._userid
        self._session_expiry = expiry = datetime_with_tz(info['expires_in'], '%a, %d-%b-%Y %H:%M:%S %Z')
        log.debug(f'Initialized legacy session for user={self._userid!r} with expiry={localize(expiry)}')
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

    def _app_launch(self, bucket_types: List[str]):
        with self.nest_url():
            payload = {'known_bucket_types': bucket_types, 'known_bucket_versions': []}
            resp = self.post(f'api/0.1/user/{self._userid}/app_launch', json=payload)
            return resp.json()

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

    def get_state(self, serial=None, fahrenheit=True):
        serial = self._validate_serial(serial)
        resp = self.app_launch(['device', 'shared'])
        info = resp[serial]
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
            return self.get('v2/mobile/user.{}'.format(self._userid)).json()

    def _validate_serial(self, serial):
        serial = serial or self.serial
        if not serial:
            raise ValueError('A Nest thermostat serial number must be provided as a param or set for the client object')
        return serial

    def _put_value(self, serial, value):
        serial = self._validate_serial(serial)
        with self.transport_url():
            payload = {'objects': [{'object_key': f'shared.{serial}', 'op': 'MERGE', 'value': value}]}
            return self.post('v5/put', json=payload)

    def set_temp_range(self, low, high, serial=None, unit='f'):
        """
        :param float low: Minimum temperature to maintain in Celsius (heat will turn on if the temp drops below this)
        :param float high: Maximum temperature to allow in Celsius (air conditioning will turn on above this)
        :param str serial: A Nest thermostat serial number
        :param str unit: Either 'f' or 'c' for fahrenheit/celsius
        :return: The parsed response
        """
        unit = unit.lower()
        if unit[0] == 'f':
            low = f2c(low)
            high = f2c(high)
        elif unit[0] != 'c':
            raise ValueError('Unit must be either \'f\' or \'c\' for fahrenheit/celsius')
        resp = self._put_value(serial, {'target_temperature_low': low, 'target_temperature_high': high})
        return resp.json()

    def set_temp(self, temp, serial=None, unit='f'):
        """
        :param float temp: The target temperature to maintain in Celsius
        :param str serial: A Nest thermostat serial number
        :param str unit: Either 'f' or 'c' for fahrenheit/celsius
        :return: The parsed response
        """
        unit = unit.lower()
        if unit[0] == 'f':
            temp = f2c(temp)
        elif unit[0] != 'c':
            raise ValueError('Unit must be either \'f\' or \'c\' for fahrenheit/celsius')
        resp = self._put_value(serial, {'target_temperature': temp})
        return resp.json()

    def set_mode(self, mode, serial=None):
        """
        :param str mode: One of 'cool', 'heat', 'range', or 'off'
        :param str serial: A Nest thermostat serial number
        :return: The parsed response
        """
        mode = mode.lower()
        if mode not in ('cool', 'heat', 'range', 'off'):
            raise ValueError(f'Invalid mode: {mode!r}')
        resp = self._put_value(serial, {'target_temperature_type': mode})
        return resp.json()

    def start_fan(self, duration=1800, serial=None):
        """
        :param int duration: Number of seconds for which the fan should run
        :param str serial: A Nest thermostat serial number
        :return: The parsed response
        """
        timeout = int(time.time()) + duration
        fmt = 'Submitting fan start request with duration={} => end time of {}'
        log.debug(fmt.format(format_duration(duration), timeout))
        resp = self._put_value(serial, {'fan_timer_timeout': timeout})
        return resp.json()

    def stop_fan(self, serial=None):
        """
        :param str serial: A Nest thermostat serial number
        :return: The parsed response
        """
        resp = self._put_value(serial, {'fan_timer_timeout': 0})
        return resp.json()

    def get_energy_usage_history(self, serial=None):
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

        :param str serial: A Nest thermostat serial number
        :return: The parsed response
        """
        serial = self._validate_serial(serial)
        with self.transport_url():
            payload = {'objects': [{'object_key': f'energy_latest.{serial}'}]}
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

    def get_schedule(self, unit='f', serial=None):
        serial = self._validate_serial(serial)
        schedule_info = self.app_launch(['schedule'])[serial]['schedule']
        day_names = calendar.day_name[-1:] + calendar.day_name[:-1]
        schedule = {}
        for day, (day_num, day_schedule) in zip(day_names, sorted(schedule_info['days'].items())):
            schedule[day] = {
                secs_to_wall(entry['time']): c2f(entry['temp']) if unit == 'f' else entry['temp']
                for i, entry in sorted(day_schedule.items())
            }
        return schedule


def secs_to_wall(seconds: int):
    hour, minute = divmod(seconds // 60, 60)
    return f'{hour:02d}:{minute:02d}'


def wall_to_secs(wall: str):
    hour, minute = map(int, wall.split(':'))
    return (hour * 60 + minute) * 60


class SessionExpired(Exception):
    pass
