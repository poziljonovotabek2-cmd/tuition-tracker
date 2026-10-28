"""Add clearly labelled demo students without modifying existing records/payments."""
from decimal import Decimal
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Student

DEMO_STUDENTS = [
    ('Азиза (тест)', '+998900000001', 'DEMO / Python', '500000.00'),
    ('Тимур (тест)', '+998900000002', 'DEMO / Python', '500000.00'),
    ('Малика (тест)', '+998900000003', 'DEMO / English', '400000.00'),
    ('Сардор (тест)', '+998900000004', 'DEMO / English', '400000.00'),
    ('Дилноза (тест)', '+998900000005', 'DEMO / Math', '350000.00'),
]


def seed_students(db):
    created = []
    for name, phone, group, fee in DEMO_STUDENTS:
        existing = db.scalar(select(Student).where(Student.phone == phone))
        if existing is None:
            student = Student(name=name, phone=phone, group=group, monthly_fee=Decimal(fee))
            db.add(student)
            db.flush()
            created.append(student.id)
    db.commit()
    return created


if __name__ == '__main__':
    with SessionLocal() as db:
        created = seed_students(db)
    print(f'Created demo students: {len(created)}; IDs: {created}')
