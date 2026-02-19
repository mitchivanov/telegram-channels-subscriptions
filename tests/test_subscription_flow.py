import pytest
import asyncio
from datetime import datetime, timedelta
from app.subscription_service import subscription_service
from app.database import User, SubscriptionPlan, UserSubscription
from sqlalchemy import select
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_full_subscription_lifecycle(session):
    # Setup
    plan = SubscriptionPlan(name="Monthly", price=1000, duration_days=30, channel_id="-123")
    session.add(plan)
    await session.commit()

    # 1. Create User & Subscription
    user_id = 123456
    sub_id = await subscription_service.create_subscription(user_id, plan_id=plan.id)

    result = await session.execute(select(UserSubscription).where(UserSubscription.id == sub_id))
    sub = result.scalar_one()
    assert sub.is_active
    assert sub.reminder_sent is False

    # 2. Simulate 24h Reminder
    # Move end_date to be in 23 hours (so < 24h)
    sub.end_date = datetime.utcnow() + timedelta(hours=23)
    session.add(sub)
    await session.commit()

    # Mock bot.send_message
    subscription_service.bot.send_message.reset_mock()
    await subscription_service.send_subscription_reminders()

    # Verify reminder sent
    subscription_service.bot.send_message.assert_called()
    call_kwargs = subscription_service.bot.send_message.call_args[1]
    assert "завтра Ваша подписка истекает" in call_kwargs['text']

    # Verify flag updated in DB
    await session.refresh(sub)
    assert sub.reminder_sent is True

    # 3. Simulate Expiration
    # Move end_date to past
    sub.end_date = datetime.utcnow() - timedelta(minutes=1)
    session.add(sub)
    await session.commit()

    # 4. Check Expired Check (Deactivation)
    await subscription_service.check_expired_subscriptions()

    # Verify removed access
    subscription_service.bot.ban_chat_member.assert_called()
    subscription_service.bot.unban_chat_member.assert_called()

    # Verify DB state
    await session.refresh(sub)
    assert sub.is_active is False

    # 5. Check Expired Reminder
    subscription_service.bot.send_message.reset_mock()
    await subscription_service.send_expired_reminders()

    subscription_service.bot.send_message.assert_called()
    call_kwargs = subscription_service.bot.send_message.call_args[1]
    assert "подписка истекла" in call_kwargs['text']

    await session.refresh(sub)
    assert sub.expired_reminder_sent is True
