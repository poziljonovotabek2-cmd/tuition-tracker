"""Telegram bot with a persistent reply keyboard and a guided payment flow."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import httpx
from app.config import get_settings
from app.bot_students import (clear_flow, set_flow, student_buttons,
                              handle_student_text, search_prompt)

log = logging.getLogger('tuition.bot')
PAGE_SIZE = 5


@dataclass
class Screen:
    text: str
    buttons: list
    placeholder: str = 'Выберите кнопку или введите сумму'

    @property
    def markup(self):
        if not self.buttons:
            return {'remove_keyboard': True}
        return {'keyboard': [[{'text': b['text']} for b in row] for row in self.buttons],
                'resize_keyboard': True, 'is_persistent': True, 'one_time_keyboard': False,
                'input_field_placeholder': self.placeholder}


def button(text, data):
    return {'text': text, 'callback_data': data}


def back(target='menu'):
    return [button('⬅️ Назад', target)]


def home():
    return Screen('Главное меню\n\nВыберите действие:', [
        [button('💳 Внести оплату', 'pay:0')],
        [button('📋 Должники', 'debt:0'), button('👥 Ученики', 'students:0')],
        [button('➕ Добавить ученика', 'create'), button('🔎 Найти ученика', 'search')],
        [button('🌐 Язык / Til', 'language')],
        [button('❓ Помощь', 'help')],
    ])


UZ_TRANSLATIONS = {
    'Главное меню\n\nВыберите действие:': 'Asosiy menyu\n\nAmalni tanlang:',
    'Выберите действие:': 'Amalni tanlang:',
    'Ученики': 'O‘quvchilar',
    'Должники': 'Qarzdorlar',
    '💳 Внести оплату': '💳 To‘lov qilish',
    '📋 Должники': '📋 Qarzdorlar',
    '👥 Ученики': '👥 O‘quvchilar',
    '➕ Добавить ученика': '➕ O‘quvchi qo‘shish',
    '🔎 Найти ученика': '🔎 O‘quvchini qidirish',
    '❓ Помощь': '❓ Yordam',
    '🌐 Язык / Til': '🌐 Til / Язык',
    '⬅️ Назад': '⬅️ Orqaga',
    '🏠 Главное меню': '🏠 Asosiy menyu',
    'Как пользоваться': 'Foydalanish bo‘yicha qo‘llanma',
    'Внести оплату: выберите ученика, введите сумму и подтвердите.': 'To‘lov qilish: o‘quvchini tanlang, summani kiriting va tasdiqlang.',
    'Должники: долг за текущий месяц.': 'Qarzdorlar: joriy oy uchun qarzlar.',
    'Ученики: карточки с историей оплат.': 'O‘quvchilar: to‘lovlar tarixi bilan kartalar.',
    'Добавить ученика: заполните данные по шагам.': 'O‘quvchi qo‘shish: ma’lumotlarni bosqichma-bosqich kiriting.',
    'Найти ученика: поиск по имени или телефону.': 'O‘quvchini qidirish: ism yoki telefon orqali qidiring.',
    'Кнопка «Назад» возвращает на предыдущий шаг.': '«Orqaga» tugmasi oldingi bosqichga qaytaradi.',
    'Вернуться в меню можно командой /start.': '/start buyrug‘i bilan menyuga qaytishingiz mumkin.',
    'Выберите ученика для оплаты': 'To‘lov uchun o‘quvchini tanlang',
    'Учеников пока нет. Нажмите «Добавить ученика» в главном меню.': 'Hozircha o‘quvchilar yo‘q. Asosiy menyuda «O‘quvchi qo‘shish» tugmasini bosing.',
    '← Предыдущие': '← Oldingilar',
    'Следующие →': 'Keyingilar →',
    'страница': 'sahifa',
    'Страница ': 'Sahifa ',
    'Должники за': 'Qarzdorlar ·',
    'За ': '',
    ' должников нет.': ' uchun qarzdorlar yo‘q.',
    'Общий долг:': 'Jami qarz:',
    'Тел.:': 'Tel.:',
    'Долг:': 'Qarz:',
    'Группа:': 'Guruh:',
    'Тариф:': 'Oylik to‘lov:',
    'Внести оплату ·': 'To‘lov qilish ·',
    '/мес.': '/oy',
    'Введите сумму, например: 150000.50': 'Summani kiriting, masalan: 150000.50',
    'Введите положительную сумму с максимум 2 знаками после точки.': 'Noldan katta summani kiriting (nuqtadan keyin ko‘pi bilan 2 raqam).',
    'Проверьте оплату': 'To‘lovni tekshiring',
    '✅ Подтвердить оплату': '✅ To‘lovni tasdiqlash',
    '💳 Ещё оплата': '💳 Yana to‘lov',
    'сохранена': 'saqlandi',
    'Ученик #': 'O‘quvchi #',
    'Сумма:': 'Summa:',
    'Дата:': 'Sana:',
    'Как пользоваться': 'Foydalanish tartibi',
    'Оплата #': 'To‘lov #',
    'Этот экран устарел. Выберите действие заново.': 'Bu ekran eskirgan. Amalni qaytadan tanlang.',
    'Этот экран устарел. Начните создание заново.': 'Bu ekran eskirgan. O‘quvchi qo‘shishni qaytadan boshlang.',
    'Новый ученик · шаг': 'Yangi o‘quvchi · bosqich',
    'Введите имя ученика (до 150 символов).': 'O‘quvchining ismini kiriting (150 belgigacha).',
    'Введите телефон, например +998901234567.': 'Telefon raqamini kiriting, masalan +998901234567.',
    'Введите группу ученика (до 100 символов).': 'O‘quvchi guruhini kiriting (100 belgigacha).',
    'Введите месячную оплату, например 500000.00. Для бесплатного обучения — 0.': 'Oylik to‘lovni kiriting, masalan 500000.00. Bepul ta’lim uchun — 0.',
    'Предыдущее значение:': 'Oldingi qiymat:',
    'Проверьте нового ученика': 'Yangi o‘quvchi ma’lumotlarini tekshiring',
    'Имя:': 'Ism:',
    'Тел.:': 'Tel.:',
    'Месячная оплата:': 'Oylik to‘lov:',
    '✅ Создать ученика': '✅ O‘quvchini yaratish',
    'добавлен': 'qo‘shildi',
    '👤 Открыть карточку': '👤 Kartani ochish',
    '➕ Ещё ученик': '➕ Yana o‘quvchi',
    'Введите текст от 1 до ': '1 dan ',
    ' символов в одну строку.': ' belgigacha matnni bir qatorda kiriting.',
    'Введите телефон цифрами, например +998901234567.': 'Telefonni raqamlar bilan kiriting, masalan +998901234567.',
    'В телефоне должно быть от 7 до 15 цифр.': 'Telefon raqamida 7 dan 15 tagacha raqam bo‘lishi kerak.',
    'Введите сумму от 0 с максимум 2 знаками после точки.': '0 yoki undan katta summani kiriting (nuqtadan keyin ko‘pi bilan 2 raqam).',
    'Поиск ученика': 'O‘quvchini qidirish',
    'Введите имя, часть имени или телефон.': 'Ismni, ismning bir qismini yoki telefon raqamini kiriting.',
    'Поиск:': 'Qidiruv:',
    'По запросу «': '«',
    '» никого не найдено.': '» bo‘yicha hech kim topilmadi.',
    'История оплат ·': 'To‘lovlar tarixi ·',
    'Оплат пока нет.': 'Hozircha to‘lovlar yo‘q.',
    'На этой странице оплат нет.': 'Bu sahifada to‘lovlar yo‘q.',
    'Как пользоваться': 'Foydalanish tartibi',
    'Для работы с оплатами откройте личный чат с ботом.': 'To‘lovlar bilan ishlash uchun bot bilan shaxsiy chatni oching.',
    'Ваш Telegram ID:': 'Telegram ID raqamingiz:',
    'Формат: /pay <id ученика> <сумма>. Или нажмите «Внести оплату» в меню.': 'Format: /pay <o‘quvchi IDsi> <summa>. Yoki menyuda «To‘lov qilish»ni bosing.',
    'API-ключ бота настроен неверно.': 'Bot API kaliti noto‘g‘ri sozlangan.',
    'Ученик не найден. Выберите ученика заново.': 'O‘quvchi topilmadi. Qaytadan tanlang.',
    'Проверьте сумму оплаты.': 'To‘lov summasini tekshiring.',
    'Конфликт данных. Проверьте оплаты через API.': 'Ma’lumotlar ziddiyati. To‘lovlarni API orqali tekshiring.',
    'API отклонил запрос.': 'API so‘rovni rad etdi.',
    'Выберите кнопку или введите сумму': 'Tugmani tanlang yoki summani kiriting',
}


def localize_screen(screen, language):
    if language != 'uz' or screen is None:
        return screen
    for source, target in sorted(UZ_TRANSLATIONS.items(), key=lambda pair: len(pair[0]), reverse=True):
        screen.text = screen.text.replace(source, target)
        for row in screen.buttons:
            for item in row:
                item['text'] = item['text'].replace(source, target)
    screen.placeholder = 'Tugmani tanlang yoki summani kiriting'
    return screen


def amount_screen(state, error=''):
    s = state['student']
    text = (f"Внести оплату · {s['name']}\nГруппа: {s['group']}\n"
            f"Тариф: {s['monthly_fee']}/мес.\n\nВведите сумму, например: 150000.50")
    return Screen((error + '\n\n' if error else '') + text,
                  [back(state['origin']), [button('🏠 Главное меню', 'menu')]])


def receipt(row):
    return Screen(f"✅ Оплата #{row['id']} сохранена\nУченик #{row['student_id']}\n"
                  f"Сумма: {row['amount']}\nДата: {row['date']}", [
        [button('💳 Ещё оплата', 'pay:0')], back(),
    ])


async def api_request(api, method, path, **kwargs):
    response = await api.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def parse_amount(text):
    try:
        if len(text) > 40:
            raise ValueError
        amount = Decimal(text.strip().replace(',', '.'))
        if (not amount.is_finite() or amount <= 0 or amount > Decimal('999999999999.99')
                or amount.as_tuple().exponent < -2):
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise ValueError('Введите положительную сумму с максимум 2 знаками после точки.') from None


async def save_payment(api, payload, key):
    return await api_request(api, 'POST', '/payments', json=payload,
                             headers={'Idempotency-Key': key})


def page_number(raw):
    if not raw.isdigit() or len(raw) > 6:
        raise ValueError
    return int(raw)


async def buttonHandler(data, user_id, api, timezone, bot_id, sessions):
    """Dispatch callback_data after authorization in process_update."""
    if data == 'menu':
        sessions.pop(user_id, None)
        return home()
    extra = await student_buttons(data, user_id, api, bot_id, sessions)
    if extra is not None:
        return extra
    if data == 'help':
        clear_flow(sessions, user_id)
        return Screen('Как пользоваться\n\n'
                      '💳 Внести оплату: выберите ученика, введите сумму и подтвердите.\n'
                      '📋 Должники: долг за текущий месяц.\n'
                      '👥 Ученики: карточки с историей оплат.\n'
                      '➕ Добавить ученика: заполните данные по шагам.\n'
                      '🔎 Найти ученика: поиск по имени или телефону.\n\n'
                      'Кнопка «Назад» возвращает на предыдущий шаг.\n'
                      'Вернуться в меню можно командой /start.', [back()])
    if data.startswith(('pay:', 'students:')):
        kind, raw_page = data.split(':')
        page = page_number(raw_page)
        rows = await api_request(api, 'GET', '/students',
                                 params={'offset': page * PAGE_SIZE, 'limit': PAGE_SIZE + 1})
        clear_flow(sessions, user_id)
        title = 'Выберите ученика для оплаты' if kind == 'pay' else 'Ученики'
        buttons = [[button(f"{s['name'][:45]} · {s['group'][:20]} · #{s['id']}",
                           f"{'pick' if kind == 'pay' else 'card'}:{page}:{s['id']}")]
                   for s in rows[:PAGE_SIZE]]
        nav = []
        if page > 0:
            nav.append(button('← Предыдущие', f'{kind}:{page - 1}'))
        if len(rows) > PAGE_SIZE:
            nav.append(button('Следующие →', f'{kind}:{page + 1}'))
        if nav:
            buttons.append(nav)
        buttons.append(back())
        return Screen(f'{title} · страница {page + 1}' if rows else 'Учеников пока нет. Нажмите «Добавить ученика» в главном меню.', buttons)
    if data.startswith('debt:'):
        page = page_number(data.split(':')[1])
        month = datetime.now(ZoneInfo(timezone)).strftime('%Y-%m')
        rows = await api_request(api, 'GET', '/debtors', params={'month': month})
        clear_flow(sessions, user_id)
        if not rows:
            return Screen(f'За {month} должников нет.', [back()])
        page = min(page, (len(rows) - 1) // PAGE_SIZE)
        lines = [f'Должники за {month} · страница {page + 1}']
        buttons = []
        for s in rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            lines.append(f"{s['name']} · {s['group']}\nТел.: {s['phone']}\nДолг: {s['debt']}")
            buttons.append([button(f"💳 {s['name'][:45]} · #{s['student_id']}", f"debtpick:{page}:{s['student_id']}")])
        total = sum((Decimal(s['debt']) for s in rows), Decimal('0'))
        lines.append(f'Общий долг: {total:.2f}')
        nav = []
        if page:
            nav.append(button('← Предыдущие', f'debt:{page - 1}'))
        if (page + 1) * PAGE_SIZE < len(rows):
            nav.append(button('Следующие →', f'debt:{page + 1}'))
        if nav:
            buttons.append(nav)
        buttons.append(back())
        return Screen('\n\n'.join(lines), buttons)
    if data.startswith(('pick:', 'card:', 'debtpick:', 'cardpay:', 'searchcard:', 'searchpay:')):
        kind, raw_page, raw_id = data.split(':')
        page, sid = page_number(raw_page), int(raw_id)
        student = await api_request(api, 'GET', f'/students/{sid}')
        if kind in ('card', 'searchcard'):
            clear_flow(sessions, user_id)
            return Screen(f"{student['name']}\nТел.: {student['phone']}\n"
                          f"Группа: {student['group']}\nТариф: {student['monthly_fee']}/мес.", [
                [button('💳 Внести оплату', f"{'searchpay' if kind == 'searchcard' else 'cardpay'}:{page}:{sid}")],
                [button('📜 История оплат', f"{'searchhistory' if kind == 'searchcard' else 'history'}:{page}:{sid}:0")],
                back(f"{'searchresults' if kind == 'searchcard' else 'students'}:{page}"),
            ])
        state = {'step': 'amount', 'student': student, 'page': page,
                 'origin': (f'debt:{page}' if kind == 'debtpick' else
                            f'card:{page}:{sid}' if kind == 'cardpay' else
                            f'searchcard:{page}:{sid}' if kind == 'searchpay' else f'pay:{page}')}
        set_flow(sessions, user_id, state)
        return amount_screen(state)
    if data.startswith('amount:'):
        token = data.split(':')[1]
        state = sessions.get(user_id, {})
        if state.get('step') == 'confirm' and state.get('token') == token:
            state = {k: v for k, v in state.items() if k not in ('payload', 'token')}
            state['step'] = 'amount'
            set_flow(sessions, user_id, state)
            return amount_screen(state)
    if data.startswith('confirm:'):
        token = data.split(':')[1]
        state = sessions.get(user_id, {})
        if state.get('token') == token:
            if state.get('step') == 'done':
                return receipt(state['receipt'])
            if state.get('step') == 'confirm':
                # Use the amount-message ID, not callback ID: double taps share one key.
                row = await save_payment(api, state['payload'], f'telegram:{bot_id}:guided:{token}')
                set_flow(sessions, user_id, {'step': 'done', 'token': token, 'receipt': row})
                return receipt(row)
    return Screen('Этот экран устарел. Выберите действие заново.', [back()])


async def _process_update(update, api, admins, timezone, bot_id, sessions=None):
    if sessions is None:
        sessions = {}
    callback = update.get('callback_query')
    message = callback.get('message') if callback else update.get('message')
    if not message:
        return None
    user_id = (callback or message).get('from', {}).get('id')
    text = message.get('text', '') if not callback else ''
    parts = text.split()
    command = parts[0].split('@')[0].lower() if parts else ''
    if message.get('chat', {}).get('type') != 'private':
        return Screen('Для работы с оплатами откройте личный чат с ботом.', [])
    if command == '/id':
        return Screen(f'Ваш Telegram ID: {user_id}', [back()] if user_id in admins else [])
    if callback:
        try:
            return await buttonHandler(callback.get('data', ''), user_id, api, timezone, bot_id, sessions)
        except (ValueError, IndexError):
            return Screen('Этот экран устарел. Выберите действие заново.', [back()])
    if command in ('/start', '/help', '/cancel'):
        sessions.pop(user_id, None)
        return home()
    if command in ('/debt', '/students'):
        return await buttonHandler('debt:0' if command == '/debt' else 'students:0',
                                   user_id, api, timezone, bot_id, sessions)
    if command == '/pay':
        if len(parts) == 1:
            return await buttonHandler('pay:0', user_id, api, timezone, bot_id, sessions)
        try:
            if len(parts) != 3:
                raise ValueError
            sid = int(parts[1])
            amount = parse_amount(parts[2])
            if sid <= 0:
                raise ValueError
        except ValueError:
            return Screen('Формат: /pay <id ученика> <сумма>. Или нажмите «Внести оплату» в меню.', [back()])
        date = datetime.fromtimestamp(message['date'], ZoneInfo(timezone)).date().isoformat()
        row = await save_payment(api, {'student_id': sid, 'amount': str(amount), 'date': date},
                                 f'telegram:{bot_id}:{update["update_id"]}')
        clear_flow(sessions, user_id)
        return receipt(row)
    state = sessions.get(user_id, {})
    if not command.startswith('/'):
        extra = await handle_student_text(text, update['update_id'], user_id, sessions)
        if extra is not None:
            return extra
        if state.get('step') == 'search_input':
            query = text.strip()
            if not query or len(query) > 150:
                screen = search_prompt()
                screen.text = 'Введите от 1 до 150 символов.\n\n' + screen.text
                return screen
            set_flow(sessions, user_id, {'step': 'search_input', 'search_query': query})
            return await buttonHandler('searchresults:0', user_id, api, timezone, bot_id, sessions)
    if state.get('step') == 'amount' and not command.startswith('/'):
        try:
            amount = parse_amount(text)
        except ValueError as exc:
            return amount_screen(state, str(exc))
        payload = {'student_id': state['student']['id'], 'amount': str(amount),
                   'date': datetime.fromtimestamp(message['date'], ZoneInfo(timezone)).date().isoformat()}
        token = str(update['update_id'])
        set_flow(sessions, user_id, {**state, 'step': 'confirm', 'payload': payload, 'token': token})
        return Screen(f"Проверьте оплату\n\nУченик: {state['student']['name']}\n"
                      f"Группа: {state['student']['group']}\nСумма: {amount:.2f}\nДата: {payload['date']}", [
            [button('✅ Подтвердить оплату', f'confirm:{token}')],
            back(f'amount:{token}'), [button('🏠 Главное меню', 'menu')],
        ])
    if state.get('step') == 'confirm':
        return Screen('Подтвердите оплату кнопкой или вернитесь к изменению суммы.', [
            [button('✅ Подтвердить оплату', f"confirm:{state['token']}")],
            back(f"amount:{state['token']}"),
        ])
    clear_flow(sessions, user_id)
    return home()


def keyboard_actions(screen):
    return {b['text']: b['callback_data'] for row in screen.buttons for b in row}


async def process_update(update, api, admins, timezone, bot_id, sessions=None, keyboards=None, languages=None):
    if sessions is None:
        sessions = {}
    if keyboards is None:
        keyboards = {}
    if languages is None:
        languages = {}
    message = update.get('message', {})
    callback = update.get('callback_query', {})
    user_id = (callback or message).get('from', {}).get('id')
    text = message.get('text', '')
    language = languages.get(user_id, 'ru')
    actions = {**keyboard_actions(localize_screen(home(), language)),
               **keyboards.get(user_id, {})}
    action = actions.get(text)
    if action is not None and not callback:
        # Reuse the button dispatcher; actor/private-chat checks remain in one place.
        update = {**update, 'callback_query': {'from': message.get('from', {}),
                                              'message': message, 'data': action}}
    data = (update.get('callback_query') or {}).get('data', '')
    if data == 'language':
        screen = Screen('Выберите язык / Tilni tanlang', [
            [button('🇷🇺 Русский', 'lang:ru')],
            [button('🇺🇿 O‘zbekcha', 'lang:uz')],
            back(),
        ])
    elif data in ('lang:ru', 'lang:uz'):
        languages[user_id] = data.split(':')[1]
        language = languages[user_id]
        screen = home()
    else:
        screen = await _process_update(update, api, admins, timezone, bot_id, sessions)
    screen = localize_screen(screen, language)
    if screen is not None:
        keyboards[user_id] = keyboard_actions(screen)
    return screen


async def send_text(telegram, chat_id, text, reply_markup=None):
    for pos in range(0, len(text), 1800):
        payload = {'chat_id': chat_id, 'text': text[pos:pos + 1800]}
        if reply_markup and pos + 1800 >= len(text):
            payload['reply_markup'] = reply_markup
        response = await telegram.post('sendMessage', json=payload)
        response.raise_for_status()
        if not response.json().get('ok'):
            raise RuntimeError('Telegram rejected sendMessage')


async def acknowledge_callback(telegram, callback):
    response = await telegram.post('answerCallbackQuery', json={'callback_query_id': callback['id']})
    # An expired callback must not block all newer updates.
    if response.status_code == 400:
        return
    response.raise_for_status()
    if not response.json().get('ok'):
        raise RuntimeError('Telegram rejected answerCallbackQuery')


async def deliver_screen(telegram, update, screen):
    callback = update.get('callback_query')
    message = callback['message'] if callback else update['message']
    # ReplyKeyboardMarkup is sent with a new message, not editMessageText.
    await send_text(telegram, message['chat']['id'], screen.text, screen.markup)


async def run():
    settings = get_settings()
    if not settings.bot_token:
        raise ValueError('Set BOT_TOKEN in .env')
    admins = {int(value.strip()) for value in settings.admin_telegram_ids.split(',') if value.strip()}
    ZoneInfo(settings.timezone)
    sessions = {}
    keyboards = {}
    languages = {}
    async with (httpx.AsyncClient(base_url=f'https://api.telegram.org/bot{settings.bot_token}/', timeout=45) as telegram,
                httpx.AsyncClient(base_url=settings.api_base_url, headers={'X-API-Key': settings.api_key}, timeout=15) as api):
        identity = await telegram.get('getMe')
        identity.raise_for_status()
        if not identity.json().get('ok'):
            raise RuntimeError('Telegram rejected bot token')
        bot_id = identity.json()['result']['id']
        offset = 0
        log.info('Bot started')
        while True:
            try:
                response = await telegram.post('getUpdates', json={
                    'offset': offset, 'timeout': 30, 'allowed_updates': ['message', 'callback_query']})
                response.raise_for_status()
                result = response.json()
                if not result.get('ok'):
                    raise RuntimeError('Telegram rejected getUpdates')
                for update in result['result']:
                    if update.get('callback_query'):
                        await acknowledge_callback(telegram, update['callback_query'])
                    try:
                        screen = await process_update(update, api, admins, settings.timezone, bot_id,
                                                      sessions, keyboards, languages)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code >= 500:
                            raise
                        message = {404: 'Ученик не найден. Выберите ученика заново.',
                                   401: 'API-ключ бота настроен неверно.',
                                   422: 'Проверьте сумму оплаты.',
                                   409: 'Конфликт данных. Проверьте оплаты через API.'}.get(
                                       exc.response.status_code, 'API отклонил запрос.')
                        user_id = (update.get('callback_query') or update.get('message', {})).get('from', {}).get('id')
                        screen = localize_screen(Screen(message, [back()]), languages.get(user_id, 'ru'))
                    if screen:
                        actor = update.get('callback_query') or update.get('message', {})
                        keyboards[actor.get('from', {}).get('id')] = keyboard_actions(screen)
                        await deliver_screen(telegram, update, screen)
                    offset = update['update_id'] + 1
            except (httpx.HTTPError, RuntimeError):
                log.warning('Telegram/API unavailable; retrying in 5 seconds')
                await asyncio.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except httpx.HTTPError:
        log.error('Cannot connect to Telegram. Check BOT_TOKEN and network access.')
        raise SystemExit(1) from None
