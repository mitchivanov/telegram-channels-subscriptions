import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram import Router, types, F
from aiogram.filters import Command
import os
from keyboards import get_reply_keyboard, get_inline_keyboard
import logging
from dotenv import load_dotenv
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from app.subscription_service import subscription_service, SUBSCRIPTION_TYPE_MAP, DURATION_MAP, CHANNEL_IDS, INVITE_LINKS_MAP
from app.database import User, UserSubscription, SubscriptionPlan, async_init_db
from aiogram.types import LabeledPrice
from aiogram.types.message import ContentType
from aiogram.types import ChatJoinRequest
import traceback
from datetime import datetime, timedelta
from sqlalchemy import select
from app.subscription_service import SubscriptionManager

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    raise ValueError('Не задан TELEGRAM_BOT_TOKEN в .env!')
TELEGRAM_PAYMENT_TOKEN = os.getenv('TELEGRAM_PAYMENT_TOKEN')
if not TELEGRAM_PAYMENT_TOKEN:
    raise ValueError('Не задан TELEGRAM_PAYMENT_TOKEN в .env!')

# Проверка тестового режима
IS_TEST_MODE = os.getenv('PAYMENT_TEST_MODE', 'False').lower() in ('true', '1', 't')
if IS_TEST_MODE and not TELEGRAM_PAYMENT_TOKEN.startswith('381764678:TEST:'):
    logging.warning("Используется тестовый платежный токен для Юкассы")

# Создаем класс состояний для хранения выбора пользователя
class SubscriptionStates(StatesGroup):
    choosing_type = State()
    confirming_payment = State()


# Хранилище состояний в памяти
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Устанавливаем экземпляр бота в сервис подписок
subscription_service.set_bot(bot)


@dp.message(Command('start'))
async def start_command(message: types.Message, state: FSMContext):
    # При старте сбрасываем состояние
    await state.clear()
    await message.answer('Приветствую! Это бот для тестирования оплаты в Telegram', reply_markup=await get_reply_keyboard(keyboard_type='start'))

@dp.message(F.text == 'Управление подпиской')
async def manage_subscription(message: types.Message, state: FSMContext):
    # Проверяем, есть ли активная подписка у пользователя
    subscription_info = await subscription_service.get_subscription_info(message.from_user.id)
    
    if subscription_info:
        # Если подписка есть, показываем информацию о ней
        days_left = subscription_info['days_left']
        message_text = (
            f"Ваша текущая подписка: {subscription_info['plan_name']}\n"
            f"Действует до: {subscription_info['end_date'].strftime('%d.%m.%Y')}\n"
            f"Осталось дней: {days_left}"
        )
        
        # Если есть ссылка-приглашение, показываем её
        if subscription_info.get('invite_link'):
            message_text += f"\n\nСсылка для входа в канал: {subscription_info['invite_link']}"
            message_text += "\n\n⚠️ Эта ссылка доступна только вам. При переходе по ссылке вам нужно будет отправить запрос на вступление, который будет автоматически одобрен."
        
        await message.answer(message_text, reply_markup=await get_inline_keyboard(keyboard_type='manage_existing_subscription'))
    else:
        # Если подписки нет, предлагаем купить
        await message.answer('Выберите действие:', reply_markup=await get_inline_keyboard(keyboard_type='manage_subscription'))


