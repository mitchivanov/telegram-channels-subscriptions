import pytest
from unittest.mock import AsyncMock, ANY
from app.main import start_command, manage_subscription, details_command
from app.database import User, SubscriptionPlan, UserSubscription
from aiogram import types
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_start_command_new_user(session):
    # Убрали spec=...
    message = AsyncMock()
    message.from_user.id = 11111
    message.from_user.first_name = "Test"
    state = AsyncMock()

    await start_command(message, state)

    # Используем ANY для клавиатуры
    message.answer.assert_any_call("🔥Доступ к каналу с товарами от 60₽", reply_markup=ANY)

@pytest.mark.asyncio
async def test_details_command_with_active_sub(session):
    plan = SubscriptionPlan(name="Plan", price=100, duration_days=30, channel_id="-100123456789")
    session.add(plan)
    user = User(telegram_user_id="22222", is_active=True)
    session.add(user)
    await session.commit()
    
    sub = UserSubscription(
        user_id=user.id, plan_id=plan.id, is_active=True,
        start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10)
    )
    session.add(sub)
    await session.commit()

    message = AsyncMock()
    message.from_user.id = 22222

    await details_command(message)

    message.answer.assert_called_once()
    call_text = message.answer.call_args[0][0]
    assert "https://t.me/c/123456789/1" in call_text