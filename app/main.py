import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram import Router, types, F
from aiogram.filters import Command
import os
from keyboards import get_inline_keyboard
import logging
from dotenv import load_dotenv
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from app.subscription_service import subscription_service, CHANNEL_IDS, DEFAULT_PLAN_PRICE
from app.database import User, UserSubscription, SubscriptionPlan, PaymentError, async_init_db
from aiogram.types import LabeledPrice
from aiogram.types.message import ContentType
from aiogram.types import ChatJoinRequest
import traceback
from datetime import datetime, timedelta
from sqlalchemy import select
from app.subscription_service import SubscriptionManager
import json

from entry_text import WELCOME_TEXT


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
    #choosing_type = State()
    confirming_payment = State()


def get_sanitized_payment_info(payment_info: types.SuccessfulPayment) -> str:
    """
    Возвращает строковое представление объекта SuccessfulPayment с удаленными персональными данными.
    """
    if payment_info is None:
        return "None"

    try:
        # В aiogram 3.x объекты являются моделями Pydantic
        if hasattr(payment_info, 'model_dump'):
            payment_dict = payment_info.model_dump()
        else:
            payment_dict = dict(payment_info)

        if payment_dict.get('order_info'):
            payment_dict['order_info'] = "[REDACTED]"
        return str(payment_dict)
    except Exception:
        # Резервный вариант на случай ошибки при сериализации
        return f"SuccessfulPayment(total_amount={getattr(payment_info, 'total_amount', 'unknown')}, currency='{getattr(payment_info, 'currency', 'unknown')}', order_info=[REDACTED])"


# Хранилище состояний в памяти
storage = MemoryStorage()
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Устанавливаем экземпляр бота в сервис подписок
subscription_service.set_bot(bot)

# Загрузка списка администраторов из .env
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '').split(',')
if not ADMIN_USER_IDS[0]:
    logging.warning("Не заданы ID администраторов (ADMIN_USER_IDS) в .env!")

@dp.message(Command('start'))
async def start_command(message: types.Message, state: FSMContext):
    # При старте сбрасываем состояние
    await state.clear()
    first_name = message.from_user.first_name or ''
    
    user = await subscription_service.get_user_by_telegram_id(message.from_user.id)
    if user and first_name:
        async with subscription_service.async_session_maker() as session:
            result = await session.execute(select(User).where(User.id == user.id))
            db_user = result.scalar_one_or_none()
            if db_user:
                db_user.first_name = first_name
                session.add(db_user)
                await session.commit()

    # Проверяем наличие активной подписки
    subscription_info = await subscription_service.get_subscription_info(message.from_user.id)
    
    if subscription_info and subscription_info.get('is_active'):
        # Если есть активная подписка, показываем "Личный кабинет"
        days_left = subscription_info['days_left']
        message_text = (
            f"Добро пожаловать, {first_name}!\n\n"
            f"✅ У вас есть активная подписка: {subscription_info['plan_name']}\n"
            f"Действует до: {subscription_info['end_date'].strftime('%d.%m.%Y')}\n"
            f"Осталось дней: {days_left}"
        )

        if subscription_info.get('invite_link'):
            message_text += f"\n\nСсылка для входа в канал: {subscription_info['invite_link']}"
            message_text += "\n\n⚠️ Эта ссылка доступна только вам. При переходе по ссылке вам нужно будет отправить запрос на вступление, который будет автоматически одобрен."

        await message.answer(message_text, reply_markup=await get_inline_keyboard(keyboard_type='manage_existing_subscription'))
    else:
        # Если подписки нет, показываем приветствие и кнопку оплаты
        text1 = WELCOME_TEXT
        text2 = "🔥Доступ к каналу с товарами от 60₽"

        await message.answer(text1, parse_mode='HTML')

        premium_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="Оплатить подписку", callback_data='buy_subscription')]
            ]
        )
        await message.answer(text2, reply_markup=premium_keyboard)

@dp.message(Command('subscription'))
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