# Обработчик запросов на вступление в канал
@dp.chat_join_request()
async def process_join_request(join_request: ChatJoinRequest):
    """Обрабатывает запросы на вступление в канал"""
    chat_id = join_request.chat.id
    user_id = join_request.from_user.id
    invite_link = join_request.invite_link.invite_link if join_request.invite_link else None
    
    logging.info(f"Получен запрос на вступление в канал: user_id={user_id}, chat_id={chat_id}, invite_link={invite_link}")
    
    # Базовая проверка - является ли канал одним из наших каналов с подписками
    if str(chat_id) not in CHANNEL_IDS.values():
        logging.warning(f"Получен запрос для неизвестного канала: {chat_id}")
        return
    
    # Если нет ссылки-приглашения, отклоняем запрос
    if not invite_link:
        logging.warning(f"Запрос без ссылки-приглашения от пользователя {user_id}")
        await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
        return
    
    # Проверяем, что запрос идет от правильного пользователя
    if subscription_service.is_valid_join_request(invite_link, user_id):
        # Одобряем запрос
        try:
            await bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
            logging.info(f"Одобрен запрос на вступление для пользователя {user_id}")
            
            # Оповещаем пользователя об успешном вступлении
            try:
                await bot.send_message(
                    chat_id=user_id, 
                    text=f"✅ Ваш запрос на вступление в канал был автоматически одобрен. Добро пожаловать!"
                )
            except Exception as e:
                logging.error(f"Ошибка при отправке уведомления пользователю: {str(e)}")
        except Exception as e:
            logging.error(f"Ошибка при одобрении запроса на вступление: {str(e)}")
    else:
        # Отклоняем запрос, если пользователь не соответствует ссылке
        try:
            await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
            logging.warning(f"Отклонен запрос на вступление для пользователя {user_id} - неправильный пользователь для ссылки")
            
            # Оповещаем пользователя об отклонении
            try:
                await bot.send_message(
                    chat_id=user_id, 
                    text="❌ Ваш запрос на вступление в канал был отклонен. Эта ссылка-приглашение предназначена для другого пользователя."
                )
            except Exception as e:
                logging.error(f"Ошибка при отправке уведомления пользователю: {str(e)}")
        except Exception as e:
            logging.error(f"Ошибка при отклонении запроса на вступление: {str(e)}")


@dp.callback_query(F.data == 'buy_subscription')
async def buy_subscription(callback: types.CallbackQuery, state: FSMContext):
    # Переходим в состояние выбора типа подписки
    await state.set_state(SubscriptionStates.choosing_type)
    await callback.message.answer('Выберите тип подписки:', reply_markup=await get_inline_keyboard(keyboard_type='choose_subscription_type'))

@dp.callback_query(SubscriptionStates.choosing_type, F.data.in_(['basic_subscription', 'premium_subscription']))
async def process_subscription_type(callback: types.CallbackQuery, state: FSMContext):
    subscription_type = callback.data
    await state.update_data(subscription_type=subscription_type)

    # Выбор длительности
    durations = [('30_days', '30 дней')]
    if IS_TEST_MODE and subscription_type == 'basic_subscription':
        durations.insert(0, ('5_min', '5 минут (тест)'))

    # Если несколько вариантов, спрашиваем пользователя
    if len(durations) > 1:
        # Сохраняем список в state
        await state.update_data(available_durations=durations)
        # Формируем inline-клавиатуру корректно для aiogram v3+
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=d_label, callback_data=f'duration_{d_key}')]
                for d_key, d_label in durations
            ]
        )
        await callback.message.answer('Выберите длительность подписки:', reply_markup=keyboard)
        return
    else:
        duration = durations[0][0]
        await state.update_data(duration=duration)

    # Сохраняем информацию о канале
    channel_id = CHANNEL_IDS.get(subscription_type, 'Неизвестно')
    await state.update_data(channel_id=channel_id)

    # Устанавливаем цену
    if subscription_type == 'basic_subscription' and IS_TEST_MODE and duration == '5_min':
        plan_price = 6900
    elif subscription_type == 'basic_subscription':
        plan_price = 10000
    else:
        plan_price = 20000
    await state.update_data(plan_price=plan_price)

    # Информация о выбранной подписке для подтверждения
    subscription_info = {
        'basic_subscription': 'Базовый',
        'premium_subscription': 'Премиум',
        '30_days': '30 дней',
        '5_min': '5 минут (тест)'
    }

    channel_type = "Базовый канал" if subscription_type == 'basic_subscription' else "Премиум канал"
    await state.set_state(SubscriptionStates.confirming_payment)
    message_text = (
        f"Вы выбрали подписку: {subscription_info[subscription_type]}\n"
        f"Длительность: {subscription_info.get(duration, duration)}\n"
        f"Стоимость: {plan_price/100} руб.\n"
        f"Доступ к каналу: {channel_type}\n\n"
        f"Пожалуйста, подтвердите оплату:"
    )
    await callback.message.answer(message_text, reply_markup=await get_inline_keyboard(keyboard_type='confirm_payment'))

