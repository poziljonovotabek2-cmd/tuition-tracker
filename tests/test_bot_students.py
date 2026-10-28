import asyncio
import json
import os
import httpx
import pytest
from app.bot import process_update, PAGE_SIZE
from app.main import app

STUDENT = {'id': 9, 'name': 'Азиза', 'phone': '+998901234567', 'group': 'Python', 'monthly_fee': '500000.00'}


def message(text, uid=7, update_id=1):
    return {'update_id': update_id, 'message': {'message_id': update_id, 'text': text,
            'date': 1793059200, 'from': {'id': uid}, 'chat': {'id': uid, 'type': 'private'}}}


def actions(screen):
    return [b['callback_data'] for row in screen.buttons for b in row]


def scenario(flow, handler):
    async def run():
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handler)) as api:
            state, keyboards, counter = {}, {}, 0
            async def send(text, uid=7):
                nonlocal counter
                counter += 1
                return await process_update(message(text, uid, counter), api, {7}, 'Asia/Tashkent', 123, state, keyboards)
            await flow(send, state)
    asyncio.run(run())


def no_request(request):
    raise AssertionError('Unexpected API request')


async def fill(send, fee='500000,00'):
    await send('➕ Добавить ученика')
    await send('Азиза')
    await send('+998 (90) 123-45-67')
    await send('Python')
    return await send(fee)


def test_creation_confirm_back_validation_and_cancel():
    async def flow(send, state):
        screen = await send('➕ Добавить ученика')
        assert 'шаг 1/4' in screen.text
        for text, expected in [('   ', 'от 1'), ('x' * 151, 'от 1')]:
            assert expected in (await send(text)).text
        await send('Азиза')
        assert 'телефон' in (await send('abc')).text.lower()
        assert 'цифр' in (await send('+12')).text
        await send('+998901234567')
        await send('Python')
        for text in ['-1', 'NaN', '0.001', '1000000000000']:
            assert 'от 0' in (await send(text)).text
        confirm = await send('0')
        assert 'Месячная оплата: 0.00' in confirm.text
        assert state[7]['step'] == 'create_confirm'
        fee = await send('⬅️ Назад')
        assert 'шаг 4/4' in fee.text
        group = await send('⬅️ Назад')
        assert 'шаг 3/4' in group.text
        await send('Other')
        confirm = await send('100')
        assert 'Группа: Other' in confirm.text
        await send('🏠 Главное меню')
        assert state == {}
    scenario(flow, no_request)


def test_creation_only_saves_on_confirm_and_normalizes_phone():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'POST' and request.url.path == '/students'
        body = json.loads(request.content)
        assert body == {k: v for k, v in STUDENT.items() if k != 'id'}
        assert request.headers['Idempotency-Key'].startswith('telegram:123:student:')
        return httpx.Response(201, json=STUDENT)
    async def flow(send, state):
        screen = await fill(send)
        assert not calls
        assert '✅ Создать ученика' in str(screen.markup)
        result = await send('✅ Создать ученика')
        assert 'Ученик #9 добавлен' in result.text
        assert len(calls) == 1
        # The old keyboard caption cannot create another student.
        await send('✅ Создать ученика')
        assert len(calls) == 1
    scenario(flow, handler)


def test_student_creation_retry_reuses_request_key():
    calls = []
    def handler(request):
        calls.append(request.headers['Idempotency-Key'])
        if len(calls) == 1:
            raise httpx.ReadTimeout('Response lost', request=request)
        return httpx.Response(201, json=STUDENT)
    async def flow(send, state):
        await fill(send)
        with pytest.raises(httpx.ReadTimeout):
            await send('✅ Создать ученика')
        assert state[7]['step'] == 'create_confirm'
        result = await send('✅ Создать ученика')
        assert 'добавлен' in result.text
        assert len(calls) == 2 and calls[0] == calls[1]
    scenario(flow, handler)


