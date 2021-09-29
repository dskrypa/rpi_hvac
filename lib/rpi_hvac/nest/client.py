"""
Library for interacting with the Nest thermostat via the cloud API

:author: Doug Skrypa
"""

import json
import logging
import pickle
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from functools import cached_property
from getpass import getpass
from threading import RLock
from typing import TYPE_CHECKING, Any, ContextManager, Union
from urllib.parse import urlparse

try:
    import keyring
except ImportError:
    keyring = None

from ds_tools.fs.paths import get_user_cache_dir
from ds_tools.input import get_input
from requests_client import RequestsClient, USER_AGENT_CHROME
from tz_aware_dt.tz_aware_dt import datetime_with_tz, localize, TZ_LOCAL, TZ_UTC
from tz_aware_dt.utils import format_duration
from ..utils import celsius_to_fahrenheit as c2f, fahrenheit_to_celsius as f2c
from .config import NestConfig
from .exceptions import SessionExpired
from .schedule import NestSchedule

if TYPE_CHECKING:
    from requests import Response

__all__ = ['NestWebClient']
log = logging.getLogger(__name__)

JWT_URL = 'https://nestauthproxyservice-pa.googleapis.com/v1/issue_jwt'
KEYRING_URL = 'https://pypi.org/project/keyring/'
NEST_API_KEY = 'AIzaSyAdkSIMNc51XGNEAYWasX9UOWkS5P6sZE4'  # public key from Nest's website
NEST_URL = 'https://home.nest.com'
OAUTH_URL = 'https://accounts.google.com/o/oauth2/iframerpc'


