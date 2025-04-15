import pytest
from app.database import async_init_db, get_async_session_maker, User, SubscriptionPlan
from app.subscription_manager import SubscriptionManager
from datetime import datetime

@pytest.mark.asyncio
async def test_subscribe_and_extend():
    engine = await async_init_db()
    session_maker = get_async_session_maker(engine)
    async with session_maker() as session:
        manager = SubscriptionManager(session)
        # Создать пользователя
        user = User(telegram_user_id='54321', is_active=True)
        session.add(user)
        await session.commit()
        # Создать тариф
        plan = SubscriptionPlan(name='Тест', price=100, duration_days=1, channel_id='test')
        session.add(plan)
        await session.commit()
        # Подписать пользователя
        sub = await manager.subscribe_user(user.id, plan.id)
        assert sub.is_active
        # Продлить подписку
        old_end = sub.end_date
        sub2 = await manager.extend_subscription(sub.id, 2)
        assert sub2.end_date > old_end 