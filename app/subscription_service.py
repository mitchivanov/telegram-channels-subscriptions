from app.database import async_init_db, get_async_session_maker, User, SubscriptionPlan, UserSubscription
from app.subscription_manager import SubscriptionManager
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import logging
import asyncio
from sqlalchemy import select
import random

# Загружаем переменные окружения
load_dotenv()

PAYMENT_TEST_MODE = os.getenv('PAYMENT_TEST_MODE', 'False').lower() in ('true', '1', 't')

# Словарь соответствия callback_data и реальных значений
SUBSCRIPTION_TYPE_MAP = {
    'basic_subscription': 'Базовый',
    'premium_subscription': 'Премиум'
}

DURATION_MAP = {
    '30_days': 30
}
if PAYMENT_TEST_MODE:
    DURATION_MAP['5_min'] = 5 / (24 * 60)  # 5 минут в днях

# ID каналов для разных типов подписок из .env
BASIC_CHANNEL_ID = os.getenv('BASIC_CHANNEL_ID')
PREMIUM_CHANNEL_ID = os.getenv('PREMIUM_CHANNEL_ID')
if not BASIC_CHANNEL_ID or not PREMIUM_CHANNEL_ID:
    raise ValueError("Не заданы переменные окружения BASIC_CHANNEL_ID и PREMIUM_CHANNEL_ID. Укажите их в .env!")
CHANNEL_IDS = {
    'basic_subscription': BASIC_CHANNEL_ID,
    'premium_subscription': PREMIUM_CHANNEL_ID
}

# Словарь для хранения соответствия ссылок-приглашений и пользователей
# Формат: {invite_link: telegram_user_id}
INVITE_LINKS_MAP = {}