class NestWebClient:
    def __init__(
        self,
        email: str = None,
        serial: str = None,
        no_store_prompt: bool = False,
        update_password: bool = False,
        config_path: str = None,
        reauth: bool = False
    ):
        """
        :param email: The email address to be used for login
        :param serial: The serial number of the thermostat to be managed by this client
        :param no_store_prompt: Do not prompt to store the password securely
        :param update_password: Prompt to update the stored password, even if one already exists
        :param config_path: The config path to use
        :param reauth: Force reauth, even if a cached session exists
        """
        self.config = NestConfig(config_path, {'email': email, 'serial': serial})
        self.session = NestWebSession(self.config, reauth, no_store_prompt, update_password)

    @property
    def user_id(self) -> str:
        return self.session.user_id

    @cached_property
    def serial(self) -> str:
        serial = self.config.serial
        if not serial:
            resp = self.app_launch(['device'])
            if len(resp) > 1:
                serials = ', '.join(sorted(resp.keys()))
                raise RuntimeError(
                    f'A device serial number must be provided in {self.config.path} or at init - found multiple '
                    f'devices: {serials}'
                )
            elif not resp:
                raise RuntimeError('No devices were found')

            serial = next(iter(resp))
            self.config.set('device', 'serial', serial)
        return serial

    def app_launch(self, bucket_types: list[str] = None):
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
        resp = self.session.app_launch(bucket_types or ['device', 'shared', 'schedule']).json()
        info = defaultdict(dict)
        for bucket in resp['updated_buckets']:
            bucket_type, serial = bucket['object_key'].split('.')
            info[serial][bucket_type] = bucket['value']
        return info

    @cached_property
    def bucket_types(self) -> dict[str, set[str]]:
        resp = self.session.app_launch(['buckets']).json()
        buckets = resp['updated_buckets'][0]['value']['buckets']
        types = defaultdict(set)
        for bucket in buckets:
            bucket_type, category = bucket.split('.', 1)
            types[category].add(bucket_type)
        return types

    @cached_property
    def device_capabilities(self):
        resp = self.app_launch(['device', 'shared'])
        return {serial: _filter_capabilities(info) for serial, info in resp.items()}

    def get_state(self, celsius: bool = None):
        info = self.app_launch(['device', 'shared'])[self.serial]
        capabilities = _filter_capabilities(info)
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
                'fan_schedule_speed', 'fan_timer_duration', 'fan_timer_speed', 'fan_timer_timeout',
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
        fahrenheit = self.config.temp_unit == 'f' if celsius is None else not celsius
        state = {}
        for section, keys in temps.items():
            for key in keys:
                state[key] = c2f(info[section][key]) if fahrenheit else info[section][key]
        for section, keys in non_temps.items():
            for key in keys:
                state[key] = info[section][key]

        return state

    def get_mobile_info(self):
        with self.session.transport_url() as client:
            return client.get(f'v2/mobile/user.{self.session.user_id}').json()

    def _post_put(self, value: Any, obj_key: str = None, op: str = None) -> 'Response':
        obj_key = obj_key or f'shared.{self.serial}'
        payload = {'objects': [{'object_key': obj_key, 'op': op or 'MERGE', 'value': value}]}
        with self.session.transport_url() as client:
            return client.post('v5/put', json=payload)

    def get_mode(self):
        return self.app_launch(['shared'])[self.serial]['shared']['target_temperature_type']

    def set_temp_range(self, low: float, high: float):
        """
        :param low: Minimum temperature to maintain in Celsius (heat will turn on if the temp drops below this)
        :param high: Maximum temperature to allow in Celsius (air conditioning will turn on above this)
        :return: The parsed response
        """
        if self.config.temp_unit == 'f':
            low = f2c(low)
            high = f2c(high)
        resp = self._post_put({'target_temperature_low': low, 'target_temperature_high': high})
        return resp.json()

    def set_temp(self, temp: float, force_run: bool = False):
        """
        :param temp: The target temperature to maintain in Celsius
        :param force_run: If the delta between the new temp and the old temp is < 0.5 degrees, then first set a
          temp that would trigger the Nest to run, then switch to the desired target temp
        :return: The parsed response
        """
        fahrenheit = self.config.temp_unit == 'f'
        if fahrenheit:
            temp = f2c(temp)
        if force_run:
            status = self.get_state(celsius=True)
            mode = status['current_schedule_mode'].upper()
            current = status['current_temperature']
            if mode == 'COOL':
                delta = current - temp
                if current > temp and delta < 0.5:
                    tmp = current - 0.6
                    log.debug(f'Setting temporary temp={tmp:.1f}')
                    resp = self._post_put({'target_temperature': tmp})
                    time.sleep(3)
            elif mode == 'HEAT':
                delta = temp - current
                log.debug(f'{current=} {temp=} {delta=} {fahrenheit=}')
                if current < temp and delta < 0.5:
                    tmp = current + 0.6
                    log.debug(f'Setting temporary temp={tmp:.1f}')
                    resp = self._post_put({'target_temperature': tmp})
                    time.sleep(3)
            else:
                log.log(19, f'Unable to force unit to run for {mode=!r}')

        log.debug(f'Setting requested temp={temp:.1f}')
        resp = self._post_put({'target_temperature': temp})
        return resp.json()

    def set_mode(self, mode: str):
        """
        :param mode: One of 'cool', 'heat', 'range', or 'off'
        :return: The parsed response
        """
        mode = mode.lower()
        if mode not in {'cool', 'heat', 'range', 'off'}:
            raise ValueError(f'Invalid mode: {mode!r}')
        resp = self._post_put({'target_temperature_type': mode})
        return resp.json()

    def start_fan(self, duration: int = 1800):
        """
        :param duration: Number of seconds for which the fan should run
        :return: The parsed response
        """
        timeout = int(time.time()) + duration
        log.debug(f'Submitting fan start request with duration={format_duration(duration)} => end time of {timeout}')
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
        payload = {'objects': [{'object_key': f'energy_latest.{self.serial}'}]}
        with self.session.transport_url() as client:
            return client.post('v5/subscribe', json=payload).json()

    def get_weather(self, zip_code: Union[str, int] = None, country_code: str = 'US'):
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
            resp = self.session.app_launch().json()
            location = next(iter(resp['weather_for_structures'].values()))['location']
            zip_code = location['zip']
            country_code = country_code or location['country']

        with self.session.nest_url() as client:
            return client.get(f'api/0.1/weather/forecast/{zip_code},{country_code}').json()

    def get_schedule(self) -> NestSchedule:
        raw = self.session.app_launch(['schedule']).json()['updated_buckets']
        return NestSchedule(self, raw)


