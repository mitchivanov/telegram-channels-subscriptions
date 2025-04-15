import pytest
from app.database import async_init_db, get_async_session_maker, User
from app.subscription_service import SubscriptionService
import asyncio

@pytest.mark.asyncio
async def test_create_and_get_user():
    engine = await async_init_db()
    session_maker = get_async_session_maker(engine)
    service = SubscriptionService(session_maker)
    user = await service.get_user_by_telegram_id('12345')
    assert isinstance(user, User)
    user2 = await service.get_user_by_telegram_id('12345')
    assert user.id == user2.id 