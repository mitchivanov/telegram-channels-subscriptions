import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram import types
from aiogram.fsm.context import FSMContext
from app.main import process_successful_payment
from app.database import UserSubscription, PaymentError, User, SubscriptionPlan
from sqlalchemy import select

@pytest.mark.asyncio
async def test_successful_payment_creates_subscription(session):
    # Setup: Create a plan
    plan = SubscriptionPlan(
        name="Test Plan",
        price=100,
        duration_days=30,
        channel_id="-100123456789"
    )
    session.add(plan)
    await session.commit()

    # Mock message and state
    user = types.User(id=123456789, is_bot=False, first_name="TestUser")
    chat = types.Chat(id=123456789, type="private")

    payment_info = MagicMock()
    payment_info.invoice_payload = f"plan_{plan.id}"
    payment_info.provider_payment_charge_id = "charge_123"
    payment_info.total_amount = 100
    payment_info.currency = "RUB"

    message = AsyncMock(spec=types.Message)
    message.from_user = user
    message.chat = chat
    message.successful_payment = payment_info
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    # Execute
    await process_successful_payment(message, state)

    # Verify
    result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == 1)) # User ID might be 1 if it's the first user
    sub = result.scalar_one_or_none()

    assert sub is not None
    assert sub.plan_id == plan.id
    assert sub.is_active == True
    assert sub.provider_payment_charge_id == "charge_123"

    # Check that success message was sent
    message.answer.assert_called_once()
    assert "Оплата успешно выполнена" in message.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_payment_error_handling(session, monkeypatch):
    # Setup: Create a plan
    plan = SubscriptionPlan(name="Test Plan", price=100, duration_days=30)
    session.add(plan)
    await session.commit()

    # Mock message
    user = types.User(id=987654321, is_bot=False, first_name="ErrorUser")
    message = AsyncMock(spec=types.Message)
    message.from_user = user
    message.successful_payment = MagicMock()
    message.successful_payment.invoice_payload = f"plan_{plan.id}"
    message.successful_payment.provider_payment_charge_id = "charge_error"
    message.successful_payment.total_amount = 100
    message.successful_payment.currency = "RUB"
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    # Simulate DB error during subscription creation
    # We need to mock subscription_service.create_subscription to raise exception
    from app.main import subscription_service

    original_create = subscription_service.create_subscription
    subscription_service.create_subscription = AsyncMock(side_effect=ValueError("Simulated DB Error"))

    try:
        # Execute
        await process_successful_payment(message, state)

        # Verify PaymentError created
        result = await session.execute(select(PaymentError).where(PaymentError.telegram_user_id == str(user.id)))
        error = result.scalar_one_or_none()

        assert error is not None
        assert "Simulated DB Error" in error.error_message
        assert error.provider_payment_charge_id == "charge_error"

        # Check user notification
        message.answer.assert_called()
        assert "техническая ошибка" in message.answer.call_args[0][0]

    finally:
        # Restore original method
        subscription_service.create_subscription = original_create
