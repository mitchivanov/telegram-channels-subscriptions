import pytest
from unittest.mock import MagicMock
from app.main import process_join_request
from app.database import User, SubscriptionPlan, UserSubscription
from aiogram import types
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_process_join_request_valid(session):
    plan = SubscriptionPlan(name="Test Plan", price=100, duration_days=30, channel_id="-1001111111111")
    session.add(plan)
    await session.commit()
    
    user = User(telegram_user_id="12345", is_active=True)
    session.add(user)
    await session.commit()
    
    sub = UserSubscription(
        user_id=user.id, plan_id=plan.id, is_active=True,
        start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=30),
        invite_link="https://t.me/+valid_link"
    )
    session.add(sub)
    await session.commit()

    join_request = MagicMock()
    join_request.chat.id = -1001111111111
    join_request.from_user.id = 12345
    join_request.invite_link.invite_link = "https://t.me/+valid_link"

    import app.main
    await process_join_request(join_request)

    # Проверяем вызовы мока из main
    app.main.bot.approve_chat_join_request.assert_called_once_with(chat_id=-1001111111111, user_id=12345)
    app.main.bot.revoke_chat_invite_link.assert_called_once_with(chat_id=-1001111111111, invite_link="https://t.me/+valid_link")
    app.main.bot.send_message.assert_called()

@pytest.mark.asyncio
async def test_process_join_request_invalid_user(session):  # <--- Добавили session сюда!
    join_request = MagicMock()
    join_request.chat.id = -1001111111111
    join_request.from_user.id = 99999
    join_request.invite_link.invite_link = "https://t.me/+invalid_link"

    import app.main
    await process_join_request(join_request)

    # Проверяем вызов отклонения
    app.main.bot.decline_chat_join_request.assert_called_once_with(chat_id=-1001111111111, user_id=99999)