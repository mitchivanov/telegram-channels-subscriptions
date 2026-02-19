from app.database import async_init_db, get_async_session_maker, User, SubscriptionPlan, UserSubscription
from app.subscription_manager import SubscriptionManager
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import logging
import asyncio
from sqlalchemy import select
import random
import traceback

# Загружаем переменные окружения
load_dotenv()

PAYMENT_TEST_MODE = os.getenv('PAYMENT_TEST_MODE', 'False').lower() in ('true', '1', 't')

# Дефолтные значения для основного плана подписки
DEFAULT_PLAN_NAME = os.getenv('DEFAULT_PLAN_NAME', 'Premium кешбэк')
DEFAULT_PLAN_PRICE = int(os.getenv('DEFAULT_PLAN_PRICE', 20000))
DEFAULT_PLAN_DURATION = int(os.getenv('DEFAULT_PLAN_DURATION', 30))

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

NEW_PLANS = [
    {'name': 'Подписка на 7 дней', 'days': 7, 'price': 6000},
    {'name': 'Подписка на 1 месяц', 'days': 30, 'price': 18000},
    {'name': 'Подписка на 3 месяца', 'days': 90, 'price': 45000},
    {'name': 'Подписка на 6 месяцев', 'days': 180, 'price': 75000},
    {'name': 'Подписка на 1 год', 'days': 365, 'price': 140000},
]

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
        """Инициализация тарифных планов и миграция старых подписок"""
        async with self.async_session_maker() as session:
            # 1. Создаем новые планы, если их нет
            new_plans_map = {} # map name -> plan object
            
            for plan_data in NEW_PLANS:
                result = await session.execute(select(SubscriptionPlan).where(
                    SubscriptionPlan.name == plan_data['name'],
                    SubscriptionPlan.price == plan_data['price'],
                    SubscriptionPlan.duration_days == plan_data['days']
                ))
                plan = result.scalar_one_or_none()
                
                if not plan:
                    plan = SubscriptionPlan(
                        name=plan_data['name'],
                        description=f"Доступ к каналу на {plan_data['days']} дней",
                        price=plan_data['price'],
                        duration_days=plan_data['days'],
                        channel_id=CHANNEL_IDS['premium_subscription'] # Все новые планы для премиум канала
                    )
                    session.add(plan)
                    await session.flush() # Получаем ID
                    logging.info(f"Создан новый план: {plan.name}")

                new_plans_map[plan.name] = plan
            
            # 2. Миграция: находим старые планы и переводим активные подписки на 'Подписка на 1 месяц'
            # Целевой план для миграции
            target_plan = new_plans_map.get('Подписка на 1 месяц')
            if not target_plan:
                logging.error("Не удалось найти целевой план 'Подписка на 1 месяц' для миграции!")
            else:
                # Получаем все планы, которые НЕ являются новыми планами
                new_plan_ids = [p.id for p in new_plans_map.values()]
                result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id.not_in(new_plan_ids)))
                old_plans = result.scalars().all()

                old_plan_ids = [p.id for p in old_plans]

                if old_plan_ids:
                    # Находим все активные подписки на старые планы
                    result = await session.execute(select(UserSubscription).where(
                        UserSubscription.plan_id.in_(old_plan_ids),
                        UserSubscription.is_active == True
                    ))
                    subscriptions_to_migrate = result.scalars().all()

                    if subscriptions_to_migrate:
                        logging.info(f"Найдено {len(subscriptions_to_migrate)} подписок для миграции на новый тариф.")
                        for sub in subscriptions_to_migrate:
                            sub.plan_id = target_plan.id
                            session.add(sub)

                        logging.info("Миграция подписок завершена.")
            
            await session.commit()

    async def get_active_plans(self):
        """Возвращает список актуальных тарифных планов"""
        async with self.async_session_maker() as session:
             # Возвращаем планы, соответствующие списку NEW_PLANS
            plans = []
            for plan_def in NEW_PLANS:
                result = await session.execute(select(SubscriptionPlan).where(
                    SubscriptionPlan.name == plan_def['name'],
                    SubscriptionPlan.price == plan_def['price'],
                    SubscriptionPlan.duration_days == plan_def['days']
                ))
                plan = result.scalar_one_or_none()
                if plan:
                    plans.append(plan)
            return plans

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
    
    
    async def get_default_month_plan(self):
        # Возвращаем новый план на 1 месяц
        async with self.async_session_maker() as session:
            # Исправляем запрос выше или ищем по параметрам
            result = await session.execute(
                select(SubscriptionPlan).where(
                    SubscriptionPlan.name == 'Подписка на 1 месяц',
                    SubscriptionPlan.duration_days == 30,
                    SubscriptionPlan.price == 18000
                )
            )
            plan = result.scalar_one_or_none()
            if not plan:
                 # Fallback if creation hasn't happened yet for some reason
                 return None
            return plan
    
    
    async def get_subscription_plan(self, subscription_type, duration):
        """Получение подходящего плана подписки по типу и длительности"""
        # Этот метод устарел, но оставим его работоспособным, если вдруг используется
        # Формируем название плана из типа и длительности
        if duration == '5_min':
            plan_name = f"{SUBSCRIPTION_TYPE_MAP[subscription_type]} 5 минут"
        else:
            plan_name = f"{SUBSCRIPTION_TYPE_MAP[subscription_type]} {DURATION_MAP[duration]} дней"
        
        # Ищем план в базе данных
        async with self.async_session_maker() as session:
            result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == plan_name))
            plan = result.scalar_one_or_none()
        
        # Если не нашли старый план, попробуем вернуть один из новых, если подходит по длительности
        if not plan:
             # Fallback logic could be added here
             pass

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
                        expire_date=datetime.now() + timedelta(days=7)
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
                            expire_date=datetime.now() + timedelta(days=7)
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
    
    async def remove_user_access(self, subscription, max_retries=3):
        """Отзыв доступа пользователя к каналу"""
        if not self.bot:
            logging.error("Бот не инициализирован в SubscriptionService")
            return False

        # FIX: Открываем новую сессию и загружаем объект заново, чтобы избежать конфликта сессий
        async with self.async_session_maker() as session:
            async with session.begin():
                # Перечитываем подписку в текущей сессии по ID переданного объекта
                stmt = select(UserSubscription).where(UserSubscription.id == subscription.id)
                result = await session.execute(stmt)
                db_subscription = result.scalar_one_or_none()

                if not db_subscription:
                    logging.error(f"Подписка {subscription.id} не найдена в базе при попытке удаления")
                    return False

                # Загружаем пользователя для этой подписки
                user_stmt = select(User).where(User.id == int(db_subscription.user_id))
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                if not user:
                    logging.error(f"Пользователь для подписки {db_subscription.id} не найден")
                    return False

                # Логика бана в Telegram
                removed = False
                for attempt in range(max_retries):
                    try:
                        await self.bot.ban_chat_member(chat_id=db_subscription.plan.channel_id, user_id=user.telegram_user_id)
                        await self.bot.unban_chat_member(chat_id=db_subscription.plan.channel_id, user_id=user.telegram_user_id, only_if_banned=True)
                        removed = True
                        break
                    except Exception as e:
                        if "USER_NOT_PARTICIPANT" in str(e) or "user not found" in str(e).lower() or "chat not found" in str(e).lower():
                            logging.info(f"REMOVE: Пользователя {user.telegram_user_id} уже нет в канале {db_subscription.plan.channel_id} или канал недоступен.")
                            removed = True # Считаем успехом, чтобы снять флаг активности
                            break
                        logging.error(f"Ошибка при бане пользователя (попытка {attempt + 1}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 + attempt + random.uniform(0, 1))

                # Логика отзыва ссылки
                if db_subscription.invite_link:
                    for attempt in range(max_retries):
                        try:
                            await self.bot.revoke_chat_invite_link(chat_id=db_subscription.plan.channel_id, invite_link=db_subscription.invite_link)
                            break
                        except Exception as e:
                            if "INVITE_HASH_EXPIRED" in str(e) or "not found" in str(e).lower():
                                logging.info(f"REMOVE: Ссылка {db_subscription.invite_link} уже неактивна.")
                                break
                            logging.error(f"Ошибка при отзыве ссылки (попытка {attempt + 1}): {e}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2 + attempt + random.uniform(0, 1))

                # Важно: меняем статус у объекта, загруженного в ЭТОЙ сессии
                db_subscription.is_active = False
                db_subscription.invite_link = None
                session.add(db_subscription)
                # commit произойдет автоматически при выходе из context manager session.begin()
                
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

    async def process_24h_reminders(self):
        """Отправляет напоминания за 24 часа до окончания подписки"""
        if not self.bot:
            logging.error("Бот не инициализирован в SubscriptionService")
            return

        try:
            expiring = await self.get_expiring_subscriptions(hours=24)
            for sub in expiring:
                if not getattr(sub, 'reminder_sent', False):
                    # Открываем сессию для обновления флага
                    async with self.async_session_maker() as session:
                         # Обновляем объект, привязывая его к новой сессии, если нужно, или загружаем заново
                        stmt = select(UserSubscription).where(UserSubscription.id == sub.id)
                        result = await session.execute(stmt)
                        db_sub = result.scalar_one_or_none()

                        if db_sub and not db_sub.reminder_sent:
                            user_stmt = select(User).where(User.id == db_sub.user_id)
                            user_result = await session.execute(user_stmt)
                            user = user_result.scalar_one_or_none()

                            if user:
                                try:
                                    await self.bot.send_message(
                                        chat_id=user.telegram_user_id,
                                        text="⏰ Ваша подписка истекает через 24 часа! Продлите её, чтобы не потерять доступ к каналу."
                                    )
                                    db_sub.reminder_sent = True
                                    session.add(db_sub)
                                    await session.commit()
                                except Exception as e:
                                    logging.error(f"Ошибка при отправке напоминания пользователю {user.telegram_user_id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка в process_24h_reminders: {e}\n{traceback.format_exc()}")

    async def process_expired_notifications(self, last_check, now):
        """Отправляет уведомления об истечении подписки"""
        if not self.bot:
            logging.error("Бот не инициализирован в SubscriptionService")
            return

        try:
            recently_expired = await self.get_recently_expired_subscriptions(last_check, now)
            for sub in recently_expired:
                # Используем expired_reminder_sent вместо reminder_sent!
                if not getattr(sub, 'expired_reminder_sent', False):
                     async with self.async_session_maker() as session:
                        stmt = select(UserSubscription).where(UserSubscription.id == sub.id)
                        result = await session.execute(stmt)
                        db_sub = result.scalar_one_or_none()

                        if db_sub and not db_sub.expired_reminder_sent:
                            user_stmt = select(User).where(User.id == db_sub.user_id)
                            user_result = await session.execute(user_stmt)
                            user = user_result.scalar_one_or_none()

                            if user:
                                try:
                                    await self.bot.send_message(
                                        chat_id=user.telegram_user_id,
                                        text="❌ Ваша подписка истекла. Доступ к каналу отозван. Оформите новую подписку для восстановления доступа."
                                    )
                                    db_sub.expired_reminder_sent = True
                                    session.add(db_sub)
                                    await session.commit()
                                except Exception as e:
                                    logging.error(f"Ошибка при отправке уведомления о завершении подписки пользователю {user.telegram_user_id}: {e}")

        except Exception as e:
            logging.error(f"Ошибка в process_expired_notifications: {e}\n{traceback.format_exc()}")

# Глобальный экземпляр сервиса подписок
subscription_service = SubscriptionService()
# (async инициализация тарифных планов вызывается отдельно в main/startup)
