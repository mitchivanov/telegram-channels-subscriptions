from celery import Celery
from datetime import datetime, timedelta
import os
from app.subscription_service import subscription_service
from app.database import User, UserSubscription
from aiogram import Bot
import asyncio
import logging

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

celery = Celery('aiogram', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
subscription_service.set_bot(bot)

@celery.task
def monitor_subscriptions_task():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(monitor_subscriptions_coro())

async def monitor_subscriptions_coro():
    now = datetime.utcnow()
    # Напоминания за 24 часа до окончания
    expiring = await subscription_service.get_expiring_subscriptions(hours=24)
    for sub in expiring:
        if not getattr(sub, 'reminder_sent', False):
            user = subscription_service.session.query(User).filter(User.id == sub.user_id).first()
            if user:
                try:
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text="⏰ Ваша подписка истекает через 24 часа! Продлите её, чтобы не потерять доступ к каналу."
                    )
                    sub.reminder_sent = True
                    subscription_service.session.commit()
                except Exception as e:
                    logging.error(f"Ошибка при отправке напоминания пользователю {user.telegram_user_id}: {e}")
    # Отзыв доступа для истекших подписок
    expired = subscription_service.session.query(UserSubscription).filter(
        UserSubscription.is_active == True,
        UserSubscription.end_date < now
    ).all()
    for sub in expired:
        user = subscription_service.session.query(User).filter(User.id == sub.user_id).first()
        try:
            await subscription_service.remove_user_access(sub)
            if user:
                await bot.send_message(
                    chat_id=user.telegram_user_id,
                    text="❌ Ваша подписка истекла. Доступ к каналу отозван. Если вы не успели вступить — оформите новую подписку для получения новой ссылки."
                )
        except Exception as e:
            logging.error(f"Ошибка при отзыве доступа у пользователя {getattr(user, 'telegram_user_id', '?')}: {e}")
    # Проверяем подписки, которые истекли за последние 2 минуты (например, после перезапуска)
    recently_expired = subscription_service.session.query(UserSubscription).filter(
        UserSubscription.is_active == False,
        UserSubscription.end_date >= now - timedelta(minutes=2),
        UserSubscription.end_date < now
    ).all()
    for sub in recently_expired:
        if not getattr(sub, 'reminder_sent', False):
            user = subscription_service.session.query(User).filter(User.id == sub.user_id).first()
            if user:
                try:
                    await bot.send_message(
                        chat_id=user.telegram_user_id,
                        text="❌ Ваша подписка истекла. Доступ к каналу отозван. Если вы не успели вступить — оформите новую подписку для получения новой ссылки."
                    )
                    sub.reminder_sent = True
                    subscription_service.session.commit()
                except Exception as e:
                    logging.error(f"Ошибка при отправке уведомления о завершении подписки пользователю {user.telegram_user_id}: {e}") 