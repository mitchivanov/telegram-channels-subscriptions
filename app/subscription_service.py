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
                {'name': 'Умная экономия', 'description': '- товары с кешбэком до 90%\n- выбор категорий товаров\n- более 20 товаров с кешбэком 100% ежемесячно\n- ежемесячный розыгрыш товаров', 
                 'price': 10000, 'duration_days': 30, 'channel_id': CHANNEL_IDS['basic_subscription']},

                
                # Премиум планы
                {'name': 'Premium кешбэк', 'description': '- товары с кешбэком от 90%\n- выбор категорий товаров\n- максимум товаров с кешбэком 100%\n- указан статус продавца (стоит ли доверять)\n- розыгрыш товаров 2 раза в месяц', 
                 'price': 20000, 'duration_days': 30, 'channel_id': CHANNEL_IDS['premium_subscription']},

            ]
            
            if PAYMENT_TEST_MODE:
                plans.append({'name': 'Базовый 5 минут', 'description': 'Тестовая подписка на 5 минут',
                              'price': 6900, 'duration_days': 5 / (24 * 60), 'channel_id': CHANNEL_IDS['basic_subscription']})
            
            for plan_data in plans:
                # Убедимся, что цена - целое число
                if not isinstance(plan_data['price'], int):
                    plan_data['price'] = int(plan_data['price'])
                plan = SubscriptionPlan(**plan_data)
                session.add(plan)
            
            await session.commit()
            logging.info(f"Инициализированы планы подписки: {len(plans)} планов")
    
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
                    # Находим активную подписку пользователя
                    sub_result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
                    subscription = sub_result.scalar_one_or_none()
                    if not subscription:
                        raise ValueError(f"Активная подписка для пользователя {user_id} не найдена")
                    invite_link_obj = await self.bot.create_chat_invite_link(
                        chat_id=channel_id,
                        name=f"Subscription_{user.telegram_user_id}",
                        creates_join_request=True,
                        expire_date=datetime.now() + timedelta(days=7),
                        member_limit=1  # ссылка одноразовая
                    )
                    # Сохраняем ссылку в подписке
                    subscription.invite_link = invite_link_obj.invite_link
                    session.add(subscription)
                    await session.commit()
                    return invite_link_obj.invite_link
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
    
    async def is_valid_join_request(self, invite_link, user_id):
        """Проверяет, валиден ли запрос на вступление от данного пользователя через базу"""
        async with self.async_session_maker() as session:
            result = await session.execute(
                select(UserSubscription).where(
                    UserSubscription.invite_link == invite_link,
                    UserSubscription.is_active == True,
                    UserSubscription.end_date > datetime.utcnow()
                )
            )
            sub = result.scalar_one_or_none()
            if not sub:
                return False
            user_result = await session.execute(select(User).where(User.id == sub.user_id))
            user = user_result.scalar_one_or_none()
            return user and str(user.telegram_user_id) == str(user_id)
    
    async def create_subscription(self, telegram_user_id, subscription_type=None, duration=None, plan_id=None):
        """Создание подписки для пользователя с полной транзакционностью"""
        async with self.async_session_maker() as session:
            async with session.begin():
                # Получаем или создаем пользователя
                result = await session.execute(select(User).where(User.telegram_user_id == str(telegram_user_id)))
                user = result.scalar_one_or_none()
                if not user:
                    user = User(telegram_user_id=str(telegram_user_id), is_active=True)
                    session.add(user)
                    await session.flush()
                # Получаем план подписки
                if plan_id is not None:
                    # Новый способ - по plan_id
                    result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
                    plan = result.scalar_one_or_none()
                    if not plan:
                        raise ValueError(f"План подписки с ID {plan_id} не найден")
                elif subscription_type and duration:
                    # Старый способ - по типу и длительности
                    plan = await self.get_subscription_plan(subscription_type, duration)
                else:
                    raise ValueError("Необходимо указать либо plan_id, либо оба параметра subscription_type и duration")
                # Деактивируем существующие активные подписки
                result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id))
                active_subscriptions = result.scalars().all()
                for subscription in active_subscriptions:
                    subscription.is_active = False
                    session.add(subscription)
                
                # Создаем новую подписку
                subscription = await SubscriptionManager(session).subscribe_user(user.id, plan.id, reminder_sent=False, commit=False)
                
                # Генерируем ссылку и сохраняем её
                if plan.channel_id and self.bot:
                    try:
                        invite_link_obj = await self.bot.create_chat_invite_link(
                            chat_id=plan.channel_id,
                            name=f"Subscription_{user.telegram_user_id}",
                            creates_join_request=True,
                            expire_date=datetime.now() + timedelta(days=7),
                            member_limit=1  # ссылка одноразовая
                        )
                        subscription.invite_link = invite_link_obj.invite_link
                    except Exception as e:
                        logging.error(f"Ошибка при создании ссылки-приглашения: {str(e)}")
                        raise
                session.add(subscription)
                await session.flush()
                subscription_id = subscription.id
            return subscription_id
    
    async def get_subscription_info(self, telegram_user_id):
        """Получение информации о текущей подписке пользователя"""
        user = await self.get_user_by_telegram_id(telegram_user_id)
        # Проверяем и обновляем истекшие подписки
        async with self.async_session_maker() as session:
            await SubscriptionManager(session).check_subscription_expiration()
        # Получаем только активную подписку
        async with self.async_session_maker() as session:
            result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
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
        Все действия выполняются в одной транзакции.
        Теперь подписка всегда деактивируется, даже если возникла ошибка при удалении пользователя из канала.
        """
        if not self.bot:
            logging.error("Бот не установлен в сервисе подписок")
            return False
        async with self.async_session_maker() as session:
            async with session.begin():
                result = await session.execute(select(User).where(User.id == int(subscription.user_id)))
                user = result.scalar_one_or_none()
                if not user:
                    logging.error(f"Не найден пользователь для подписки {subscription.id}")
                    return False
                # Пытаемся удалить пользователя из канала
                removed = False
                for attempt in range(max_retries):
                    try:
                        await self.bot.ban_chat_member(chat_id=subscription.channel_id, user_id=user.telegram_user_id)
                        await self.bot.unban_chat_member(chat_id=subscription.channel_id, user_id=user.telegram_user_id, only_if_banned=True)
                        removed = True
                        break
                    except Exception as e:
                        if 'USER_NOT_PARTICIPANT' in str(e) or 'user not found' in str(e).lower() or 'chat not found' in str(e).lower():
                            logging.info(f"[REMOVE] Пользователь {user.telegram_user_id} уже не состоит в канале {subscription.channel_id} или канал не найден.")
                            break
                        logging.error(f"Ошибка при удалении пользователя (попытка {attempt+1}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                # Пытаемся отозвать ссылку
                if subscription.invite_link:
                    for attempt in range(max_retries):
                        try:
                            await self.bot.revoke_chat_invite_link(chat_id=subscription.channel_id, invite_link=subscription.invite_link)
                            break
                        except Exception as e:
                            if 'INVITE_HASH_EXPIRED' in str(e) or 'not found' in str(e).lower():
                                logging.info(f"[REMOVE] Ссылка уже неактивна или не найдена: {subscription.invite_link}")
                                break
                            logging.error(f"Ошибка при отзыве ссылки (попытка {attempt+1}): {e}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                # ВСЕГДА деактивируем подписку и очищаем invite_link
                subscription.is_active = False
                subscription.invite_link = None
                session.add(subscription)
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