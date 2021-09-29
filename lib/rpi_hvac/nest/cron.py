"""
Cron utilities specifically for working with Nest thermostat schedules

:author: Doug Skrypa
"""

import logging
from typing import Iterator

from ds_tools.utils.cron import CronSchedule

__all__ = ['NestCronSchedule']
log = logging.getLogger(__name__)


class NestCronSchedule(CronSchedule):
    @classmethod
    def from_cron(cls, cron_str: str) -> 'NestCronSchedule':
        cron = super().from_cron(cron_str)
        if attr := next((attr for attr in ('day', 'month', 'week') if not getattr(cron, attr).all()), None):
            bad = getattr(cron, attr)
            raise ValueError(f'Nest schedules only support minutes, hours, and days of the week - {bad=!r}')
        return cron  # noqa

    def __iter__(self) -> Iterator[tuple[int, int]]:
        """
        :return iterator: Iterator that yields tuples of (day of week, time of day [seconds])
        """
        for dow in self.dow:
            for hour in self.hour:
                for minute in self.minute:
                    yield dow, (hour * 60 + minute) * 60
