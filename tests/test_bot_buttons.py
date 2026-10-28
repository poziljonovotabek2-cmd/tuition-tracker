import asyncio
import json
import httpx
import pytest
from app.bot import process_update, deliver_screen, acknowledge_callback, Screen, back

STUDENT = {'id': 2, 'name': 'Азиза', 'group': 'Python', 'phone': '+99890', 'monthly_fee': '500000.00'}


def message(text, update_id=51, uid=7):
    return {'update_id': update_id, 'message': {'message_id': update_id, 'text': text,
            'date': 1793059200, 'from': {'id': uid}, 'chat': {'id': uid, 'type': 'private'}}}


def callback(data, uid=7, update_id=60):
    return {'update_id': update_id, 'callback_query': {'id': str(update_id), 'data': data,
            'from': {'id': uid}, 'message': {'message_id': 10, 'date': 1793059200,
            'from': {'id': 123}, 'chat': {'id': uid, 'type': 'private'}}}}


def actions(screen):
    return [b['callback_data'] for row in screen.buttons for b in row]


def run_flow(flow, handler):
    async def run():
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handler)) as api:
            sessions = {}
            async def dispatch(update):
                return await process_update(update, api, {7}, 'Asia/Tashkent', 123, sessions)
            await flow(dispatch, sessions)
    asyncio.run(run())


def student_handler(request):
    if request.url.path == '/students/2':
        return httpx.Response(200, json=STUDENT)
    if request.url.path == '/students':
        return httpx.Response(200, json=[STUDENT])
    raise AssertionError(str(request.url))


def test_menu_students_card_and_back():
    async def flow(dispatch, sessions):
        menu = await dispatch(message('/start'))
        assert {'pay:0', 'debt:0', 'students:0', 'help'} <= set(actions(menu))
        students = await dispatch(callback('students:0'))
        assert 'card:0:2' in actions(students)
        assert 'menu' in actions(students)
        card = await dispatch(callback('card:0:2'))
        assert STUDENT['phone'] in card.text
        assert 'students:0' in actions(card)
        assert 'cardpay:0:2' in actions(card)
        amount = await dispatch(callback('cardpay:0:2'))
        assert 'card:0:2' in actions(amount)
        help_screen = await dispatch(callback('help'))
        assert 'menu' in actions(help_screen)
    run_flow(flow, student_handler)


def test_guided_payment_confirm_and_double_tap():
    calls = []
    def handler(request):
        if request.url.path == '/payments':
            calls.append(request)
            assert request.headers['Idempotency-Key'] == 'telegram:123:guided:51'
            body = json.loads(request.content)
            assert body == {'student_id': 2, 'amount': '150.50', 'date': '2026-10-27'}
            return httpx.Response(201, json={'id': 9, **body})
        return student_handler(request)
    async def flow(dispatch, sessions):
        pick = await dispatch(callback('pick:0:2'))
        assert 'Введите сумму' in pick.text and 'pay:0' in actions(pick)
        confirm = await dispatch(message('150,50'))
        assert 'confirm:51' in actions(confirm) and 'amount:51' in actions(confirm)
        assert not calls  # No insert before explicit confirmation.
        result = await dispatch(callback('confirm:51'))
        assert 'Оплата #9' in result.text and 'menu' in actions(result)
        repeat = await dispatch(callback('confirm:51', update_id=61))
        assert repeat.text == result.text
        assert len(calls) == 1
    run_flow(flow, handler)


def test_back_invalidates_confirmation_and_allows_new_amount():
    async def flow(dispatch, sessions):
        await dispatch(callback('pick:0:2'))
        await dispatch(message('100'))
        amount = await dispatch(callback('amount:51'))
        assert 'Введите сумму' in amount.text
        stale = await dispatch(callback('confirm:51'))
        assert 'устарел' in stale.text
        confirm = await dispatch(message('200', update_id=52))
        assert 'confirm:52' in actions(confirm)
        assert '200.00' in confirm.text
        await dispatch(callback('menu'))
        assert sessions == {}
        assert 'устарел' in (await dispatch(callback('confirm:52'))).text
    run_flow(flow, student_handler)


@pytest.mark.parametrize('text', ['-1', 'NaN', 'hello', '0', '0.001', '1000000000000'])
def test_invalid_amount_keeps_input_and_back(text):
    async def flow(dispatch, sessions):
        await dispatch(callback('pick:0:2'))
        result = await dispatch(message(text))
        assert sessions[7]['step'] == 'amount'
        assert 'Введите положительную сумму' in result.text
        assert 'pay:0' in actions(result)
    run_flow(flow, student_handler)


def test_debtor_navigation_and_payment_back():
    def handler(request):
        if request.url.path == '/debtors':
            return httpx.Response(200, json=[{'student_id': 2, **STUDENT, 'paid': '0', 'debt': '500000.00'}])
        return student_handler(request)
    async def flow(dispatch, sessions):
        debt = await dispatch(callback('debt:0'))
        assert 'debtpick:0:2' in actions(debt) and 'menu' in actions(debt)
        amount = await dispatch(callback('debtpick:0:2'))
        assert 'debt:0' in actions(amount)
    run_flow(flow, handler)