@dp.message(Command('details'))
async def details_command(message: types.Message):
    """
    Показывает дату окончания подписки и прямую ссылку на канал.
    Ссылка формируется динамически на основе купленного плана.
    """
    # 1. Получаем данные о подписке (включая ID канала из плана)
    subscription_info = await subscription_service.get_subscription_info(message.from_user.id)

    if subscription_info and subscription_info['is_active']:
        # Форматируем дату
        end_date = subscription_info['end_date'].strftime('%d.%m.%Y')
        
        # Получаем ID канала из подписки (он берется из базы, куда попал из .env)
        raw_channel_id = str(subscription_info['channel_id'])
        
        # 2. Формируем прямую ссылку на канал для подписчиков
        # ID обычно выглядит как -1001234567890. Для ссылки t.me/c/ нужно убрать "-100"
        if raw_channel_id.startswith('-100'):
            clean_id = raw_channel_id[4:] # Убираем первые 4 символа (-100)
            channel_link = f"https://t.me/c/{clean_id}/1"
        else:
            # Если вдруг ID короткий (старый тип групп) или публичный юзернейм
            channel_link = f"https://t.me/c/{raw_channel_id.replace('-', '')}/1"

        text = (
            f"📅 <b>Подписка активна до:</b> {end_date}\n\n"
            f"🔗 <b>Ваш канал:</b> <a href=\"{channel_link}\">Открыть канал</a>\n"
            f"<i>(Эта ссылка работает, если вы уже состоите в канале)</i>"
        )
        
        await message.answer(text, parse_mode='HTML', disable_web_page_preview=True)
        
    else:
        await message.answer("❌ Активная подписка не найдена.")


@dp.message(Command('help'))
async def help_command(message: types.Message, state: FSMContext):
    first_name = message.from_user.first_name or ''
    text1 = '''🤝 Поддержка

Если у вас есть вопрос - напишите мне @mariidori
'''   
    await message.answer(text1, parse_mode='HTML',
                         #reply_markup=await get_reply_keyboard(keyboard_type='start')
                        )


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
    is_valid = await subscription_service.is_valid_join_request(invite_link, user_id)
    if is_valid:
        # Одобряем запрос
        try:
            await bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
            logging.info(f"Одобрен запрос на вступление для пользователя {user_id}")
            
            # Отзываем ссылку сразу после одобрения
            try:
                async with subscription_service.async_session_maker() as session:
                    link_result = await session.execute(select(UserSubscription).where(UserSubscription.invite_link == invite_link))
                    sub = link_result.scalar_one_or_none()
                    if sub and sub.invite_link:
                        await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=sub.invite_link)
                        sub.invite_link = None
                        session.add(sub)
                        await session.commit()
                        logging.info(f"Ссылка {invite_link} отозвана после успешного вступления пользователя {user_id}")
            except Exception as e:
                logging.error(f"Ошибка при отзыве ссылки после вступления: {str(e)}")
            
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
    # Получаем актуальные тарифы
    plans = await subscription_service.get_active_plans()
    
    # Сортируем планы по цене
    plans.sort(key=lambda x: x.price)

    keyboard_buttons = []
    for plan in plans:
        # Форматируем цену: 6000 -> 60 RUB
        price_rub = int(plan.price / 100)
        button_text = f"{plan.name} - {price_rub}₽"
        keyboard_buttons.append([types.InlineKeyboardButton(text=button_text, callback_data=f'select_plan_{plan.id}')])

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await callback.message.answer('Выберите подходящий тариф:', reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == 'change_subscription')
async def change_subscription(callback: types.CallbackQuery, state: FSMContext):
    # Логика та же, что и при покупке - показываем список тарифов
    await buy_subscription(callback, state)

@dp.callback_query(F.data.startswith('select_plan_'))
async def process_plan_selection(callback: types.CallbackQuery, state: FSMContext):
    try:
        plan_id = int(callback.data.split('_')[-1])

        # Получаем план
        async with subscription_service.async_session_maker() as session:
            result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
            plan = result.scalar_one_or_none()

        if not plan:
            await callback.message.answer("Ошибка: тариф не найден.")
            return

        await send_invoice_for_plan(callback, state, plan, edit=False, is_extension=False)
    except Exception as e:
        logging.error(f"Error in process_plan_selection: {e}")
        await callback.message.answer("Произошла ошибка при выборе тарифа.")

