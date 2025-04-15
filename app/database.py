from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import os

# Создаем базовый класс для наших моделей
Base = declarative_base()

# Модель тарифного плана
class SubscriptionPlan(Base):
    __tablename__ = 'subscription_plans'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Integer, nullable=False)  # Цена в копейках/центах
    duration_days = Column(Integer, nullable=False)  # Длительность подписки в днях
    channel_id = Column(String)  # ID канала для подписки
    
    # Отношение с подписками пользователей
    subscriptions = relationship("UserSubscription", back_populates="plan")
    
    def __repr__(self):
        return f"<SubscriptionPlan(id={self.id}, name='{self.name}', price={self.price/100})>"

# Модель пользователя
class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(String, nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    
    # Отношение с подписками пользователя
    subscriptions = relationship("UserSubscription", back_populates="user")
    
    def __repr__(self):
        return f"<User(id={self.id}, telegram_user_id='{self.telegram_user_id}')>"

# Модель подписки пользователя
class UserSubscription(Base):
    __tablename__ = 'user_subscriptions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    plan_id = Column(Integer, ForeignKey('subscription_plans.id'), nullable=False)
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    invite_link = Column(String)  # Ссылка-приглашение в канал
    reminder_sent = Column(Boolean, default=False)  # Было ли отправлено напоминание о скором окончании
    
    # Отношения
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan", back_populates="subscriptions")
    
    def __repr__(self):
        return f"<UserSubscription(id={self.id}, user_id={self.user_id}, plan_id={self.plan_id}, active={self.is_active})>"

# Асинхронное подключение к PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Не задана переменная окружения DATABASE_URL. Укажите её в .env!")

def get_async_engine():
    return create_async_engine(DATABASE_URL, echo=True)

def get_async_session_maker(engine=None):
    if engine is None:
        engine = get_async_engine()
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Асинхронная инициализация базы данных
async def async_init_db():
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine




