"""Student creation, search and payment history for the Telegram UI.

Screen helpers are imported lazily to avoid a module-level cycle with bot.py.
"""
import re
from decimal import Decimal, InvalidOperation

FIELDS = ('name', 'phone', 'group', 'monthly_fee')
PROMPTS = (
    'Введите имя ученика (до 150 символов).',
    'Введите телефон, например +998901234567.',
    'Введите группу ученика (до 100 символов).',
    'Введите месячную оплату, например 500000.00. Для бесплатного обучения — 0.',
)


def set_flow(sessions, user_id, state):
    query = sessions.get(user_id, {}).get('search_query')
    sessions[user_id] = {**({'search_query': query} if query else {}), **state}


def clear_flow(sessions, user_id):
    query = sessions.get(user_id, {}).get('search_query')
    if query:
        sessions[user_id] = {'search_query': query}
    else:
        sessions.pop(user_id, None)


def creation_prompt(state, error=''):
    from app.bot import Screen, button, back
    index = state['index']
    text = f'Новый ученик · шаг {index + 1}/4\n\n{PROMPTS[index]}'
    if FIELDS[index] in state['data']:
        text += f"\nПредыдущее значение: {state['data'][FIELDS[index]]}"
    if error:
        text = error + '\n\n' + text
    return Screen(text, [back('createback' if index else 'menu'), [button('🏠 Главное меню', 'menu')]])


def creation_confirmation(state):
    from app.bot import Screen, button, back
    d = state['data']
    return Screen(f"Проверьте нового ученика\n\nИмя: {d['name']}\nТел.: {d['phone']}\n"
                  f"Группа: {d['group']}\nМесячная оплата: {d['monthly_fee']}", [
        [button('✅ Создать ученика', f"createconfirm:{state['token']}")],
        back('createback'), [button('🏠 Главное меню', 'menu')],
    ])


def created_screen(student):
    from app.bot import Screen, button, back
    return Screen(f"✅ Ученик #{student['id']} добавлен\n{student['name']}\nГруппа: {student['group']}", [
        [button('👤 Открыть карточку', f"card:0:{student['id']}")],
        [button('➕ Ещё ученик', 'create')], back(),
    ])


def search_prompt():
    from app.bot import Screen, back
    return Screen('Поиск ученика\n\nВведите имя, часть имени или телефон.', [back()])


def validate_field(index, text):
    value = text.strip()
    if index in (0, 2):
        max_length = 150 if index == 0 else 100
        if not value or len(value) > max_length or any(ord(c) < 32 for c in value):
            raise ValueError(f'Введите текст от 1 до {max_length} символов в одну строку.')
        return value
    if index == 1:
        if len(value) > 40 or not re.fullmatch(r'\+?[0-9()\- ]+', value):
            raise ValueError('Введите телефон цифрами, например +998901234567.')
        digits = re.sub(r'[^0-9]', '', value)
        if not 7 <= len(digits) <= 15:
            raise ValueError('В телефоне должно быть от 7 до 15 цифр.')
        return ('+' if value.startswith('+') else '') + digits
    try:
        if len(value) > 40:
            raise ValueError
        fee = Decimal(value.replace(',', '.'))
        if (not fee.is_finite() or fee < 0 or fee > Decimal('999999999999.99')
                or fee.as_tuple().exponent < -2):
            raise ValueError
    except (ValueError, InvalidOperation):
        raise ValueError('Введите сумму от 0 с максимум 2 знаками после точки.') from None
    return format(fee, '.2f')


async def handle_student_text(text, update_id, user_id, sessions):
    state = sessions.get(user_id, {})
    if state.get('step') == 'create':
        try:
            value = validate_field(state['index'], text)
        except ValueError as exc:
            return creation_prompt(state, str(exc))
        state = {**state, 'data': {**state['data'], FIELDS[state['index']]: value}}
        if state['index'] < 3:
            state['index'] += 1
            set_flow(sessions, user_id, state)
            return creation_prompt(state)
        state.update(step='create_confirm', token=str(update_id))
        set_flow(sessions, user_id, state)
        return creation_confirmation(state)
    if state.get('step') == 'create_confirm':
        return creation_confirmation(state)
    return None


