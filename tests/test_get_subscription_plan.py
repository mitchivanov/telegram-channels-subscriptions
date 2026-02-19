import sys
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Mock modules before importing the app
mock_modules = [
    'sqlalchemy',
    'sqlalchemy.ext.asyncio',
    'sqlalchemy.orm',
    'sqlalchemy.future',
    'sqlalchemy.exc',
    'aiogram',
    'aiogram.types',
    'aiogram.utils',
    'dotenv',
    'asyncpg',
]
for module_name in mock_modules:
    if module_name not in sys.modules:
        sys.modules[module_name] = MagicMock()

import os
os.environ['DATABASE_URL'] = 'postgresql+asyncpg://user:pass@localhost/db'
os.environ['BASIC_CHANNEL_ID'] = '123'
os.environ['PREMIUM_CHANNEL_ID'] = '456'
os.environ['PAYMENT_TEST_MODE'] = 'True'

import pytest
from app.subscription_service import SubscriptionService

def test_get_subscription_plan_standard_duration():
    async def run_test():
        mock_session_maker = MagicMock()
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        service = SubscriptionService(async_session_maker=mock_session_maker)

        mock_plan = MagicMock()
        mock_plan.name = "Базовый 30 дней"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_plan
        mock_session.execute.return_value = mock_result

        with patch('app.subscription_service.select') as mock_select, \
             patch('app.subscription_service.SubscriptionPlan') as mock_plan_model:

            plan = await service.get_subscription_plan('basic_subscription', '30_days')

            assert plan == mock_plan
            mock_select.assert_called_with(mock_plan_model)
            # Verify plan name formation
            mock_plan_model.name.__eq__.assert_called_with("Базовый 30 дней")

    asyncio.run(run_test())

def test_get_subscription_plan_test_mode_duration():
    async def run_test():
        mock_session_maker = MagicMock()
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        service = SubscriptionService(async_session_maker=mock_session_maker)

        mock_plan = MagicMock()
        mock_plan.name = "Премиум 5 минут"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_plan
        mock_session.execute.return_value = mock_result

        with patch('app.subscription_service.select') as mock_select, \
             patch('app.subscription_service.SubscriptionPlan') as mock_plan_model:

            plan = await service.get_subscription_plan('premium_subscription', '5_min')

            assert plan == mock_plan
            # Verify plan name formation for 5 minutes
            mock_plan_model.name.__eq__.assert_called_with("Премиум 5 минут")

    asyncio.run(run_test())

def test_get_subscription_plan_invalid_type():
    async def run_test():
        service = SubscriptionService()
        with pytest.raises(KeyError):
            await service.get_subscription_plan('invalid_type', '30_days')

    asyncio.run(run_test())

def test_get_subscription_plan_invalid_duration():
    async def run_test():
        service = SubscriptionService()
        with pytest.raises(KeyError):
            await service.get_subscription_plan('basic_subscription', '99_days')

    asyncio.run(run_test())

def test_get_subscription_plan_not_found():
    async def run_test():
        mock_session_maker = MagicMock()
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        service = SubscriptionService(async_session_maker=mock_session_maker)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch('app.subscription_service.select'), \
             patch('app.subscription_service.SubscriptionPlan'):

            with pytest.raises(ValueError, match="не найден"):
                await service.get_subscription_plan('basic_subscription', '30_days')

    asyncio.run(run_test())