async def send_invoice_for_plan(callback, state, plan, edit=False, is_extension=False):
    # preview_text = (
    #     f"Вы выбрали {'продление подписки' if is_extension else 'подписку'}: {plan.name}\n"
    #     f"Описание: {plan.description or '-'}\n"
    #     f"Длительность: {plan.duration_days} дней\n"
    #     f"Стоимость: {plan.price/100:.2f} руб.\n\n"
    #     f"Нажмите кнопку ниже для оплаты:"
    # )
    # try:
    #     if edit:
    #         await callback.message.edit_text(preview_text)
    #     else:
    #         await callback.message.answer(preview_text)
    # except Exception as e:
    #     logging.error(f"Ошибка при отправке превью подписки: {str(e)}\nTRACEBACK: {traceback.format_exc()}")
    #     await callback.message.answer(preview_text)
    try:
        # Подготавливаем данные для чека (provider_data)
        provider_data = {
            "receipt": {
                "items": [
                    {
                        "description": f"{'Продление подписки' if is_extension else 'Подписка'} {plan.name} на {plan.duration_days} дней",
                        "quantity": 1.0,
                        "amount": {
                            "value": plan.price / 100,  # В рублях, а не копейках
                            "currency": "RUB"
                        },
                        "vat_code": 1,  # НДС 20%
                        "payment_mode": "full_payment",
                        "payment_subject": "service"  # Услуга
                    }
                ],
                "tax_system_code": 1  # Общая система налогообложения
            }
        }
        provider_data_json = json.dumps(provider_data)
        # Клавиатура только для инвойса
        invoice_keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="Оплатить", pay=True)],
                [types.InlineKeyboardButton(text="↩️ Назад к выбору тарифа", callback_data="back_to_plan_selection")]
            ]
        )
        
        # Формируем payload в зависимости от типа операции (новая подписка или продление)
        payload = f"extend_{plan.id}" if is_extension else f"plan_{plan.id}"
        
        invoice_message = await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"{'Продление подписки' if is_extension else 'Подписка'} {plan.name}",
            description=f"Оплата {'продления доступа' if is_extension else 'доступа'} к тарифу {plan.name}, продолжительность - {plan.duration_days} дней",
            payload=payload,
            provider_token=TELEGRAM_PAYMENT_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=plan.name, amount=plan.price)],
            start_parameter="subscription_payment",
            need_name=False,
            need_phone_number=False,
            need_email=True,
            send_email_to_provider=True,
            need_shipping_address=False,
            is_flexible=False,
            protect_content=True,
            provider_data=provider_data_json,
            reply_markup=invoice_keyboard
        )
        # Сохраняем id сообщений для удаления
        await state.update_data(preview_msg_id=callback.message.message_id, invoice_msg_id=invoice_message.message_id)
        logging.info(f"[INVOICE] Инвойс успешно отправлен пользователю {callback.from_user.id}")
    except Exception as e:
        logging.error(f"[INVOICE][ERROR] Ошибка при создании платежа: {str(e)}\nTRACEBACK: {traceback.format_exc()}")
        logging.error(f"[INVOICE][ERROR] Параметры платежа при ошибке: chat_id={callback.from_user.id}, title={plan.name}, description=Оплата доступа к тарифу {plan.name}, продолжительность - {plan.duration_days} дней, payload=plan_{plan.id}, provider_token={TELEGRAM_PAYMENT_TOKEN}, currency=RUB, price={plan.price}, need_email=True, send_email_to_provider=True")
        await callback.message.answer(
            f"Произошла ошибка при создании платежа: {str(e)}",
            # reply_markup=await get_reply_keyboard(keyboard_type='start')
        )
        await state.clear()

# @dp.callback_query(SubscriptionStates.choosing_type, lambda c: c.data.startswith('plan_'))
# async def process_subscription_plan(callback: types.CallbackQuery, state: FSMContext):
#     plan_id = int(callback.data.replace('plan_', ''))
#     # Получаем тариф из базы
#     async with subscription_service.async_session_maker() as session:
#         result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
#         plan = result.scalar_one_or_none()
#     if not plan:
#         await callback.message.answer('Ошибка: выбранный тариф не найден.')
#         return
#     await state.update_data(plan_id=plan_id)
#     # Показываем превью и инвойс
#     await send_invoice_for_plan(callback, state, plan, edit=True)