class SubscriptionService:
    def __init__(self, async_session_maker=None):
        self.engine = None
        self.async_session_maker = async_session_maker or get_async_session_maker()
        self.bot = None
        # self.manager = SubscriptionManager(self.session)  # manager будет переписан отдельно
        # Инициализация тарифных планов будет async
        # asyncio.create_task(self._init_subscription_plans())
    
    def set_bot(self, bot):
        """Установка экземпляра бота для работы с API Telegram"""
        self.bot = bot
    
    async def _init_subscription_plans(self):
        """Инициализация базовых тарифных планов при первом запуске"""
        async with self.async_session_maker() as session:
            result = await session.execute(select(SubscriptionPlan))
            existing_plans = result.scalars().all()
            if existing_plans:
                return
            
            # Базовый план на разные сроки
            plans = [
                # Базовые планы
                {'name': 'Базовый 30 дней', 'description': 'Базовая подписка на 30 дней', 
                 'price': 10000, 'duration_days': 30, 'channel_id': CHANNEL_IDS['basic_subscription']},

                
                # Премиум планы
                {'name': 'Премиум 30 дней', 'description': 'Премиум подписка на 30 дней', 
                 'price': 20000, 'duration_days': 30, 'channel_id': CHANNEL_IDS['premium_subscription']},

            ]
            
            if PAYMENT_TEST_MODE:
                plans.append({'name': 'Базовый 5 минут', 'description': 'Тестовая подписка на 5 минут',
                              'price': 6900, 'duration_days': 5 / (24 * 60), 'channel_id': CHANNEL_IDS['basic_subscription']})
            
            for plan_data in plans:
                plan = SubscriptionPlan(**plan_data)
                session.add(plan)
            
            await session.commit()
    
    async def get_user_by_telegram_id(self, telegram_user_id):
        """Получение пользователя по Telegram ID или создание нового"""
        async with self.async_session_maker() as session:
            result = await session.execute(select(User).where(User.telegram_user_id == str(telegram_user_id)))
            user = result.scalar_one_or_none()
            
            if not user:
                # Создаем нового пользователя
                user = User(telegram_user_id=str(telegram_user_id), is_active=True)
                session.add(user)
                await session.commit()
            
            return user
    
    async def get_subscription_plan(self, subscription_type, duration):
        """Получение подходящего плана подписки по типу и длительности"""
        # Формируем название плана из типа и длительности
        if duration == '5_min':
            plan_name = f"{SUBSCRIPTION_TYPE_MAP[subscription_type]} 5 минут"
        else:
            plan_name = f"{SUBSCRIPTION_TYPE_MAP[subscription_type]} {DURATION_MAP[duration]} дней"
        
        # Ищем план в базе данных
        async with self.async_session_maker() as session:
            result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == plan_name))
            plan = result.scalar_one_or_none()
        
        if not plan:
            raise ValueError(f"План подписки {plan_name} не найден")
        
        return plan
    
    async def create_channel_invite(self, channel_id, user_id, max_retries=3):
        """Создание защищенной ссылки-приглашения в канал
        
        Создает ссылку, которая требует подтверждения для вступления.
        Бот автоматически одобрит только запросы от правильного пользователя.
        """
        if not self.bot:
            raise ValueError("Бот не установлен в сервисе подписок")
        for attempt in range(max_retries):
            try:
                async with self.async_session_maker() as session:
                    result = await session.execute(select(User).where(User.telegram_user_id == str(user_id)))
                    user = result.scalar_one_or_none()
                    if not user:
                        raise ValueError(f"Пользователь с Telegram ID {user_id} не найден")
                    invite_link = await self.bot.create_chat_invite_link(
                        chat_id=channel_id,
                        name=f"Subscription_{user.telegram_user_id}",
                        creates_join_request=True,
                        expire_date=datetime.now() + timedelta(days=7)
                    )
                    INVITE_LINKS_MAP[invite_link.invite_link] = str(user.telegram_user_id)
                    return invite_link.invite_link
            except Exception as e:
                logging.error(f"Ошибка при создании ссылки-приглашения (попытка {attempt+1}): {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                else:
                    raise ValueError(f"Не удалось создать ссылку-приглашение после {max_retries} попыток: {str(e)}")
    
    async def approve_join_request(self, chat_id, user_id):
        """Одобряет запрос пользователя на вступление в канал"""
        if not self.bot:
            return False
        
        try:
            # Одобряем запрос на вступление
            async with self.async_session_maker() as session:
                result = await session.execute(select(User).where(User.telegram_user_id == str(user_id)))
                user = result.scalar_one_or_none()
                if not user:
                    raise ValueError(f"Пользователь с ID {user_id} не найден")
                
                await self.bot.approve_chat_join_request(
                    chat_id=chat_id,
                    user_id=user.telegram_user_id
                )
            return True
        except Exception as e:
            return False
    
    def is_valid_join_request(self, invite_link, user_id):
        """Проверяет, валиден ли запрос на вступление от данного пользователя"""
        if invite_link not in INVITE_LINKS_MAP:
            return False
        
        expected_user_id = INVITE_LINKS_MAP.get(invite_link)
        return str(user_id) == expected_user_id
    
    async def create_subscription(self, telegram_user_id, subscription_type, duration):
        """Создание подписки для пользователя"""
        # Получаем или создаем пользователя
        user = await self.get_user_by_telegram_id(telegram_user_id)
        
        # Получаем подходящий план
        plan = await self.get_subscription_plan(subscription_type, duration)
        
        # Деактивируем существующие активные подписки
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
            active_subscriptions = result.scalars().all()
            for subscription in active_subscriptions:
                subscription.is_active = False
        
        # Создаем новую подписку
        async with self.async_session_maker() as session:
            subscription = await SubscriptionManager(session).subscribe_user(user.id, plan.id)
            subscription.reminder_sent = False
        
        # Создаем ссылку-приглашение в канал, если есть ID канала
        if plan.channel_id and self.bot:
            try:
                invite_link = await self.create_channel_invite(plan.channel_id, user.telegram_user_id)
                async with self.async_session_maker() as session:
                    result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
                    active_subscriptions = result.scalars().all()
                    for subscription in active_subscriptions:
                        subscription.invite_link = invite_link
                        session.add(subscription)
                        session.commit()
            except Exception as e:
                # Логируем ошибку, но не прерываем создание подписки
                logging.error(f"Ошибка при создании ссылки-приглашения: {str(e)}")
        
        return subscription
    
    async def get_subscription_info(self, telegram_user_id):
        """Получение информации о текущей подписке пользователя"""
        user = await self.get_user_by_telegram_id(telegram_user_id)
        # Проверяем и обновляем истекшие подписки
        async with self.async_session_maker() as session:
            await SubscriptionManager(session).check_subscription_expiration()
        # Получаем активную подписку
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
            subscriptions = result.scalars().all()
        if not subscriptions:
            return None
        subscription = subscriptions[0]
        # Получаем план по plan_id
        async with self.async_session_maker() as session:
            result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
            plan = result.scalar_one_or_none()
        days_left = (subscription.end_date - datetime.utcnow()).days
        return {
            'plan_name': plan.name if plan else 'Неизвестно',
            'start_date': subscription.start_date,
            'end_date': subscription.end_date,
            'days_left': max(0, days_left),
            'is_active': subscription.is_active,
            'channel_id': plan.channel_id if plan else None,
            'invite_link': subscription.invite_link
        }
    
    async def remove_user_access(self, subscription: UserSubscription, max_retries=3):
        """
        Удаляет пользователя из канала, отзывает ссылку-приглашение, помечает подписку как неактивную и очищает ссылку в базе.
        """
        if not self.bot:
            logging.error("Бот не установлен в сервисе подписок")
            return False
        async with self.async_session_maker() as session:
            result = await session.execute(select(User).where(User.id == int(subscription.user_id)))
            user = result.scalar_one_or_none()
            if not user:
                logging.error(f"Не найден пользователь для подписки {subscription.id}")
                return False
            for attempt in range(max_retries):
                try:
                    await self.bot.ban_chat_member(chat_id=subscription.channel_id, user_id=user.telegram_user_id)
                    await self.bot.unban_chat_member(chat_id=subscription.channel_id, user_id=user.telegram_user_id, only_if_banned=True)
                    break
                except Exception as e:
                    logging.error(f"Ошибка при удалении пользователя (попытка {attempt+1}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                    else:
                        return False
            if subscription.invite_link:
                for attempt in range(max_retries):
                    try:
                        await self.bot.revoke_chat_invite_link(chat_id=subscription.channel_id, invite_link=subscription.invite_link)
                        break
                    except Exception as e:
                        logging.error(f"Ошибка при отзыве ссылки (попытка {attempt+1}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                        else:
                            pass
            subscription.is_active = False
            subscription.invite_link = None
            session.add(subscription)
            await session.commit()
        return True

    async def get_expiring_subscriptions(self, hours=24):
        """
        Находит подписки, истекающие через указанное количество часов (по умолчанию 24).
        """
        now = datetime.utcnow()
        soon = now + timedelta(hours=hours)
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(
                UserSubscription.is_active == True,
                UserSubscription.end_date > now,
                UserSubscription.end_date <= soon
            ))
            return result.scalars().all()

    async def get_expired_subscriptions(self):
        now = datetime.utcnow()
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(
                UserSubscription.is_active == True,
                UserSubscription.end_date < now
            ))
            return result.scalars().all()

    async def get_recently_expired_subscriptions(self, last_check, now):
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(
                UserSubscription.is_active == False,
                UserSubscription.end_date >= last_check,
                UserSubscription.end_date < now
            ))
            return result.scalars().all()

# Глобальный экземпляр сервиса подписок
subscription_service = SubscriptionService()
# (async инициализация тарифных планов вызывается отдельно в main/startup) 