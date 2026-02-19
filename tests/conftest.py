import os
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
import asyncio

# Add app directory to sys.path so that 'import keyboards' works
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

# Set environment variables for testing before importing app modules
os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456789:AABBCCDDEEFFaabbccddeeff1234567890')
os.environ.setdefault('TELEGRAM_PAYMENT_TOKEN', '123456789:TEST:1234567890')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')
os.environ.setdefault('CELERY_BROKER_URL', 'redis://localhost:6379/0')
os.environ.setdefault('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
os.environ.setdefault('BASIC_CHANNEL_ID', '-1001111111111')
os.environ.setdefault('PREMIUM_CHANNEL_ID', '-1002222222222')
os.environ.setdefault('ADMIN_USER_IDS', '123456789')
os.environ.setdefault('PAYMENT_TEST_MODE', 'True')

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.subscription_service import subscription_service

# Fixture for event loop to handle async fixtures correctly
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

# Automatically patch create_async_engine in app.database to use StaticPool for :memory:
@pytest.fixture(autouse=True)
def patch_db_engine(monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine as original_create

    def mocked_create(*args, **kwargs):
        if ':memory:' in str(args[0]):
            kwargs['poolclass'] = StaticPool
            kwargs['connect_args'] = {"check_same_thread": False}
        return original_create(*args, **kwargs)

    monkeypatch.setattr("app.database.create_async_engine", mocked_create)

@pytest_asyncio.fixture(scope="function")
async def db_session_maker():
    # Use in-memory SQLite for testing to ensure isolation
    # StaticPool ensures the same connection is used for all sessions, preserving data in memory
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Override the global subscription_service session maker
    original_maker = subscription_service.async_session_maker
    subscription_service.async_session_maker = session_maker

    # Mock bot to prevent actual API calls
    # Mock bot to prevent actual API calls
    # We also mock the bot in app.main using sys.modules or just patching subscription_service.bot since main uses it
    # However, app.main imports 'bot' globally. We need to patch that too.
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.ban_chat_member = AsyncMock()
    mock_bot.unban_chat_member = AsyncMock()
    mock_bot.revoke_chat_invite_link = AsyncMock()
    mock_bot.create_chat_invite_link = AsyncMock()

    subscription_service.bot = mock_bot

    # Also patch app.main.bot if it exists in loaded modules
    import sys
    if 'app.main' in sys.modules:
        sys.modules['app.main'].bot = mock_bot

    # Mock invite link object
    mock_invite = MagicMock()
    mock_invite.invite_link = "https://t.me/+invite_link_mock"
    subscription_service.bot.create_chat_invite_link.return_value = mock_invite

    yield session_maker

    # Teardown
    subscription_service.async_session_maker = original_maker
    await engine.dispose()

@pytest_asyncio.fixture(scope="function")
async def session(db_session_maker):
    async with db_session_maker() as session:
        yield session
