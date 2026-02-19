import pytest
from unittest.mock import AsyncMock, MagicMock
from app.main import confirm_cancel_subscription
from app.database import User, SubscriptionPlan, UserSubscription
from aiogram import types
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_confirm_cancel_subscription_success(session):
    plan = SubscriptionPlan(name="Plan", price=100, duration_days=30, channel_id="-1009999")
    session.add(plan)
    user = User(telegram_user_id="33333", is_active=True)
    session.add(user)
    await session.commit()
    
    sub = UserSubscription(user_id=user.id, plan_id=plan.id, is_active=True, end_date=datetime.utcnow() + timedelta(days=10))
    session.add(sub)
    await session.commit()

    # Убрали spec=...
    callback = AsyncMock()
    callback.from_user.id = 33333
    state = AsyncMock()

    from app.subscription_service import subscription_service
    await confirm_cancel_subscription(callback, state)

    # Проверка Telegram API (Пользователя кикнули)
    subscription_service.bot.ban_chat_member.assert_called_once()
    subscription_service.bot.unban_chat_member.assert_called_once()
    
    # Проверка БД
    await session.refresh(sub)
    assert sub.is_active is False