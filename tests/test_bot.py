import asyncio
import httpx
from app.bot import process_update


def update(text, uid=7, kind='private'):
    return {'update_id': 42, 'message': {'text': text, 'date': 1793059200,
            'from': {'id': uid}, 'chat': {'id': uid, 'type': kind}}}


def invoke(data, handler):
    async def run():
        async with httpx.AsyncClient(base_url='http://test', transport=httpx.MockTransport(handler)) as api:
            return (await process_update(data, api, {7}, 'Asia/Tashkent', 123)).text
    return asyncio.run(run())


def test_access_and_group_privacy():
    def no_request(request):
        raise AssertionError('Unauthorized request reached API')
    assert 'Доступ закрыт' in invoke(update('/debt', uid=8), no_request)
    assert '8' in invoke(update('/id', uid=8), no_request)
    assert 'личный чат' in invoke(update('/debt', kind='group'), no_request)


def test_payment_and_idempotency_header():
    def handler(request):
        assert request.url.path == '/payments'
        assert request.headers['Idempotency-Key'] == 'telegram:123:42'
        import json
        body = json.loads(request.content)
        assert body['amount'] == '150.50'
        assert body['student_id'] == 2
        assert body['date'] == '2026-10-27'
        return httpx.Response(201, json={'id': 9, **body})
    assert 'Оплата #9' in invoke(update('/pay 2 150,50'), handler)


def test_invalid_payment_and_empty_debtors():
    def no_request(request):
        raise AssertionError('Invalid input reached API')
    for command in ['/pay 1 NaN', '/pay 1 -20', '/pay 1 0.001', '/pay x 10']:
        assert 'Формат:' in invoke(update(command), no_request)
    assert 'должников нет' in invoke(update('/debt'), lambda r: httpx.Response(200, json=[]))