@dp.callback_query(F.data == 'cancel_payment')
async def cancel_payment(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer('Оплата отменена. Вы можете вернуться в главное меню',
                                  #reply_markup=await get_reply_keyboard(keyboard_type='start')
                                  )

# @dp.callback_query(F.data == 'back_to_start')
# async def back_to_start(callback: types.CallbackQuery, state: FSMContext):
#     await state.clear()
#     await callback.message.answer('Вы вернулись в главное меню', reply_markup=await get_reply_keyboard(keyboard_type='start'))

@dp.callback_query(F.data == 'cancel_subscription')
async def cancel_subscription_request(callback: types.CallbackQuery, state: FSMContext):
    """Запрос на отмену подписки - показывает подтверждение"""
    await callback.message.answer(
        "⚠️ Вы уверены, что хотите отменить подписку? Доступ к каналу будет отозван, деньги за неиспользованный период не возвращаются.",
        reply_markup=await get_inline_keyboard(keyboard_type='confirm_cancel_subscription')
    )
    await callback.answer()

@dp.callback_query(F.data == 'extend_subscription')
async def extend_subscription(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await subscription_service.get_user_by_telegram_id(user_id)
    # Получаем активные подписки асинхронно
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
        active_subs = result.scalars().all()
    if not active_subs:
        await callback.message.answer(
            'У вас нет активной подписки для продления.',
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
        return
    subscription = active_subs[0]
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
        plan = result.scalar_one_or_none()
    if not plan:
        await callback.message.answer(
            'Ошибка: тариф не найден.',
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
        return
    
    # Сохраняем информацию о текущей подписке в состоянии для использования после оплаты
    await state.update_data(
        extend_subscription_id=subscription.id,
        plan_id=plan.id,
    )
    
    # Отправляем инвойс для оплаты продления
    await send_invoice_for_plan(callback, state, plan, edit=False, is_extension=True)

@dp.callback_query(F.data == 'confirm_cancel_subscription')
async def confirm_cancel_subscription(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await subscription_service.get_user_by_telegram_id(user_id)
    logging.info(f"[CANCEL] Пользователь {user_id} инициировал отмену подписки")
    # Получаем активные подписки асинхронно
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(UserSubscription).where(UserSubscription.user_id == user.id, UserSubscription.is_active == True))
        active_subs = result.scalars().all()
    if not active_subs:
        logging.warning(f"[CANCEL] Нет активной подписки для пользователя {user_id}")
        await callback.message.answer(
            'У вас нет активной подписки для отмены.',
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
        return
    subscription = active_subs[0]
    
    # Получаем информацию о плане подписки, чтобы знать channel_id
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
        plan = result.scalar_one_or_none()
    
    if not plan:
        logging.error(f"[CANCEL] Не найден тариф для подписки {subscription.id}")
        await callback.message.answer(
            'Ошибка: не удалось найти тариф для вашей подписки.',
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
        return
    
    # Устанавливаем channel_id из плана в подписку для метода remove_user_access
    subscription.channel_id = plan.channel_id
    logging.info(f"[CANCEL] Передаю подписку {subscription.id} с channel_id={subscription.channel_id} в remove_user_access")
    
    # Отзываем доступ
    success = await subscription_service.remove_user_access(subscription)
    
    # Проверяем статус подписки после отмены
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(UserSubscription).where(UserSubscription.id == subscription.id))
        updated_sub = result.scalar_one_or_none()
        logging.info(f"[CANCEL] Статус подписки после отмены: is_active={getattr(updated_sub, 'is_active', None)}, invite_link={getattr(updated_sub, 'invite_link', None)}")
    
    if success:
        await callback.message.answer(
            'Ваша подписка отменена. Доступ к каналу отозван. Деньги за неиспользованный период не возвращаются.', 
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
    else:
        await callback.message.answer(
            'Произошла ошибка при отмене подписки. Пожалуйста, попробуйте позже или обратитесь в поддержку.',
            #reply_markup=await get_reply_keyboard(keyboard_type='start')
            )
    
    await callback.answer()

# Обработчик предварительной проверки платежа (обязательно нужен для работы платежей)
@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    logging.info(f"[PRE_CHECKOUT] Получен pre_checkout_query: {pre_checkout_query}")
    try:
        payload = pre_checkout_query.invoice_payload
        logging.info(f"[PRE_CHECKOUT] Payload: {payload}")
        
        if payload.startswith('plan_') or payload.startswith('extend_'):
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
            logging.info(f"[PRE_CHECKOUT] Pre-checkout подтвержден для запроса {pre_checkout_query.id}")
        else:
            logging.error(f"[PRE_CHECKOUT][ERROR] Некорректный формат payload: {payload}")
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка обработки платежа: некорректный формат данных.")
    except Exception as e:
        logging.error(f"[PRE_CHECKOUT][ERROR] Ошибка при обработке pre_checkout_query: {str(e)}\nTRACEBACK: {traceback.format_exc()}")
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка обработки платежа. Пожалуйста, попробуйте позже.")


# Обработчик успешной оплаты
@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message, state: FSMContext):
    logging.info(f"[PAYMENT] Получено уведомление об успешном платеже: {get_sanitized_payment_info(message.successful_payment)}")
    try:
        payment_info = message.successful_payment
        payload = payment_info.invoice_payload
        provider_payment_charge_id = payment_info.provider_payment_charge_id
        # Не логируем order_info напрямую, так как он содержит персональные данные (email)
        logging.info(f"[PAYMENT] payload={payload}, charge_id={provider_payment_charge_id}, сумма={payment_info.total_amount}, валюта={payment_info.currency}, order_info=[REDACTED]")
        
        # Обработка различных типов платежей
        if payload.startswith('plan_'):
            # Создание новой подписки
            plan_id = int(payload.replace('plan_', ''))
            try:
                # КРИТИЧЕСКАЯ ОПЕРАЦИЯ: создание подписки
                logging.info(f"[PAYMENT] Начинаем создание подписки для пользователя {message.from_user.id}, план {plan_id}")
                subscription_id = await subscription_service.create_subscription(
                    message.from_user.id, 
                    plan_id=plan_id
                )
                logging.info(f"[PAYMENT] Подписка успешно создана с ID={subscription_id}")
                
                # Сохраняем provider_payment_charge_id в подписке в новой сессии
                async with subscription_service.async_session_maker() as session:
                    result = await session.execute(select(UserSubscription).where(UserSubscription.id == subscription_id))
                    sub = result.scalar_one_or_none()
                    if sub:
                        logging.info(f"[PAYMENT] Найдена подписка для сохранения charge_id: {sub}")
                        sub.provider_payment_charge_id = provider_payment_charge_id
                        session.add(sub)
                        await session.commit()
                        logging.info(f"[PAYMENT] Сохранён provider_payment_charge_id в подписке: {sub}")
                    else:
                        logging.error(f"[PAYMENT][ERROR] Не удалось найти подписку для сохранения charge_id")
                # Получаем информацию о плане для формирования ответа
                plan = None
                subscription = None
                async with subscription_service.async_session_maker() as session:
                    result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
                    plan = result.scalar_one_or_none()
                if not plan:
                    raise ValueError(f"План с ID {plan_id} не найден после оплаты")
                # Получаем подписку для отображения даты окончания
                async with subscription_service.async_session_maker() as session:
                    result = await session.execute(select(UserSubscription).where(UserSubscription.id == subscription_id))
                    subscription = result.scalar_one_or_none()
                if not subscription:
                    raise ValueError(f"Подписка с ID {subscription_id} не найдена после сохранения")
                    
                response_text = f"✅ Оплата успешно выполнена!\n\n"
                response_text += f"Подписка: {plan.name}\n"
                response_text += f"Срок действия: до {subscription.end_date.strftime('%d.%m.%Y')}\n\n"
                if hasattr(subscription, 'invite_link') and subscription.invite_link:
                    response_text += f"Ссылка для входа в канал: {subscription.invite_link}\n"
                    response_text += "⚠️ Перейдя по ссылке, нажмите 'Запросить вступление'. Ваш запрос будет автоматически одобрен."
                await message.answer(
                    response_text,
                    #reply_markup=await get_reply_keyboard(keyboard_type='start')
                    )
                logging.info(f"[PAYMENT] Подписка успешно создана для пользователя {message.from_user.id}, план {plan_id}, charge_id={provider_payment_charge_id}")
                # Логируем содержимое подписки из базы
                logging.info(f"[PAYMENT] Итоговое состояние подписки в базе: {subscription}")
            except Exception as e:
                stack_trace = traceback.format_exc()
                logging.critical(f"[PAYMENT][CRITICAL_ERROR] Ошибка при создании подписки после оплаты: {str(e)}\nTRACEBACK: {stack_trace}")
                
                # Сохраняем информацию об ошибке в базу данных
                try:
                    async with subscription_service.async_session_maker() as session:
                        payment_error = PaymentError(
                            telegram_user_id=str(message.from_user.id),
                            plan_id=plan_id,
                            provider_payment_charge_id=provider_payment_charge_id,
                            payment_amount=payment_info.total_amount,
                            payment_currency=payment_info.currency,
                            error_message=str(e),
                            invoice_payload=payload,
                            payment_info=get_sanitized_payment_info(payment_info),
                            stack_trace=stack_trace
                        )
                        session.add(payment_error)
                        await session.commit()
                        logging.info(f"[PAYMENT][ERROR_SAVED] Информация об ошибке сохранена в базу данных с ID={payment_error.id}")

                        # Уведомление администратора
                        for admin_id in ADMIN_USER_IDS:
                            if not admin_id: continue
                            try:
                                await bot.send_message(
                                    chat_id=admin_id,
                                    text=f"🚨 <b>Ошибка оплаты!</b>\n\n"
                                         f"Пользователь: {message.from_user.id} ({message.from_user.username or 'No username'})\n"
                                         f"Сумма: {payment_info.total_amount / 100} {payment_info.currency}\n"
                                         f"План ID: {plan_id}\n"
                                         f"Ошибка: {str(e)[:200]}\n"
                                         f"ID ошибки в БД: {payment_error.id}",
                                    parse_mode='HTML'
                                )
                            except Exception as admin_notify_error:
                                logging.error(f"Не удалось отправить уведомление админу {admin_id}: {admin_notify_error}")

                except Exception as db_error:
                    logging.critical(f"[PAYMENT][DB_ERROR] Не удалось сохранить информацию об ошибке в базу данных: {str(db_error)}")
                
                # Экстренное сохранение информации о платеже в логах для ручного восстановления
                emergency_info = {
                    "user_id": message.from_user.id,
                    "plan_id": plan_id,
                    "charge_id": provider_payment_charge_id,
                    "payment_time": datetime.now().isoformat(),
                    "payment_info": get_sanitized_payment_info(payment_info),
                    "error": str(e)
                }
                logging.critical(f"[PAYMENT][EMERGENCY] Данные платежа для ручного восстановления: {emergency_info}")
                
                await message.answer(
                    "⚠️ Платеж выполнен, но возникла техническая ошибка при активации подписки. Наши специалисты уже работают над этим и восстановят ваш доступ в ближайшее время. Пожалуйста, сохраните этот чат для подтверждения оплаты.", 
                    #reply_markup=await get_reply_keyboard(keyboard_type='start')
                    )
        
        elif payload.startswith('extend_'):
            # Продление существующей подписки
            plan_id = int(payload.replace('extend_', ''))
            
            try:
                # Получаем данные из состояния
                user_data = await state.get_data()
                subscription_id = user_data.get('extend_subscription_id')
                
                if not subscription_id:
                    raise ValueError("Не найден ID подписки для продления")
                
                logging.info(f"[PAYMENT][EXTEND] Начинаем продление подписки ID={subscription_id}, план {plan_id}")
                
                # Получаем информацию о плане
                async with subscription_service.async_session_maker() as session:
                    result = await session.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
                    plan = result.scalar_one_or_none()
                
                if not plan:
                    raise ValueError(f"План с ID {plan_id} не найден для продления")
                
                days = plan.duration_days
                
                # Продляем подписку
                async with subscription_service.async_session_maker() as session:
                    manager = SubscriptionManager(session)
                    subscription = await manager.extend_subscription(subscription_id, days, reminder_sent=False)
                    
                    # Сохраняем информацию о платеже
                    subscription.provider_payment_charge_id = provider_payment_charge_id
                    
                    await session.commit()
                
                # Генерируем новую ссылку-приглашение
                invite_link = None
                user = await subscription_service.get_user_by_telegram_id(message.from_user.id)
                
                if plan.channel_id and subscription_service.bot:
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
                        logging.error(f"[PAYMENT][EXTEND] Ошибка при создании ссылки-приглашения: {str(e)}")
                        invite_link = None
                
                # Формируем ответ
                end_date = subscription.end_date.strftime('%d.%m.%Y')
                response_text = f"✅ Оплата успешно выполнена!\n\n"
                response_text += f"Подписка продлена: {plan.name}\n"
                response_text += f"Срок действия: до {end_date}\n\n"
                
                if invite_link:
                    response_text += f"Ваша новая ссылка для входа в канал: {invite_link}\n"
                    response_text += "⚠️ Перейдя по ссылке, нажмите 'Запросить вступление'. Ваш запрос будет автоматически одобрен."
                
                await message.answer(
                    response_text,
                    #reply_markup=await get_reply_keyboard(keyboard_type='start')
                    )
                logging.info(f"[PAYMENT][EXTEND] Подписка успешно продлена для пользователя {message.from_user.id}, ID={subscription.id}, план {plan_id}")
            
            except Exception as e:
                stack_trace = traceback.format_exc()
                logging.critical(f"[PAYMENT][EXTEND][ERROR] Ошибка при продлении подписки: {str(e)}\nTRACEBACK: {stack_trace}")
                
                # Сохраняем информацию об ошибке
                try:
                    async with subscription_service.async_session_maker() as session:
                        payment_error = PaymentError(
                            telegram_user_id=str(message.from_user.id),
                            plan_id=plan_id,
                            provider_payment_charge_id=provider_payment_charge_id,
                            payment_amount=payment_info.total_amount,
                            payment_currency=payment_info.currency,
                            error_message=f"Ошибка при продлении подписки: {str(e)}",
                            invoice_payload=payload,
                            payment_info=get_sanitized_payment_info(payment_info),
                            stack_trace=stack_trace
                        )
                        session.add(payment_error)
                        await session.commit()
                        logging.info(f"[PAYMENT][EXTEND][ERROR_SAVED] Информация об ошибке продления сохранена в БД с ID={payment_error.id}")

                        # Уведомление администратора
                        for admin_id in ADMIN_USER_IDS:
                            if not admin_id: continue
                            try:
                                await bot.send_message(
                                    chat_id=admin_id,
                                    text=f"🚨 <b>Ошибка продления подписки!</b>\n\n"
                                         f"Пользователь: {message.from_user.id} ({message.from_user.username or 'No username'})\n"
                                         f"Сумма: {payment_info.total_amount / 100} {payment_info.currency}\n"
                                         f"План ID: {plan_id}\n"
                                         f"Ошибка: {str(e)[:200]}\n"
                                         f"ID ошибки в БД: {payment_error.id}",
                                    parse_mode='HTML'
                                )
                            except Exception as admin_notify_error:
                                logging.error(f"Не удалось отправить уведомление админу {admin_id}: {admin_notify_error}")

                except Exception as db_error:
                    logging.critical(f"[PAYMENT][EXTEND][DB_ERROR] Не удалось сохранить информацию об ошибке в БД: {str(db_error)}")
                
                await message.answer("⚠️ Платеж выполнен, но возникла техническая ошибка при продлении подписки. Наши специалисты уже работают над этим и скоро восстановят ваш доступ.", 
                                   #reply_markup=await get_reply_keyboard(keyboard_type='start')
                                   )
        
        else:
            logging.error(f"[PAYMENT][ERROR] Некорректный формат payload после оплаты: {payload}")
            await message.answer("Произошла ошибка при обработке платежа. Пожалуйста, обратитесь в поддержку.", 
                               #reply_markup=await get_reply_keyboard(keyboard_type='start')
                               )
            return
            
    except Exception as e:
        stack_trace = traceback.format_exc()
        logging.error(f"[PAYMENT][ERROR] Ошибка при обработке успешного платежа: {str(e)}\nTRACEBACK: {stack_trace}")
        
        # Пытаемся сохранить информацию об ошибке в базу данных, даже если не удалось получить детали платежа
        try:
            if 'payment_info' in locals():
                async with subscription_service.async_session_maker() as session:
                    payment_error = PaymentError(
                        telegram_user_id=str(message.from_user.id),
                        provider_payment_charge_id=getattr(payment_info, 'provider_payment_charge_id', 'unknown'),
                        payment_amount=getattr(payment_info, 'total_amount', None),
                        payment_currency=getattr(payment_info, 'currency', None),
                        error_message=str(e),
                        invoice_payload=getattr(payment_info, 'invoice_payload', None),
                        payment_info=get_sanitized_payment_info(payment_info) if 'payment_info' in locals() else None,
                        stack_trace=stack_trace
                    )
                    session.add(payment_error)
                    await session.commit()
                    logging.info(f"[PAYMENT][ERROR_SAVED] Информация об общей ошибке сохранена в базу данных с ID={payment_error.id}")
        except Exception as db_error:
            logging.critical(f"[PAYMENT][DB_ERROR] Не удалось сохранить информацию об общей ошибке в базу данных: {str(db_error)}")
        
        await message.answer("Произошла ошибка при обработке платежа. Пожалуйста, обратитесь в поддержку.", 
                           #reply_markup=await get_reply_keyboard(keyboard_type='start')
                           )
    finally:
        await state.clear()

# Добавляем обработчик для кнопки "Назад к выбору тарифа"
# @dp.callback_query(F.data == 'back_to_plan_selection')
# async def back_to_plan_selection(callback: types.CallbackQuery, state: FSMContext):
#     # Получаем id сообщений для удаления
#     data = await state.get_data()
#     preview_msg_id = data.get('preview_msg_id')
#     invoice_msg_id = data.get('invoice_msg_id')
#     # Удаляем оба сообщения, если они есть
#     try:
#         if invoice_msg_id:
#             await callback.bot.delete_message(callback.message.chat.id, invoice_msg_id)
#         if preview_msg_id:
#             await callback.bot.delete_message(callback.message.chat.id, preview_msg_id)
#     except Exception as e:
#         logging.error(f"[BACK] Ошибка при удалении сообщений: {str(e)}\nTRACEBACK: {traceback.format_exc()}")
#     # Переходим обратно к выбору тарифа
#     #await state.set_state(SubscriptionStates.choosing_type)
#     async with subscription_service.async_session_maker() as session:
#         result = await session.execute(select(SubscriptionPlan))
#         plans = result.scalars().all()
#     keyboard = types.InlineKeyboardMarkup(
#         inline_keyboard=[
#             [types.InlineKeyboardButton(text=plan.name, callback_data=f'plan_{plan.id}')]
#             for plan in plans
#         ]
#     )
#     await callback.message.answer('Выберите тип подписки:', reply_markup=keyboard)
#     await callback.answer()

# Admin commands
@dp.message(Command('payment_errors'), lambda msg: str(msg.from_user.id) in ADMIN_USER_IDS)
async def show_payment_errors(message: types.Message, state: FSMContext):
    """Показать неразрешенные ошибки платежей (только для админов)"""
    async with subscription_service.async_session_maker() as session:
        result = await session.execute(select(PaymentError).where(PaymentError.is_resolved == False))
        errors = result.scalars().all()
    
    if not errors:
        await message.answer("Нет неразрешенных ошибок платежей.")
        return
    
    for error in errors:
        error_text = (
            f"🚨 Ошибка платежа #{error.id}:\n"
            f"Пользователь: {error.telegram_user_id}\n"
            f"Время платежа: {error.payment_time.strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"ID транзакции: {error.provider_payment_charge_id}\n"
            f"Сумма: {error.payment_amount/100 if error.payment_amount else 'N/A'} {error.payment_currency or 'N/A'}\n"
            f"План: {error.plan_id or 'N/A'}\n"
            f"Ошибка: {error.error_message}\n\n"
            f"Для разрешения используйте команду:\n"
            f"/resolve_payment_error {error.id} <причина решения>"
        )
        await message.answer(error_text)

@dp.message(lambda msg: msg.text and msg.text.startswith('/resolve_payment_error'), lambda msg: str(msg.from_user.id) in ADMIN_USER_IDS)
async def resolve_payment_error(message: types.Message, state: FSMContext):
    """Отметить ошибку платежа как разрешенную (только для админов)"""
    try:
        parts = message.text.split(' ', 2)
        if len(parts) < 2:
            await message.answer("Неверный формат команды. Используйте: /resolve_payment_error ID <причина решения>")
            return
        
        error_id = int(parts[1])
        notes = parts[2] if len(parts) > 2 else "Разрешено администратором"
        
        async with subscription_service.async_session_maker() as session:
            result = await session.execute(select(PaymentError).where(PaymentError.id == error_id))
            error = result.scalar_one_or_none()
            
            if not error:
                await message.answer(f"Ошибка платежа с ID {error_id} не найдена.")
                return
            
            error.is_resolved = True
            error.resolution_notes = notes
            error.resolution_time = datetime.utcnow()
            session.add(error)
            await session.commit()
        
        await message.answer(f"✅ Ошибка платежа #{error_id} помечена как разрешенная.")
        
        # Отправляем уведомление пользователю
        try:
            await bot.send_message(
                chat_id=error.telegram_user_id,
                text="✅ Проблема с вашим платежом была разрешена администратором. Если у вас остались вопросы, пожалуйста, свяжитесь с поддержкой."
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление пользователю {error.telegram_user_id}: {str(e)}")
    
    except ValueError:
        await message.answer("Неверный формат ID. Используйте: /resolve_payment_error ID <причина решения>")
    except Exception as e:
        await message.answer(f"Произошла ошибка: {str(e)}")

async def main():
    """Запуск бота"""
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting bot")
    logging.info(f"Платежный токен: {TELEGRAM_PAYMENT_TOKEN[:10]}... (Тестовый режим: {IS_TEST_MODE})")
    logging.info(f"Каналы: Базовый: {CHANNEL_IDS['basic_subscription']}, Премиум: {CHANNEL_IDS['premium_subscription']}")

    await async_init_db()  # Сначала создаём таблицы!
    await subscription_service._init_subscription_plans()  # Потом инициализируем тарифы

    # Запускаем поллинг (мониторинг подписок теперь выполняется через Celery)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 