class NestWebSession:
    _nest_host_port = ('home.nest.com', None)

    def __init__(
        self, config: NestConfig, reauth: bool = False, no_store_prompt: bool = False, update_password: bool = False
    ):
        self.config = config
        self.cache_path = get_user_cache_dir('nest').joinpath('session.pickle')
        self.client = RequestsClient(NEST_URL, user_agent_fmt=USER_AGENT_CHROME, headers={'Referer': NEST_URL})
        self._lock = RLock()
        self.expiry = None
        self._user_id = None
        self._reauth = reauth
        self._no_store_pw = no_store_prompt
        self._update_pw = update_password

    @property
    def user_id(self):
        if self._user_id is None:
            self._maybe_refresh_login()
        return self._user_id

    @user_id.setter
    def user_id(self, value):
        self._user_id = value

    @contextmanager
    def transport_url(self) -> ContextManager[RequestsClient]:
        with self._lock:
            self._maybe_refresh_login()
            log.debug('Using host:port={}:{}'.format(*self._transport_host_port))
            self.client.host, self.client.port = self._transport_host_port
            yield self.client

    @contextmanager
    def nest_url(self) -> ContextManager[RequestsClient]:
        with self._lock:
            self._maybe_refresh_login()
            log.debug('Using host:port={}:{}'.format(*self._nest_host_port))
            self.client.host, self.client.port = self._nest_host_port
            yield self.client

    @cached_property
    def service_urls(self):
        return self.app_launch().json()['service_urls']

    @cached_property
    def _transport_host_port(self):
        transport_url = urlparse(self.service_urls['urls']['transport_url'])
        return transport_url.hostname, transport_url.port

    def _maybe_refresh_login(self):
        with self._lock:
            if self.expiry is None or self.expiry < datetime.now(TZ_LOCAL):
                for key in ('service_urls', '_transport_host_port'):
                    try:
                        del self.__dict__[key]
                    except KeyError:
                        pass

                if 'oauth' in self.config:
                    try:
                        self._load_cached()
                    except SessionExpired as e:
                        log.debug(e)
                        self._login_via_google()
                else:
                    self._login_via_nest()

    def _load_cached(self):
        if self._reauth:
            raise SessionExpired('Forced reauth')
        elif self.cache_path.exists():
            with self.cache_path.open('rb') as f:
                try:
                    expiry, userid, jwt_token, cookies = pickle.load(f)
                except (TypeError, ValueError) as e:
                    raise SessionExpired(f'Found a cached session, but encountered an error loading it: {e}')

            if expiry < datetime.now(TZ_LOCAL) or any(cookie.expires < time.time() for cookie in cookies):
                raise SessionExpired('Found a cached session, but it expired')

            self._register_session(expiry, userid, jwt_token, cookies)
            log.debug(f'Loaded session for user={userid} with expiry={localize(expiry)}')
        else:
            raise SessionExpired('No cached session was found')

    def _register_session(self, expiry: datetime, userid: str, jwt_token: str, cookies=None, save: bool = False):
        self.expiry = expiry
        self.user_id = userid
        self.client.session.headers['Authorization'] = f'Basic {jwt_token}'
        if cookies is not None:
            for cookie in cookies:
                self.client.session.cookies.set_cookie(cookie)

        if save:
            if not self.cache_path.exists():
                self.cache_path.parent.mkdir(parents=True)
            log.debug(f'Saving session info in cache: {self.cache_path}')
            with self.cache_path.open('wb') as f:
                pickle.dump((expiry, userid, jwt_token, list(self.client.session.cookies)), f)

    def _get_oauth_token(self) -> str:
        headers = {
            'Sec-Fetch-Mode': 'cors',
            'X-Requested-With': 'XmlHttpRequest',
            'Referer': 'https://accounts.google.com/o/oauth2/iframe',
            'cookie': self.config.oauth_cookie,
        }
        # token_url = self.config.get('oauth', 'token_url', 'OAuth Token URL', required=True)
        # resp = self.client.session.get(token_url, headers=headers)
        params = {
            'action': ['issueToken'],
            'response_type': ['token id_token'],
            'login_hint': [self.config.oauth_login_hint],
            'client_id': [self.config.oauth_client_id],
            'origin': [NEST_URL],
            'scope': ['openid profile email https://www.googleapis.com/auth/nest-account'],
            'ss_domain': [NEST_URL],
        }
        resp = self.client.session.get(OAUTH_URL, params=params, headers=headers).json()
        log.log(9, 'Received OAuth response: {}'.format(json.dumps(resp, indent=4, sort_keys=True)))
        return resp['access_token']

    def _login_via_google(self):
        token = self._get_oauth_token()
        headers = {'Authorization': f'Bearer {token}', 'x-goog-api-key': NEST_API_KEY}
        params = {
            'embed_google_oauth_access_token': True, 'expire_after': '3600s',
            'google_oauth_access_token': token, 'policy_id': 'authproxy-oauth-policy',
        }
        resp = self.client.session.post(JWT_URL, params=params, headers=headers).json()
        log.log(9, 'Initialized session; response: {}'.format(json.dumps(resp, indent=4, sort_keys=True)))
        claims = resp['claims']
        expiry = datetime_with_tz(claims['expirationTime'], '%Y-%m-%dT%H:%M:%S.%fZ', TZ_UTC).astimezone(TZ_LOCAL)
        self._register_session(expiry, claims['subject']['nestId']['id'], resp['jwt'], save=True)
        log.debug(f'Initialized session for user={self.user_id!r} with expiry={localize(expiry)}')

    def _login_via_nest(self):
        """This login method is deprecated"""
        log.debug(f'Initializing session for email={self.config.email!r}')
        resp = self.client.post('session', json={'email': self.config.email, 'password': self.__password})
        info = resp.json()
        self.user_id = info['userid']
        self.client.session.headers['Authorization'] = 'Basic {}'.format(info['access_token'])
        self.client.session.headers['X-nl-user-id'] = self.user_id
        self.expiry = expiry = datetime_with_tz(info['expires_in'], '%a, %d-%b-%Y %H:%M:%S %Z')
        log.debug(f'Initialized legacy session for user={self.user_id!r} with expiry={localize(expiry)}')
        transport_url = urlparse(info['urls']['transport_url'])
        self.__dict__['_transport_host_port'] = (transport_url.hostname, transport_url.port)

    @cached_property
    def __password(self) -> str:
        if keyring is not None:
            password = keyring.get_password('NestWebClient', self.config.email)
            if self._update_pw and password:
                keyring.delete_password('NestWebClient', self.config.email)
                password = None
            if password is None:
                password = getpass()
                if not self._no_store_pw and get_input(f'Store password in keyring ({KEYRING_URL})?'):
                    keyring.set_password('NestWebClient', self.config.email, password)
                    log.info('Stored password in keyring')
            else:
                log.debug('Using password from keyring')
        else:
            password = getpass()
        return password

    def app_launch(self, bucket_types: list[str] = None) -> 'Response':
        with self.nest_url() as client:
            payload = {'known_bucket_types': bucket_types or [], 'known_bucket_versions': []}
            return client.post(f'api/0.1/user/{self.user_id}/app_launch', json=payload)


def _filter_capabilities(info):
    capabilities = {key.split('_', 1)[1]: val for key, val in info['device'].items() if key.startswith('has_')}
    capabilities['fan_capabilities'] = info['device']['fan_capabilities']
    return capabilities
