from decimal import Decimal
import pytest
from app.db import engine


def add(client, name='Aziza', phone='+998 (90) 123-45-67', fee='100'):
    response = client.post('/students', json={'name': name, 'phone': phone, 'group': 'Python', 'monthly_fee': fee})
    assert response.status_code == 201
    return response.json()


def test_search_names_phone_format_pagination_and_literal_wildcards(client):
    one = add(client)
    two = add(client, name='AZIZA II', phone='+998901234568')
    literal = add(client, name='Name_100%', phone='+998999999999')
    add(client, name='Other', phone='+998901111111')
    assert [s['id'] for s in client.get('/students', params={'q': 'azIZA'}).json()] == [one['id'], two['id']]
    assert client.get('/students', params={'q': 'azIZA', 'offset': 1, 'limit': 1}).json()[0]['id'] == two['id']
    assert client.get('/students', params={'q': '901234567'}).json()[0]['id'] == one['id']
    assert client.get('/students', params={'q': '+998 (90) 123-45-67'}).json()[0]['id'] == one['id']
    for query in ['%', '_', '100%']:
        assert [s['id'] for s in client.get('/students', params={'q': query}).json()] == [literal['id']]
    assert client.get('/students', params={'q': 'missing'}).json() == []
    for query in ['', '   ', 'x' * 151]:
        assert client.get('/students', params={'q': query}).status_code == 422


def test_cyrillic_case_insensitive_search_on_postgres(client):
    if engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL Unicode case folding; SQLite lower() only handles ASCII')
    student = add(client, name='Азиза Каримова')
    assert client.get('/students', params={'q': 'АЗИЗА'}).json()[0]['id'] == student['id']


def test_student_creation_idempotency_normalizes_fee_and_survives_edit(client):
    body = {'name': 'Aziza', 'phone': '+998901234567', 'group': 'Python', 'monthly_fee': '100'}
    headers = {'Idempotency-Key': 'student-request-1'}
    first = client.post('/students', json=body, headers=headers)
    second = client.post('/students', json={**body, 'monthly_fee': '100.00'}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    sid = first.json()['id']
    client.patch(f'/students/{sid}', json={'name': 'Changed'})
    repeat = client.post('/students', json=body, headers=headers)
    assert repeat.json()['id'] == sid
    assert client.post('/students', json={**body, 'group': 'Other'}, headers=headers).status_code == 409
    assert len(client.get('/students').json()) == 1
    assert client.delete(f'/students/{sid}').status_code == 204
    assert client.get('/students').json() == []


def test_payment_history_pagination_and_order(client):
    student = add(client)
    for amount, date in [('50', '2026-09-30'), ('25', '2026-10-01'), ('10', '2026-10-01')]:
        assert client.post('/payments', json={'student_id': student['id'], 'amount': amount, 'date': date}).status_code == 201
    url = f"/students/{student['id']}/payments"
    rows = client.get(url, params={'limit': 2}).json()
    assert [Decimal(p['amount']) for p in rows] == [Decimal('10'), Decimal('25')]
    assert client.get(url, params={'offset': 2, 'limit': 2}).json()[0]['date'] == '2026-09-30'
