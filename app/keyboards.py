from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


async def get_reply_keyboard(keyboard_type: str):
    match keyboard_type:
        case 'start':
            reply_keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text='Управление подпиской')]
                ],
                resize_keyboard=True
            )
        case _:
            raise ValueError(f"Неизвестный тип клавиатуры: {keyboard_type}")
    return reply_keyboard


async def get_inline_keyboard(keyboard_type: str):
    match keyboard_type:
        case 'manage_subscription':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='Купить подписку', callback_data='buy_subscription'),
                     InlineKeyboardButton(text='Вернуться назад', callback_data='back_to_start')]
                ]
            )
            
        case 'manage_existing_subscription':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='Продлить подписку', callback_data='extend_subscription')],
                    [InlineKeyboardButton(text='Сменить тариф', callback_data='change_subscription')],
                    [InlineKeyboardButton(text='Отменить подписку', callback_data='cancel_subscription')],
                    [InlineKeyboardButton(text='Вернуться назад', callback_data='back_to_start')]
                ]
            )
            
        case 'choose_subscription_type':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='Базовый', callback_data='basic_subscription'),
                     InlineKeyboardButton(text='Премиум', callback_data='premium_subscription')]
                ]
            )
        
        case 'choose_subscription_duration':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='30 дней', callback_data='30_days')]
                ]
            )
        
        case 'confirm_payment':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='Подтвердить оплату', callback_data='confirm_payment')],
                    [InlineKeyboardButton(text='Отменить', callback_data='cancel_payment')]
                ]
            )
            
        case 'confirm_cancel_subscription':
            inline_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='Подтвердить отмену', callback_data='confirm_cancel_subscription')],
                    [InlineKeyboardButton(text='Отмена', callback_data='back_to_start')]
                ]
            )
            
        case _:
            raise ValueError(f"Неизвестный тип клавиатуры: {keyboard_type}")
    return inline_keyboard


