import pytest
import sys
import os

# Ensure app is in path if running directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from app.scheduler import setup_scheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

@pytest.mark.asyncio
async def test_scheduler_setup():
    scheduler = setup_scheduler()

    assert isinstance(scheduler, AsyncIOScheduler)
    assert str(scheduler.timezone) == 'UTC'

    # Check if jobs are added
    jobs = scheduler.get_jobs()
    job_ids = [job.id for job in jobs]

    expected_jobs = [
        'send_registration_reminders',
        'send_subscription_reminders',
        'send_last_day_reminders',
        'send_expired_reminders',
        'check_expired_subscriptions',
        'force_cleanup_expired'
    ]

    for job_id in expected_jobs:
        assert job_id in job_ids

    # Check intervals
    job_map = {job.id: job for job in jobs}

    # apscheduler intervals are timedeltas
    assert job_map['send_registration_reminders'].trigger.interval.total_seconds() == 600.0
    assert job_map['send_subscription_reminders'].trigger.interval.total_seconds() == 3600.0
    assert job_map['check_expired_subscriptions'].trigger.interval.total_seconds() == 300.0