async def student_buttons(data, user_id, api, bot_id, sessions):
    from app.bot import Screen, button, back, api_request, page_number, PAGE_SIZE
    if data == 'create':
        state = {'step': 'create', 'index': 0, 'data': {}}
        set_flow(sessions, user_id, state)
        return creation_prompt(state)
    if data == 'createback':
        state = sessions.get(user_id, {})
        if state.get('step') not in ('create', 'create_confirm'):
            return Screen('Этот экран устарел. Начните создание заново.', [back()])
        index = 3 if state['step'] == 'create_confirm' else max(0, state['index'] - 1)
        state = {**state, 'step': 'create', 'index': index}
        state.pop('token', None)
        set_flow(sessions, user_id, state)
        return creation_prompt(state)
    if data.startswith('createconfirm:'):
        token = data.split(':')[1]
        state = sessions.get(user_id, {})
        if state.get('token') == token and state.get('step') == 'created':
            return created_screen(state['student'])
        if state.get('token') == token and state.get('step') == 'create_confirm':
            student = await api_request(api, 'POST', '/students', json=state['data'],
                                        headers={'Idempotency-Key': f'telegram:{bot_id}:student:{token}'})
            set_flow(sessions, user_id, {'step': 'created', 'student': student, 'token': token})
            return created_screen(student)
        return Screen('Этот экран устарел. Начните создание заново.', [back()])
    if data == 'search':
        set_flow(sessions, user_id, {'step': 'search_input'})
        return search_prompt()
    if data.startswith('searchresults:'):
        page = page_number(data.split(':')[1])
        query = sessions.get(user_id, {}).get('search_query')
        if not query:
            set_flow(sessions, user_id, {'step': 'search_input'})
            return search_prompt()
        rows = await api_request(api, 'GET', '/students',
                                 params={'q': query, 'offset': page * PAGE_SIZE, 'limit': PAGE_SIZE + 1})
        set_flow(sessions, user_id, {'step': 'search_results', 'search_query': query})
        buttons = [[button(f"{s['name'][:45]} · {s['group'][:20]} · #{s['id']}",
                           f"searchcard:{page}:{s['id']}")] for s in rows[:PAGE_SIZE]]
        nav = []
        if page:
            nav.append(button('← Предыдущие', f'searchresults:{page - 1}'))
        if len(rows) > PAGE_SIZE:
            nav.append(button('Следующие →', f'searchresults:{page + 1}'))
        if nav:
            buttons.append(nav)
        buttons += [back('search'), [button('🏠 Главное меню', 'menu')]]
        return Screen(f'Поиск: {query}\nСтраница {page + 1}' if rows else f'По запросу «{query}» никого не найдено.', buttons)
    if data.startswith(('history:', 'searchhistory:')):
        kind, raw_origin_page, raw_id, raw_page = data.split(':')
        origin_page, sid, page = page_number(raw_origin_page), int(raw_id), page_number(raw_page)
        student = await api_request(api, 'GET', f'/students/{sid}')
        rows = await api_request(api, 'GET', f'/students/{sid}/payments',
                                 params={'offset': page * PAGE_SIZE, 'limit': PAGE_SIZE + 1})
        clear_flow(sessions, user_id)
        lines = [f"История оплат · {student['name']}\nСтраница {page + 1}"]
        for payment in rows[:PAGE_SIZE]:
            lines.append(f"{payment['date']} · {payment['amount']} · оплата #{payment['id']}")
        if not rows:
            lines.append('Оплат пока нет.' if page == 0 else 'На этой странице оплат нет.')
        buttons, nav = [], []
        prefix = f'{kind}:{origin_page}:{sid}'
        if page:
            nav.append(button('← Предыдущие', f'{prefix}:{page - 1}'))
        if len(rows) > PAGE_SIZE:
            nav.append(button('Следующие →', f'{prefix}:{page + 1}'))
        if nav:
            buttons.append(nav)
        card = 'searchcard' if kind == 'searchhistory' else 'card'
        buttons += [back(f'{card}:{origin_page}:{sid}'), [button('🏠 Главное меню', 'menu')]]
        return Screen('\n\n'.join(lines), buttons)
    return None
