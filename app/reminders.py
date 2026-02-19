from celery import Celery
import os
from app.subscription_service import subscription_service
from aiogram import Bot
import asyncio

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

@celery.task(name='reminders.send_registration_reminders_task')
def send_registration_reminders_task():
    """Рассылка через 3 часа после регистрации без оформления подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.send_registration_reminders())

@celery.task(name='reminders.send_subscription_reminders_task')
def send_subscription_reminders_task():
    """Рассылка за сутки до окончания подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.send_subscription_reminders())

@celery.task(name='reminders.send_last_day_reminders_task')
def send_last_day_reminders_task():
    """Рассылка в последний день действия подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.send_last_day_reminders())

@celery.task(name='reminders.send_expired_reminders_task')
def send_expired_reminders_task():
    """Рассылка в день истечения подписки"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.send_expired_reminders())

@celery.task(name='reminders.check_expired_subscriptions_task')
def check_expired_subscriptions_task():
    """Проверка и деактивация истекших подписок"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.check_expired_subscriptions())

@celery.task(name='reminders.force_cleanup_expired_task')
def force_cleanup_expired_task():
    """Принудительная зачистка всех, у кого истекла дата"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(subscription_service.force_cleanup_expired())
