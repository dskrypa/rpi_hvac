"""
Exceptions for working with Nest thermostats

:author: Doug Skrypa
"""


class SessionExpired(Exception):
    pass


class TimeNotFound(ValueError):
    pass
