from celery import Celery
from datetime import datetime, timedelta
import os
from app.subscription_service import subscription_service
from app.database import User, UserSubscription, SubscriptionPlan
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import logging
from sqlalchemy import select, and_

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL')
if not CELERY_BROKER_URL:
    raise ValueError('Не задан CELERY_BROKER_URL в .env!')

CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND')
if not CELERY_RESULT_BACKEND:
    raise ValueError('Не задан CELERY_RESULT_BACKEND в .env!')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    raise ValueError('Не задан TELEGRAM_BOT_TOKEN в .env!')

celery = Celery('reminders', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# Конфигурация Celery Beat для периодических задач
celery.conf.beat_schedule = {
    'send-registration-reminders': {
        'task': 'reminders.send_registration_reminders_task',
        'schedule': 600.0,  # Каждые 10 минут
    },
    'send-subscription-reminders': {
        'task': 'reminders.send_subscription_reminders_task',
        'schedule': 3600.0,  # Каждый час
    },
    'send-last-day-reminders': {
        'task': 'reminders.send_last_day_reminders_task',
        'schedule': 3600.0,  # Каждый час
    },
    'send-expired-reminders': {
        'task': 'reminders.send_expired_reminders_task',
        'schedule': 3600.0,  # Каждый час
    },
    'check-expired-subscriptions': {
        'task': 'reminders.check_expired_subscriptions_task',
        'schedule': 300.0,  # Каждые 5 минут
    },
    'force-cleanup-expired': {
        'task': 'reminders.force_cleanup_expired_task',
        'schedule': 3600.0,  # Запуск раз в час
    },

}

celery.conf.timezone = 'UTC'

bot = Bot(token=TELEGRAM_BOT_TOKEN)
subscription_service.set_bot(bot)

def get_payment_keyboard():
    """Клавиатура с кнопкой оплаты"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить подписку", callback_data='buy_subscription')]
        ]
    )

@celery.task(name='reminders.send_registration_reminders_task')
def send_registration_reminders_task():
    """Рассылка через 3 часа после регистрации без оформления подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_registration_reminders())

async def send_registration_reminders():
    now = datetime.utcnow()
    three_hours_ago = now - timedelta(hours=3)
    
    async with subscription_service.async_session_maker() as session:
        # Находим пользователей без активной подписки, зарегистрированных более 3 часов назад
        result = await session.execute(
            select(User).where(
                and_(
                    User.created_at <= three_hours_ago,
                    User.first_start_reminder_sent  == False
                )
            )
        )
        users = result.scalars().all()
        
        for user in users:
            # Проверяем, есть ли у пользователя активная подписка
            sub_result = await session.execute(
                select(UserSubscription).where(
                    and_(
                        UserSubscription.user_id == user.id,
                        UserSubscription.is_active == True
                    )
                )
            )
            has_subscription = sub_result.scalar_one_or_none()
            
            if not has_subscription:
                try:
                    first_name = user.first_name or "Друг"
                    text = (
                        f"{first_name}! Мы ждём Вас в нашем канале с эксклюзивными товарами "
                        f"за кешбэк 100 %. Осталось только оплатить подписку — сделаем это прямо сейчас?\n\n"
                        f"Начните зарабатывать и экономить уже сегодня💥"
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text=text,
                        reply_markup=get_payment_keyboard()
                    )
                    
                    user.first_start_reminder_sent  = True
                    session.add(user)
                    logging.info(f"Отправлено напоминание о регистрации пользователю {user.telegram_user_id}")
                except Exception as e:
                    logging.error(f"Ошибка при отправке напоминания о регистрации пользователю {user.telegram_user_id}: {e}")
        
        await session.commit()

