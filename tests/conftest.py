import os
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

# 1. Запоминаем URL БД до того, как любые импорты приложения (и локальный .env) смогут его перезаписать.
# В Docker это будет URL с хостом 'db:5432', как указано в docker-compose.test.yml
ACTUAL_DB_URL = os.environ.get('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

# Добавляем папку app в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

# Заглушки окружения для тестов
os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456789:AABBCCDDEEFFaabbccddeeff1234567890')
os.environ.setdefault('TELEGRAM_PAYMENT_TOKEN', '123456789:TEST:1234567890')
os.environ.setdefault('BASIC_CHANNEL_ID', '-1001111111111')
os.environ.setdefault('PREMIUM_CHANNEL_ID', '-1002222222222')
os.environ.setdefault('ADMIN_USER_IDS', '123456789')
os.environ.setdefault('PAYMENT_TEST_MODE', 'True')

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool, NullPool
from app.database import Base

is_sqlite = ':memory:' in ACTUAL_DB_URL
engine_kwargs = {"echo": False}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine_kwargs["poolclass"] = StaticPool
else:
    # Отключаем пулинг для тестов с Postgres, чтобы избежать зависших соединений
    engine_kwargs["poolclass"] = NullPool

# 2. Создаем ЕДИНЫЙ глобальный тестовый движок
test_engine = create_async_engine(ACTUAL_DB_URL, **engine_kwargs)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

# 3. ЖЕСТКИЙ МОНКИ-ПАТЧИНГ: подменяем функции создания сессий в приложении ДО импорта самих модулей
import app.database
app.database.get_async_engine = lambda: test_engine
app.database.get_async_session_maker = lambda engine=None: test_session_maker
app.database.DATABASE_URL = ACTUAL_DB_URL  # Принудительно ставим правильный URL

# Теперь безопасно импортируем приложение (оно подхватит наш тестовый движок)
from app.subscription_service import subscription_service
import app.main

# На всякий случай подменяем уже созданные объекты в сервисах
subscription_service.async_session_maker = test_session_maker
if hasattr(app.main, 'session_maker'):
    app.main.session_maker = test_session_maker

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Создает структуру таблиц в базе данных один раз перед запуском всех тестов"""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    # По завершению всех тестов удаляем таблицы
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()

@pytest_asyncio.fixture(scope="function")
async def db_session_maker():
    """Очищает данные из таблиц перед КАЖДЫМ тестом (без удаления схемы) и мокает бота"""
    async with test_engine.begin() as conn:
        # Очищаем все строки из таблиц, чтобы тесты 100% не конфликтовали (решает проблему Duplicate Key)
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    
    # Настраиваем глобальные моки для Telegram Bot API
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.ban_chat_member = AsyncMock()
    mock_bot.unban_chat_member = AsyncMock()
    mock_bot.revoke_chat_invite_link = AsyncMock()
    mock_bot.create_chat_invite_link = AsyncMock()
    mock_bot.approve_chat_join_request = AsyncMock()
    mock_bot.decline_chat_join_request = AsyncMock()
    
    mock_invite = MagicMock()
    mock_invite.invite_link = "https://t.me/+invite_link_mock"
    mock_bot.create_chat_invite_link.return_value = mock_invite

    # Подменяем реального бота в модулях на мок
    original_main_bot = app.main.bot
    original_service_bot = subscription_service.bot
    
    app.main.bot = mock_bot
    subscription_service.bot = mock_bot

    yield test_session_maker

    # Возвращаем бота обратно после теста
    app.main.bot = original_main_bot
    subscription_service.bot = original_service_bot

@pytest_asyncio.fixture(scope="function")
async def session(db_session_maker):
    """Предоставляет чистую асинхронную сессию БД для самого теста"""
    async with db_session_maker() as session:
        yield session