"""
Cron utilities specifically for working with Nest thermostat schedules

:author: Doug Skrypa
"""

import logging
from typing import Iterator, Tuple

from ds_tools.utils.cron import CronSchedule

__all__ = ['NestCronSchedule']
log = logging.getLogger(__name__)


class NestCronSchedule(CronSchedule):
    @classmethod
    def from_cron(cls, cron_str: str) -> 'NestCronSchedule':
        cron = super().from_cron(cron_str)
        if any(not any(getattr(cron, attr).values()) for attr in ('_second', '_day', '_month', '_weeks')):
            raise ValueError('Nest schedules only support minutes, hours, and days of the week')
        # noinspection PyTypeChecker
        return cron

    def __iter__(self) -> Iterator[Tuple[int, int]]:
        """
        :return iterator: Iterator that yields tuples of (day of week, time of day [seconds])
        """
        for dow, dow_enabled in self._dow.items():
            if dow_enabled:
                for hour, hour_enabled in self._hour.items():
                    if hour_enabled:
                        for minute, min_enabled in self._minute.items():
                            if min_enabled:
                                yield dow, (hour * 60 + minute) * 60