# Обработка выбора длительности
@dp.callback_query(lambda c: c.data.startswith('duration_'))
async def choose_duration(callback: types.CallbackQuery, state: FSMContext):
    duration = callback.data.replace('duration_', '')
    await state.update_data(duration=duration)
    user_data = await state.get_data()
    subscription_type = user_data.get('subscription_type')
    # Сохраняем информацию о канале
    channel_id = CHANNEL_IDS.get(subscription_type, 'Неизвестно')
    await state.update_data(channel_id=channel_id)
    # Устанавливаем цену
    if subscription_type == 'basic_subscription' and IS_TEST_MODE and duration == '5_min':
        plan_price = 6900
    elif subscription_type == 'basic_subscription':
        plan_price = 10000
    else:
        plan_price = 20000
    await state.update_data(plan_price=plan_price)
    subscription_info = {
        'basic_subscription': 'Базовый',
        'premium_subscription': 'Премиум',
        '30_days': '30 дней',
        '5_min': '5 минут (тест)'
    }
    channel_type = "Базовый канал" if subscription_type == 'basic_subscription' else "Премиум канал"
    await state.set_state(SubscriptionStates.confirming_payment)
    message_text = (
        f"Вы выбрали подписку: {subscription_info[subscription_type]}\n"
        f"Длительность: {subscription_info.get(duration, duration)}\n"
        f"Стоимость: {plan_price/100} руб.\n"
        f"Доступ к каналу: {channel_type}\n\n"
        f"Пожалуйста, подтвердите оплату:"
    )
    await callback.message.answer(message_text, reply_markup=await get_inline_keyboard(keyboard_type='confirm_payment'))

@dp.callback_query(SubscriptionStates.confirming_payment, F.data == 'confirm_payment')
async def confirm_payment(callback: types.CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    subscription_type = user_data.get('subscription_type')
    duration = user_data.get('duration')
    plan_price = user_data.get('plan_price', 0)
    subscription_info = {
        'basic_subscription': 'Базовый',
        'premium_subscription': 'Премиум',
        '30_days': '30 дней',
        '5_min': '5 минут (тест)'
    }
    title = f"Подписка {subscription_info[subscription_type]}"
    description = f"Подписка {subscription_info[subscription_type]} на {subscription_info.get(duration, duration)}"
    payload = f"{subscription_type}:{duration}"
    start_parameter = "subscription_payment"
    currency = "RUB"
    prices = [LabeledPrice(label=title, amount=plan_price)]
    logging.info(f"Отправка инвойса: {title}, {description}, {payload}, {TELEGRAM_PAYMENT_TOKEN[:10]}..., {currency}, {prices}")
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=title,
            description=description,
            payload=payload,
            provider_token=TELEGRAM_PAYMENT_TOKEN,
            currency=currency,
            prices=prices,
            start_parameter=start_parameter,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False,
            protect_content=True
        )
    except Exception as e:
        logging.error(f"Ошибка при создании платежа: {str(e)}")
        await callback.message.answer(
            f"Произошла ошибка при создании платежа: {str(e)}",
            reply_markup=await get_reply_keyboard(keyboard_type='start')
        )
        await state.clear()

# Обработчик пре-чекаута (проверка платежа перед выполнением)
@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    # Здесь можно выполнить дополнительные проверки, например, доступность товара
    # Для примера, всегда подтверждаем платеж
    logging.info(f"Получен pre_checkout_query: {pre_checkout_query}")
    
    try:
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        logging.info("Pre-checkout подтвержден")
    except Exception as e:
        logging.error(f"Ошибка при подтверждении pre-checkout: {str(e)}")
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id, 
            ok=False, 
            error_message="Произошла ошибка при проверке платежа. Пожалуйста, попробуйте позже."
        )