@celery.task(name='reminders.send_subscription_reminders_task')
def send_subscription_reminders_task():
    """Рассылка за сутки до окончания подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_subscription_reminders())

async def send_subscription_reminders():
    now = datetime.utcnow()
    tomorrow = now + timedelta(hours=24)
    
    async with subscription_service.async_session_maker() as session:
        # Находим подписки, истекающие через 24 часа
        result = await session.execute(
            select(UserSubscription).where(
                and_(
                    UserSubscription.is_active == True,
                    UserSubscription.end_date <= tomorrow,
                    UserSubscription.end_date > now,
                    UserSubscription.reminder_sent == False
                )
            )
        )
        subscriptions = result.scalars().all()
        
        for sub in subscriptions:
            try:
                user_result = await session.execute(
                    select(User).where(User.id == sub.user_id)
                )
                user = user_result.scalar_one_or_none()
                
                if user:
                    text = (
                        "Внимание: завтра Ваша подписка истекает. "
                        "Чтобы не прерывать доступ к кешбэку 100 %, "
                        "оформите оплату на следующий месяц уже сегодня."
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text=text,
                        reply_markup=get_payment_keyboard()
                    )
                    
                    sub.reminder_sent = True
                    session.add(sub)
                    logging.info(f"Отправлено напоминание за сутки пользователю {user.telegram_user_id}")
            except Exception as e:
                logging.error(f"Ошибка при отправке напоминания за сутки: {e}")
        
        await session.commit()

@celery.task(name='reminders.send_last_day_reminders_task')
def send_last_day_reminders_task():
    """Рассылка в последний день действия подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_last_day_reminders())

async def send_last_day_reminders():
    now = datetime.utcnow()
    end_of_today = now.replace(hour=23, minute=59, second=59)
    
    async with subscription_service.async_session_maker() as session:
        # Находим подписки, истекающие сегодня
        result = await session.execute(
            select(UserSubscription).where(
                and_(
                    UserSubscription.is_active == True,
                    UserSubscription.end_date <= end_of_today,
                    UserSubscription.end_date > now,
                    UserSubscription.last_day_reminder_sent == False
                )
            )
        )
        subscriptions = result.scalars().all()
        
        for sub in subscriptions:
            try:
                user_result = await session.execute(
                    select(User).where(User.id == sub.user_id)
                )
                user = user_result.scalar_one_or_none()
                
                if user:
                    text = (
                        "Не дайте подписке закончиться! Сегодня последний день — "
                        "продлите доступ к каналу и продолжайте получать кешбэк 100 %."
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text=text,
                        reply_markup=get_payment_keyboard()
                    )
                    
                    sub.last_day_reminder_sent = True
                    session.add(sub)
                    logging.info(f"Отправлено напоминание в последний день пользователю {user.telegram_user_id}")
            except Exception as e:
                logging.error(f"Ошибка при отправке напоминания в последний день: {e}")
        
        await session.commit()

@celery.task(name='reminders.send_expired_reminders_task')
def send_expired_reminders_task():
    """Рассылка в день истечения подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(send_expired_reminders())

async def send_expired_reminders():
    now = datetime.utcnow()
    
    async with subscription_service.async_session_maker() as session:
        # Находим подписки, которые только что истекли
        result = await session.execute(
            select(UserSubscription).where(
                and_(
                    UserSubscription.is_active == False,
                    UserSubscription.end_date <= now,
                    UserSubscription.expired_reminder_sent == False
                )
            )
        )
        subscriptions = result.scalars().all()
        
        for sub in subscriptions:
            try:
                user_result = await session.execute(
                    select(User).where(User.id == sub.user_id)
                )
                user = user_result.scalar_one_or_none()
                
                if user:
                    first_name = user.first_name or "Друг"
                    text = (
                        f"{first_name}, привет! Сообщаем, что доступ к каналу закрыт — подписка истекла.\n\n"
                        f"Не хотите пропустить новые предложения с кешбэком 100 %? "
                        f"Продлите доступ прямо сейчас."
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text=text,
                        reply_markup=get_payment_keyboard()
                    )
                    
                    sub.expired_reminder_sent = True
                    session.add(sub)
                    logging.info(f"Отправлено напоминание об истечении пользователю {user.telegram_user_id}")
            except Exception as e:
                logging.error(f"Ошибка при отправке напоминания об истечении: {e}")
        
        await session.commit()

@celery.task(name='reminders.check_expired_subscriptions_task')
def check_expired_subscriptions_task():
    """Проверка и деактивация истекших подписок"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(check_expired_subscriptions())

