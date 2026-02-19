import pytest
from unittest.mock import AsyncMock
from app.database import async_init_db, get_async_session_maker, User, SubscriptionPlan, UserSubscription
from app.subscription_service import SubscriptionService
from app.subscription_manager import SubscriptionManager
import asyncio

@pytest.mark.asyncio
async def test_create_channel_invite_retry(monkeypatch):
    engine = await async_init_db()
    session_maker = get_async_session_maker(engine)
    service = SubscriptionService(session_maker)
    # Создать пользователя
    user = await service.get_user_by_telegram_id('99999')

    # Create Plan and Subscription (Required for create_channel_invite)
    async with session_maker() as session:
        plan = SubscriptionPlan(name="Test", price=100, duration_days=30, channel_id="test_channel")
        session.add(plan)
        await session.commit()
        manager = SubscriptionManager(session)
        await manager.subscribe_user(user.id, plan.id)

    # Мокаем self.bot.create_chat_invite_link
    attempts = []
    async def fail_then_succeed(*args, **kwargs):
        if len(attempts) < 2:
            attempts.append(1)
            raise Exception('fail')
        class Dummy:
            invite_link = 'test_link'
        return Dummy()
    service.bot = AsyncMock()
    service.bot.create_chat_invite_link = fail_then_succeed
    # Pass telegram_user_id, not DB id
    link = await service.create_channel_invite('test_channel', user.telegram_user_id, max_retries=3)
    assert link == 'test_link'
    assert len(attempts) == 2 