# Обработчик успешного платежа
@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message, state: FSMContext):
    payment = message.successful_payment
    logging.info(f"Получен успешный платеж: {payment}")
    subscription_type, duration = payment.invoice_payload.split(':')
    try:
        subscription = await subscription_service.create_subscription(
            message.from_user.id, 
            subscription_type, 
            duration
        )
        plan_name = SUBSCRIPTION_TYPE_MAP[subscription_type]
        # Для тестовой подписки корректно отображаем срок
        if duration == '5_min':
            end_date = (subscription.start_date + timedelta(minutes=5)).strftime('%d.%m.%Y %H:%M')
            duration_text = '5 минут (тест)'
        else:
            end_date = subscription.end_date.strftime('%d.%m.%Y')
            duration_text = '30 дней'
        success_message = (
            f"Платеж успешно проведен!\n"
            f"Подписка '{plan_name}' на {duration_text} активирована.\n"
            f"Действует до: {end_date}"
        )
        if subscription.invite_link:
            success_message += (
                f"\n\nДля доступа к каналу используйте ссылку: {subscription.invite_link}\n"
                f"⚠️ Перейдя по ссылке, нажмите 'Запросить вступление'. "
                f"Ваш запрос будет автоматически одобрен, так как ссылка персональная."
            )
        else:
            success_message += "\n\nНе удалось создать ссылку для приглашения в канал, пожалуйста, обратитесь в поддержку."
        await message.answer(
            success_message,
            reply_markup=await get_reply_keyboard(keyboard_type='start')
        )
    except Exception as e:
        logging.error(f"Ошибка при активации подписки: {str(e)}")
        await message.answer(
            f"Платеж успешно проведен, но произошла ошибка при активации подписки: {str(e)}\n"
            f"Пожалуйста, обратитесь в поддержку.",
            reply_markup=await get_reply_keyboard(keyboard_type='start')
        )
    await state.clear()

