import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.subscription_service import subscription_service
from app.google_sheets_service import google_sheets_service

# Настройка логирования
logger = logging.getLogger(__name__)

# Инициализация планировщика с явным указанием таймзоны UTC
scheduler = AsyncIOScheduler(timezone='UTC')

async def send_registration_reminders_task():
    """Рассылка через 3 часа после регистрации без оформления подписки"""
    try:
        await subscription_service.send_registration_reminders()
    except Exception as e:
        logger.error(f"Ошибка в задаче send_registration_reminders: {e}")

async def send_subscription_reminders_task():
    """Рассылка за сутки до окончания подписки"""
    try:
        await subscription_service.send_subscription_reminders()
    except Exception as e:
        logger.error(f"Ошибка в задаче send_subscription_reminders: {e}")

async def send_last_day_reminders_task():
    """Рассылка в последний день действия подписки"""
    try:
        await subscription_service.send_last_day_reminders()
    except Exception as e:
        logger.error(f"Ошибка в задаче send_last_day_reminders: {e}")

async def send_expired_reminders_task():
    """Рассылка в день истечения подписки"""
    try:
        await subscription_service.send_expired_reminders()
    except Exception as e:
        logger.error(f"Ошибка в задаче send_expired_reminders: {e}")

async def check_expired_subscriptions_task():
    """Проверка и деактивация истекших подписок"""
    try:
        await subscription_service.check_expired_subscriptions()
    except Exception as e:
        logger.error(f"Ошибка в задаче check_expired_subscriptions: {e}")

async def force_cleanup_expired_task():
    """Принудительная зачистка всех, у кого истекла дата"""
    try:
        await subscription_service.force_cleanup_expired()
    except Exception as e:
        logger.error(f"Ошибка в задаче force_cleanup_expired: {e}")

async def async_record_payment(user_id, username, amount, duration_days, plan_name, payment_type, transaction_id):
    """
    Асинхронная обертка для записи платежа в Google Sheets.
    Запускает синхронный код в отдельном потоке, чтобы не блокировать Event Loop.
    """
    try:
        await asyncio.to_thread(
            google_sheets_service.append_payment,
            user_id=user_id,
            username=username,
            amount=amount,
            duration_days=duration_days,
            plan_name=plan_name,
            payment_type=payment_type,
            transaction_id=transaction_id
        )
    except Exception as e:
        logger.error(f"Ошибка при записи в Google Sheets: {e}")

def setup_scheduler():
    """Регистрация задач в планировщике"""
    # Каждые 10 минут
    scheduler.add_job(
        send_registration_reminders_task,
        IntervalTrigger(minutes=10),
        id='send_registration_reminders',
        replace_existing=True
    )

    # Каждый час
    scheduler.add_job(
        send_subscription_reminders_task,
        IntervalTrigger(hours=1),
        id='send_subscription_reminders',
        replace_existing=True
    )

    # Каждый час
    scheduler.add_job(
        send_last_day_reminders_task,
        IntervalTrigger(hours=1),
        id='send_last_day_reminders',
        replace_existing=True
    )

    # Каждый час
    scheduler.add_job(
        send_expired_reminders_task,
        IntervalTrigger(hours=1),
        id='send_expired_reminders',
        replace_existing=True
    )

    # Каждые 5 минут
    scheduler.add_job(
        check_expired_subscriptions_task,
        IntervalTrigger(minutes=5),
        id='check_expired_subscriptions',
        replace_existing=True
    )

    # Запуск раз в час
    scheduler.add_job(
        force_cleanup_expired_task,
        IntervalTrigger(hours=1),
        id='force_cleanup_expired',
        replace_existing=True
    )

    return scheduler
