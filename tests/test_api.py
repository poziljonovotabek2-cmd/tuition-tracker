from decimal import Decimal
import pytest


def student(client, fee='100.00', name='Азиза'):
    response = client.post('/students', json={'name': name, 'phone': '+998901234567', 'group': 'Python', 'monthly_fee': fee})
    assert response.status_code == 201
    return response.json()['id']


def pay(client, sid, amount, date='2026-10-10', **kwargs):
    return client.post('/payments', json={'student_id': sid, 'amount': amount, 'date': date}, **kwargs)


def test_student_crud_and_delete_payments(client):
    sid = student(client)
    assert len(client.get('/students').json()) == 1
    assert client.patch(f'/students/{sid}', json={'group': 'Backend', 'monthly_fee': '120.50'}).json()['group'] == 'Backend'
    assert client.get(f'/students/{sid}').json()['monthly_fee'] == '120.50'
    assert client.patch(f'/students/{sid}', json={'name': None}).status_code == 422
    assert pay(client, sid, '20').status_code == 201
    assert client.delete(f'/students/{sid}').status_code == 204
    assert client.get(f'/students/{sid}').status_code == 404
    assert client.get(f'/students/{sid}/payments').status_code == 404
    assert client.get('/debtors?month=2026-10').json() == []


def test_cached_debtors_are_invalidated_after_payment_and_student_update(client):
    sid = student(client, '100.00', 'Cache test')
    month = '2026-10'
    assert client.get('/debtors', params={'month': month}).json()[0]['debt'] == '100.00'

    assert pay(client, sid, '40.00').status_code == 201
    debtors = client.get('/debtors', params={'month': month}).json()
    assert len(debtors) == 1
    assert debtors[0]['paid'] == '40.00'
    assert debtors[0]['debt'] == '60.00'

    client.patch(f'/students/{sid}', json={'monthly_fee': '30.00'})
    assert client.get('/debtors', params={'month': month}).json() == []


def test_debtors_partial_full_overpayment_zero_and_month_boundaries(client):
    partial, full, over, unpaid, free = [student(client, fee, str(i)) for i, fee in enumerate(['100.00'] * 4 + ['0'])]
    for amount, when in [('0.10', '2026-10-01'), ('0.20', '2026-10-31'), ('500', '2026-09-30'), ('500', '2026-11-01')]:
        assert pay(client, partial, amount, when).status_code == 201
    pay(client, full, '100')
    pay(client, over, '150')
    rows = client.get('/debtors?month=2026-10').json()
    assert [r['student_id'] for r in rows] == [partial, unpaid]
    assert Decimal(rows[0]['paid']) == Decimal('0.30')
    assert Decimal(rows[0]['debt']) == Decimal('99.70')
    assert Decimal(rows[1]['debt']) == Decimal('100')
    assert len(client.get(f'/students/{partial}/payments').json()) == 4


def test_year_boundary_and_leap_day(client):
    sid = student(client)
    pay(client, sid, '50', '2026-12-31')
    pay(client, sid, '100', '2027-01-01')
    assert client.get('/debtors?month=2026-12').json()[0]['debt'] == '50.00'
    assert client.get('/debtors?month=2027-01').json() == []
    pay(client, sid, '100', '2024-02-29')
    assert client.get('/debtors?month=2024-02').json() == []


@pytest.mark.parametrize('month', ['2026-00', '2026-13', '26-10', '2026-1', '0000-10', '9999-12', 'invalid'])
def test_invalid_month(client, month):
    assert client.get('/debtors', params={'month': month}).status_code == 422


@pytest.mark.parametrize('amount', ['0', '-1', '0.001', 'NaN', 'Infinity', '1000000000000'])
def test_invalid_payment(client, amount):
    sid = student(client)
    assert pay(client, sid, amount).status_code == 422


def test_idempotency_and_missing_student(client):
    sid = student(client)
    headers = {'Idempotency-Key': 'telegram:123:42'}
    first = pay(client, sid, '25', headers=headers)
    repeat = pay(client, sid, '25', headers=headers)
    assert first.json()['id'] == repeat.json()['id']
    assert pay(client, sid, '30', headers=headers).status_code == 409
    assert len(client.get(f'/students/{sid}/payments').json()) == 1
    assert pay(client, 9999, '10').status_code == 404
    assert pay(client, sid, '10', '2026-02-30').status_code == 422


def test_authorization_and_student_validation(client):
    assert client.get('/students', headers={'X-API-Key': 'wrong'}).status_code == 401
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.post('/students', json={'name': ' ', 'phone': '+998', 'group': 'A', 'monthly_fee': '-1'}).status_code == 422