async def check_expired_subscriptions():
    now = datetime.utcnow()
    
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(
            select(UserSubscription).where(
                and_(
                    UserSubscription.is_active == True,
                    UserSubscription.end_date < now
                )
            )
        )
        expired = result.scalars().all()
        
        for sub in expired:
            try:
                await subscription_service.remove_user_access(sub)
                logging.info(f"Отозван доступ для подписки {sub.id}")
            except Exception as e:
                logging.error(f"Ошибка при отзыве доступа для подписки {sub.id}: {e}")


@celery.task(name='reminders.force_cleanup_expired_task')
def force_cleanup_expired_task():
    """Принудительная зачистка всех, у кого истекла дата, даже если is_active=False"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(force_cleanup_expired())

async def force_cleanup_expired():
    now = datetime.utcnow()
    # Берем всех, у кого дата окончания прошла более 2 часов назад (чтобы не конфликтовать с основной задачей)
    cutoff_time = now - timedelta(hours=2)
    
    async with subscription_service.async_session_maker() as session:
        # Ищем подписки, которые истекли по времени
        # Нам не важен статус is_active, мы хотим убедиться, что их нет в канале
        result = await session.execute(
            select(UserSubscription).where(
                UserSubscription.end_date < cutoff_time
            )
        )
        expired_subs = result.scalars().all()
        
        logging.info(f"CLEANUP: Найдено {len(expired_subs)} подписок для проверки удаления.")

        for sub in expired_subs:
            try:
                # Получаем пользователя и план
                user_stmt = select(User).where(User.id == sub.user_id)
                user_res = await session.execute(user_stmt)
                user = user_res.scalar_one_or_none()
                
                plan_stmt = select(SubscriptionPlan).where(SubscriptionPlan.id == sub.plan_id)
                plan_res = await session.execute(plan_stmt)
                plan = plan_res.scalar_one_or_none()
                
                if user and plan:
                    channel_id = plan.channel_id
                    user_tg_id = user.telegram_user_id
                    
                    # Пытаемся кикнуть из канала (Kick + Unban)
                    try:
                        # Сначала проверяем статус (чтобы не спамить запросами API, если юзера там нет)
                        member = await subscription_service.bot.get_chat_member(chat_id=channel_id, user_id=user_tg_id)
                        
                        if member.status not in ('left', 'kicked'):
                            logging.warning(f"CLEANUP: Найден нелегал! User {user_tg_id} (sub {sub.id}) всё ещё в канале. Удаляем...")
                            await subscription_service.bot.ban_chat_member(chat_id=channel_id, user_id=user_tg_id)
                            await subscription_service.bot.unban_chat_member(chat_id=channel_id, user_id=user_tg_id, only_if_banned=True)
                            
                            # Если вдруг он был True в базе - исправим
                            if sub.is_active:
                                sub.is_active = False
                                session.add(sub)
                                await session.commit()
                        else:
                            # Пользователя и так нет в канале, всё ок.
                            # Если статус был True, ставим False
                             if sub.is_active:
                                sub.is_active = False
                                session.add(sub)
                                await session.commit()
                                
                    except Exception as e:
                        if "user not found" in str(e).lower() or "participant" in str(e).lower():
                             # Его там нет - отлично
                             pass
                        else:
                            logging.error(f"CLEANUP Error for user {user_tg_id}: {e}")

            except Exception as outer_e:
                logging.error(f"CLEANUP Critical error on sub {sub.id}: {outer_e}")
