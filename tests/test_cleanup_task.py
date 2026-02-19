import pytest
from unittest.mock import AsyncMock, MagicMock
from app.database import User, SubscriptionPlan, UserSubscription
from aiogram.types import ChatMemberMember, User as AiogramUser
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_force_cleanup_expired(session):
    plan = SubscriptionPlan(name="Plan", price=100, duration_days=30, channel_id="-100777")
    session.add(plan)
    user = User(telegram_user_id="88888", is_active=True)
    session.add(user)
    await session.commit()
    
    # Подписка истекла 3 часа назад
    expired_date = datetime.utcnow() - timedelta(hours=3)
    sub = UserSubscription(user_id=user.id, plan_id=plan.id, is_active=True, start_date=expired_date, end_date=expired_date)
    session.add(sub)
    await session.commit()

    from app.subscription_service import subscription_service
    
    # Мокаем проверку: юзер все еще числится в канале
    mock_member = ChatMemberMember(user=AiogramUser(id=88888, is_bot=False, first_name="A"), status="member")
    subscription_service.bot.get_chat_member.return_value = mock_member

    await subscription_service.force_cleanup_expired()

    # Проверяем, что его удалили
    subscription_service.bot.ban_chat_member.assert_called_with(chat_id="-100777", user_id="88888")
    
    # Проверяем статус в БД
    await session.refresh(sub)
    assert sub.is_active is False