def test_search_history_and_back_keep_search_context():
    def handler(request):
        if request.url.path == '/students':
            assert request.url.params['q'] == 'Азиза'
            return httpx.Response(200, json=[STUDENT])
        if request.url.path == '/students/9':
            return httpx.Response(200, json=STUDENT)
        if request.url.path == '/students/9/payments':
            offset = int(request.url.params['offset'])
            rows = [{'id': i, 'student_id': 9, 'date': '2026-10-27', 'amount': '100.00'} for i in range(offset + 1, 8)]
            return httpx.Response(200, json=rows[:int(request.url.params['limit'])])
        raise AssertionError(str(request.url))
    async def flow(send, state):
        prompt = await send('🔎 Найти ученика')
        assert 'телефон' in prompt.text
        results = await send('Азиза')
        label = results.buttons[0][0]['text']
        card = await send(label)
        assert 'История оплат' in str(card.markup)
        history = await send('📜 История оплат')
        assert 'оплата #1' in history.text and 'оплата #6' not in history.text
        assert any(a.endswith(':1') for a in actions(history))
        second = await send('Следующие →')
        assert 'оплата #6' in second.text
        assert 'оплата #1' not in second.text
        card = await send('⬅️ Назад')
        assert STUDENT['phone'] in card.text
        results = await send('⬅️ Назад')
        assert 'Поиск: Азиза' in results.text
        await send(label)
        amount = await send('💳 Внести оплату')
        assert 'Введите сумму' in amount.text
        card = await send('⬅️ Назад')
        assert STUDENT['phone'] in card.text
    scenario(flow, handler)


def test_empty_search_empty_history_and_search_paging():
    def handler(request):
        if request.url.path == '/students':
            q = request.url.params['q']
            if q == 'missing':
                return httpx.Response(200, json=[])
            offset = int(request.url.params['offset'])
            return httpx.Response(200, json=[{**STUDENT, 'id': i} for i in range(offset + 1, min(offset + PAGE_SIZE + 2, 8))])
        if request.url.path == '/students/9':
            return httpx.Response(200, json=STUDENT)
        if request.url.path == '/students/9/payments':
            return httpx.Response(200, json=[])
        raise AssertionError(str(request.url))
    async def flow(send, state):
        await send('🔎 Найти ученика')
        assert 'от 1 до 150' in (await send(' ')).text
        assert 'от 1 до 150' in (await send('x' * 151)).text
        empty = await send('missing')
        assert 'никого не найдено' in empty.text
        await send('⬅️ Назад')
        first = await send('Aziza')
        assert 'searchresults:1' in actions(first)
        second = await send('Следующие →')
        assert 'searchresults:0' in actions(second)
    scenario(flow, handler)


def test_public_user_can_list_students():
    def handler(request):
        assert request.url.path == '/students'
        return httpx.Response(200, json=[STUDENT])
    async def flow(send, state):
        result = await send('/students', uid=8)
        assert 'Азиза' in result.text
    scenario(flow, handler)


def test_bot_create_search_pay_history_against_real_api(client):
    # Full flow through FastAPI and its test database, without Telegram network calls.
    async def run():
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.ASGITransport(app=app),
                                     headers={'X-API-Key': os.environ['API_KEY']}) as api:
            state, keyboards, counter = {}, {}, 100
            async def send(text):
                nonlocal counter
                counter += 1
                return await process_update(message(text, update_id=counter), api, {7}, 'Asia/Tashkent', 123, state, keyboards)
            await fill(send)
            result = await send('✅ Создать ученика')
            assert 'добавлен' in result.text
            await send('👤 Открыть карточку')
            assert 'Оплат пока нет' in (await send('📜 История оплат')).text
            await send('🏠 Главное меню')
            await send('🔎 Найти ученика')
            results = await send('901234567')
            await send(results.buttons[0][0]['text'])
            await send('💳 Внести оплату')
            await send('150,50')
            assert 'сохранена' in (await send('✅ Подтвердить оплату')).text
            await send('🏠 Главное меню')
            await send('👥 Ученики')
            students = await send('👥 Ученики')
            await send(students.buttons[0][0]['text'])
            history = await send('📜 История оплат')
            assert '150.50' in history.text
    asyncio.run(run())
