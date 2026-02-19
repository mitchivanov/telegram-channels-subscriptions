import pytest
from unittest.mock import AsyncMock, MagicMock, ANY
from aiogram import types
from app.main import process_pre_checkout_query

@pytest.mark.asyncio
async def test_pre_checkout_valid():
    query = MagicMock()
    query.id = "query_123"
    query.invoice_payload = "plan_1"

    import app.main
    app.main.bot.answer_pre_checkout_query = AsyncMock()

    await process_pre_checkout_query(query)
    app.main.bot.answer_pre_checkout_query.assert_called_once_with("query_123", ok=True)

@pytest.mark.asyncio
async def test_pre_checkout_invalid():
    query = MagicMock()
    query.id = "query_456"
    query.invoice_payload = "wrong_payload"

    import app.main
    app.main.bot.answer_pre_checkout_query = AsyncMock()

    await process_pre_checkout_query(query)
    # Используем ANY вместо pytest.approx(str)
    app.main.bot.answer_pre_checkout_query.assert_called_once_with("query_456", ok=False, error_message=ANY)