from app.db import get_db
from app.main import app
from app.seed import seed_students


def test_seed_is_repeatable_and_preserves_real_students_and_payments(client):
    real = client.post('/students', json={'name': 'Реальный ученик', 'phone': '+998999999999',
                                       'group': 'Real', 'monthly_fee': '100'}).json()
    client.post('/payments', json={'student_id': real['id'], 'amount': '50', 'date': '2026-10-27'})
    dependency = app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        assert len(seed_students(db)) == 5
        assert seed_students(db) == []
    finally:
        dependency.close()
    rows = client.get('/students').json()
    assert len(rows) == 6
    assert sum(s['group'].startswith('DEMO / ') for s in rows) == 5
    assert len(client.get(f"/students/{real['id']}/payments").json()) == 1