@dp.callback_query(F.data == 'cancel_payment')
async def cancel_payment(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer('Оплата отменена. Вы можете вернуться в главное меню', reply_markup=await get_reply_keyboard(keyboard_type='start'))

@dp.callback_query(F.data == 'back_to_start')
async def back_to_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer('Вы вернулись в главное меню', reply_markup=await get_reply_keyboard(keyboard_type='start'))

@dp.callback_query(F.data == 'extend_subscription')
async def extend_subscription(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await subscription_service.get_user_by_telegram_id(user_id)
    # Получаем активные подписки асинхронно
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
        active_subs = result.scalars().all()
    if not active_subs:
        await callback.message.answer('У вас нет активной подписки для продления.', reply_markup=await get_reply_keyboard(keyboard_type='start'))
        return
    subscription = active_subs[0]
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
        plan = result.scalar_one_or_none()
    # Определяем срок продления
    if plan and plan.name == 'Базовый 5 минут':
        days = 5 / (24 * 60)
        duration_text = '5 минут (тест)'
    else:
        days = 30
        duration_text = '30 дней'
    # Продлеваем подписку
    async with subscription_service.async_session_maker() as session:
        manager = SubscriptionManager(session)
        subscription = await manager.extend_subscription(subscription.id, days)
        subscription.reminder_sent = False
        session.add(subscription)
        await session.commit()
    # Генерируем новую ссылку-приглашение, если требуется
    invite_link = None
    if plan and plan.channel_id and subscription_service.bot:
        try:
            invite_link = await subscription_service.create_channel_invite(plan.channel_id, user.telegram_user_id)
            async with subscription_service.async_session_maker() as session:
                result = await session.execute(select(UserSubscription).where(UserSubscription.id == subscription.id))
                sub = result.scalar_one_or_none()
                if sub:
                    sub.invite_link = invite_link
                    session.add(sub)
                    await session.commit()
        except Exception as e:
            invite_link = None
    # Формируем сообщение
    end_date = subscription.end_date.strftime('%d.%m.%Y %H:%M') if plan and plan.name == 'Базовый 5 минут' else subscription.end_date.strftime('%d.%m.%Y')
    message = f'Подписка успешно продлена!\nНовая дата окончания: {end_date}'
    if invite_link:
        message += f"\n\nВаша новая ссылка для входа в канал: {invite_link}\n⚠️ Перейдя по ссылке, нажмите 'Запросить вступление'. Ваш запрос будет автоматически одобрен."
    await callback.message.answer(message, reply_markup=await get_reply_keyboard(keyboard_type='start'))
    await callback.answer()

@dp.callback_query(F.data == 'confirm_cancel_subscription')
async def confirm_cancel_subscription(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await subscription_service.get_user_by_telegram_id(user_id)
    # Получаем активные подписки асинхронно
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
        active_subs = result.scalars().all()
    if not active_subs:
        await callback.message.answer('У вас нет активной подписки для отмены.', reply_markup=await get_reply_keyboard(keyboard_type='start'))
        return
    subscription = active_subs[0]
    await subscription_service.remove_user_access(subscription)
    await callback.message.answer('Ваша подписка отменена. Доступ к каналу отозван. Деньги за неиспользованный период не возвращаются.', reply_markup=await get_reply_keyboard(keyboard_type='start'))
    await callback.answer()

async def monitor_subscriptions():
    """Фоновая задача для мониторинга подписок и отзыва доступа"""
    last_check = datetime.utcnow()
    while True:
        try:
            now = datetime.utcnow()
            # Напоминания за 24 часа до окончания
            expiring = await subscription_service.get_expiring_subscriptions(hours=24)
            for sub in expiring:
                if not getattr(sub, 'reminder_sent', False):
                    # Получаем пользователя асинхронно
                    user = await subscription_service.get_user_by_telegram_id(sub.user_id)
                    if user:
                        try:
                            await bot.send_message(
                                chat_id=user.telegram_user_id,
                                text="⏰ Ваша подписка истекает через 24 часа! Продлите её, чтобы не потерять доступ к каналу."
                            )
                            sub.reminder_sent = True
                            # Сохраняем обновление через асинхронный сервис
                            # (можно реализовать отдельный метод для этого)
                        except Exception as e:
                            logging.error(f"Ошибка при отправке напоминания пользователю {user.telegram_user_id}: {e}")
            # Отзыв доступа для истекших подписок
            expired = await subscription_service.get_expired_subscriptions()  # реализовать этот метод
            for sub in expired:
                user = await subscription_service.get_user_by_telegram_id(sub.user_id)
                try:
                    await subscription_service.remove_user_access(sub)
                    if user:
                        await bot.send_message(
                            chat_id=user.telegram_user_id,
                            text="❌ Ваша подписка истекла. Доступ к каналу отозван. Оформите новую подписку для восстановления доступа."
                        )
                except Exception as e:
                    logging.error(f"Ошибка при отзыве доступа у пользователя {getattr(user, 'telegram_user_id', '?')}: {e}\n{traceback.format_exc()}")
            # Проверяем подписки, которые истекли за последние 2 минуты
            recently_expired = await subscription_service.get_recently_expired_subscriptions(last_check, now)  # реализовать этот метод
            for sub in recently_expired:
                if not getattr(sub, 'reminder_sent', False):
                    user = await subscription_service.get_user_by_telegram_id(sub.user_id)
                    if user:
                        try:
                            await bot.send_message(
                                chat_id=user.telegram_user_id,
                                text="❌ Ваша подписка истекла. Доступ к каналу отозван. Оформите новую подписку для восстановления доступа."
                            )
                            sub.reminder_sent = True
                        except Exception as e:
                            logging.error(f"Ошибка при отправке уведомления о завершении подписки пользователю {user.telegram_user_id}: {e}")
            last_check = now
        except Exception as e:
            logging.error(f"Ошибка в задаче мониторинга подписок: {e}\n{traceback.format_exc()}")
        await asyncio.sleep(60)

async def main():
    """Запуск бота"""
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting bot")
    logging.info(f"Платежный токен: {TELEGRAM_PAYMENT_TOKEN[:10]}... (Тестовый режим: {IS_TEST_MODE})")
    logging.info(f"Каналы: Базовый: {CHANNEL_IDS['basic_subscription']}, Премиум: {CHANNEL_IDS['premium_subscription']}")

    await async_init_db()  # Сначала создаём таблицы!
    await subscription_service._init_subscription_plans()  # Потом инициализируем тарифы

    try:
        # Запускаем мониторинг подписок параллельно с polling'ом
        await asyncio.gather(
            monitor_subscriptions(),
            dp.start_polling(bot)
        )
    finally:
        # Закрываем соединение с базой данных при завершении работы
        subscription_service.close()

if __name__ == "__main__":
    asyncio.run(main()) 