def test_student_pagination():
    def handler(request):
        offset = int(request.url.params['offset'])
        return httpx.Response(200, json=[{**STUDENT, 'id': i} for i in range(offset + 1, min(offset + 7, 8))])
    async def flow(dispatch, sessions):
        first = await dispatch(callback('pay:0'))
        assert 'pay:1' in actions(first)
        second = await dispatch(callback('pay:1'))
        assert 'pay:0' in actions(second) and 'menu' in actions(second)
        assert 'pay:2' not in actions(second)
    run_flow(flow, handler)


def test_stale_and_unauthorized_callbacks_never_call_api():
    def handler(request):
        raise AssertionError('Unexpected API access')
    async def flow(dispatch, sessions):
        for data in ['confirm:51', 'amount:51', 'pay:-1', 'pick:broken', 'other']:
            result = await dispatch(callback(data))
            assert 'устарел' in result.text and 'menu' in actions(result)
        result = await dispatch(callback('confirm:51', uid=8))
        assert 'Доступ закрыт' in result.text
    run_flow(flow, handler)


def test_non_text_input_can_return_back():
    async def flow(dispatch, sessions):
        await dispatch(callback('pick:0:2'))
        photo = message('')
        del photo['message']['text']
        result = await dispatch(photo)
        assert 'pay:0' in actions(result)
    run_flow(flow, student_handler)


def test_callback_ack_and_screen_edit_transport():
    calls = []
    def handler(request):
        calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'ok': True, 'result': True})
    async def run():
        async with httpx.AsyncClient(base_url='http://telegram/', transport=httpx.MockTransport(handler)) as telegram:
            update = callback('help')
            await acknowledge_callback(telegram, update['callback_query'])
            await deliver_screen(telegram, update, Screen('Помощь', [back()]))
    asyncio.run(run())
    assert [path for path, _ in calls] == ['/answerCallbackQuery', '/sendMessage']
    assert calls[1][1]['reply_markup']['keyboard'][0][0]['text'] == '⬅️ Назад'
    assert calls[1][1]['reply_markup']['is_persistent'] is True
    assert calls[1][1]['reply_markup']['resize_keyboard'] is True


def test_expired_callback_is_acknowledged():
    def handler(request):
        return httpx.Response(400, json={'ok': False, 'description': 'Bad Request: message is not modified'})
    async def run():
        async with httpx.AsyncClient(base_url='http://telegram/', transport=httpx.MockTransport(handler)) as telegram:
            await acknowledge_callback(telegram, callback('menu')['callback_query'])
    asyncio.run(run())


def test_retry_payment_after_connection_failure_reuses_key():
    calls = []
    def handler(request):
        if request.url.path == '/payments':
            calls.append(request.headers['Idempotency-Key'])
            if len(calls) == 1:
                raise httpx.ReadTimeout('Response lost', request=request)
            return httpx.Response(201, json={'id': 9, **json.loads(request.content)})
        return student_handler(request)
    async def flow(dispatch, sessions):
        await dispatch(callback('pick:0:2'))
        await dispatch(message('150.50'))
        with pytest.raises(httpx.ReadTimeout):
            await dispatch(callback('confirm:51'))
        assert sessions[7]['step'] == 'confirm'
        result = await dispatch(callback('confirm:51'))
        assert 'Оплата #9' in result.text
        assert calls == ['telegram:123:guided:51'] * 2
    run_flow(flow, handler)


def test_restart_expires_pending_confirmation_without_payment():
    async def flow(dispatch, sessions):
        await dispatch(callback('pick:0:2'))
        await dispatch(message('100'))
        sessions.clear()  # In-memory conversations intentionally reset on bot restart.
        result = await dispatch(callback('confirm:51'))
        assert 'устарел' in result.text and 'menu' in actions(result)
    run_flow(flow, student_handler)


def test_reply_keyboard_payment_and_back_navigation():
    async def run():
        sessions, keyboards = {}, {}
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(student_handler)) as api:
            async def send(text, update_id=51):
                return await process_update(message(text, update_id), api, {7}, 'Asia/Tashkent', 123, sessions, keyboards)
            home = await send('/start')
            assert 'keyboard' in home.markup and 'inline_keyboard' not in home.markup
            picker = await send('💳 Внести оплату')
            student_label = picker.buttons[0][0]['text']
            amount = await send(student_label)
            assert 'Введите сумму' in amount.text
            confirm = await send('150.50', update_id=52)
            assert 'Проверьте оплату' in confirm.text
            amount = await send('⬅️ Назад')
            assert sessions[7]['step'] == 'amount'
            assert 'Введите сумму' in amount.text
            picker = await send('⬅️ Назад')
            assert 'Выберите ученика' in picker.text
            home = await send('⬅️ Назад')
            assert 'Главное меню' in home.text and sessions == {}
    asyncio.run(run())


def test_student_labels_distinguish_same_names():
    def handler(request):
        return httpx.Response(200, json=[STUDENT, {**STUDENT, 'id': 3}])
    async def flow(dispatch, sessions):
        result = await dispatch(message('/students'))
        labels = [row[0]['text'] for row in result.buttons[:2]]
        assert labels[0] != labels[1]
    run_flow(flow, handler)
