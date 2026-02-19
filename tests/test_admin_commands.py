import pytest
from unittest.mock import AsyncMock, ANY
from app.main import show_payment_errors, resolve_payment_error
from app.database import PaymentError
from aiogram import types
from aiogram.fsm.context import FSMContext

@pytest.mark.asyncio
async def test_resolve_payment_error(session):
    error = PaymentError(
        telegram_user_id="123", provider_payment_charge_id="charge_1",
        error_message="Test", is_resolved=False
    )
    session.add(error)
    await session.commit()

    # Убрали spec=...
    message = AsyncMock()
    message.from_user.id = 123456789  # ID админа из .env
    message.text = f"/resolve_payment_error {error.id} Выдали руками"
    state = AsyncMock()

    import app.main
    app.main.bot.send_message = AsyncMock()

    await resolve_payment_error(message, state)

    await session.refresh(error)
    assert error.is_resolved is True
    assert error.resolution_notes == "Выдали руками"
    # Используем ANY для любых строк
    app.main.bot.send_message.assert_called_with(chat_id="123", text=ANY)