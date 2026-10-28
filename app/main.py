import secrets
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import date
from threading import RLock
from typing import Annotated
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.security import APIKeyHeader
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import Base, engine, get_db
from app.models import Payment, Student, StudentCreation
from app.schemas import Debtor, PaymentCreate, PaymentOut, StudentCreate, StudentOut, StudentPatch


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title='Учёт оплат учебного центра', version='1.0.0', lifespan=lifespan)
api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)


def authorize(key: Annotated[str | None, Depends(api_key_header)]):
    if key is None or not secrets.compare_digest(key, get_settings().api_key):
        raise HTTPException(401, 'Неверный API-ключ')


DB = Annotated[Session, Depends(get_db)]
protected = [Depends(authorize)]


# Small process-local TTL cache for read-heavy endpoints. Mutations invalidate all
# entries so subsequent reads always reflect committed database state.
_read_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = RLock()
_cache_generation = 0
_CACHE_TTL_SECONDS = 30
_CACHE_MAX_ENTRIES = 512


def cached(key, factory):
    now = time.monotonic()
    with _cache_lock:
        generation = _cache_generation
        entry = _read_cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
    value = factory()
    with _cache_lock:
        if generation == _cache_generation:
            for expired in [item for item, record in _read_cache.items() if record[0] <= now]:
                _read_cache.pop(expired, None)
            if len(_read_cache) >= _CACHE_MAX_ENTRIES:
                _read_cache.pop(next(iter(_read_cache)))
            _read_cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)
    return value


def invalidate_read_cache():
    global _cache_generation
    with _cache_lock:
        _cache_generation += 1
        _read_cache.clear()


def find_student(db, student_id):
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(404, 'Ученик не найден')
    return student


@app.get('/health')
def health(db: DB):
    db.execute(select(1))
    return {'status': 'ok'}


@app.post('/students', response_model=StudentOut, status_code=201, dependencies=protected)
def create_student(data: StudentCreate, db: DB,
                   idempotency_key: Annotated[str | None, Header(min_length=1, max_length=100)] = None):
    payload = data.model_dump(mode='json')
    payload['monthly_fee'] = format(data.monthly_fee, '.2f')
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if idempotency_key:
        existing = db.get(StudentCreation, idempotency_key)
        if existing:
            return same_student_creation(db, existing, fingerprint)
    student = Student(**data.model_dump())
    db.add(student)
    try:
        db.flush()
        if idempotency_key:
            db.add(StudentCreation(key=idempotency_key, fingerprint=fingerprint, student_id=student.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(StudentCreation, idempotency_key) if idempotency_key else None
        if existing:
            return same_student_creation(db, existing, fingerprint)
        raise HTTPException(409, 'Не удалось сохранить ученика; проверьте данные')
    invalidate_read_cache()
    return student


def same_student_creation(db, request, fingerprint):
    if request.fingerprint != fingerprint:
        raise HTTPException(409, 'Этот Idempotency-Key уже использован с другими данными')
    return find_student(db, request.student_id)


@app.get('/students', response_model=list[StudentOut], dependencies=protected)
def list_students(db: DB, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
                  q: str | None = Query(None, min_length=1, max_length=150)):
    query = select(Student)
    if q is not None:
        q = q.strip()
        if not q:
            raise HTTPException(422, 'Введите имя или телефон для поиска')
        predicates = [func.lower(Student.name).contains(q.lower(), autoescape=True),
                      Student.phone.contains(q, autoescape=True)]
        # Phone search ignores presentation characters in either stored or entered numbers.
        if re.fullmatch(r'[+()\-\s0-9]+', q):
            digits = re.sub(r'[^0-9]', '', q)
            if digits:
                phone = Student.phone
                for character in ('+', '-', '(', ')', ' '):
                    phone = func.replace(phone, character, '')
                predicates.append(phone.contains(digits, autoescape=True))
        query = query.where(or_(*predicates))
    key = ('students', offset, limit, q)
    return cached(key, lambda: db.scalars(query.order_by(Student.id).offset(offset).limit(limit)).all())


@app.get('/students/{student_id}', response_model=StudentOut, dependencies=protected)
def get_student(student_id: int, db: DB):
    return find_student(db, student_id)


@app.patch('/students/{student_id}', response_model=StudentOut, dependencies=protected)
def update_student(student_id: int, data: StudentPatch, db: DB):
    student = find_student(db, student_id)
    fields = data.model_dump(exclude_unset=True)
    if any(value is None for value in fields.values()):
        raise HTTPException(422, 'Поля ученика не могут быть null')
    for field, value in fields.items():
        setattr(student, field, value)
    db.commit()
    invalidate_read_cache()
    return student


@app.delete('/students/{student_id}', status_code=204, dependencies=protected)
def delete_student(student_id: int, db: DB):
    student = find_student(db, student_id)
    # Explicit deletion also works with SQLite test databases.
    db.execute(delete(StudentCreation).where(StudentCreation.student_id == student_id))
    db.execute(delete(Payment).where(Payment.student_id == student_id))
    db.delete(student)
    db.commit()
    invalidate_read_cache()
    return Response(status_code=204)


@app.post('/payments', response_model=PaymentOut, status_code=201, dependencies=protected)
def add_payment(data: PaymentCreate, db: DB,
                idempotency_key: Annotated[str | None, Header(max_length=100)] = None):
    find_student(db, data.student_id)
    if idempotency_key:
        existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing:
            return same_payment(existing, data)
    payment = Payment(**data.model_dump(), idempotency_key=idempotency_key or None)
    db.add(payment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key)) if idempotency_key else None
        if existing:
            return same_payment(existing, data)
        raise HTTPException(409, 'Данные изменились; проверьте ученика и повторите запрос')
    invalidate_read_cache()
    return payment


def same_payment(payment, data):
    if (payment.student_id, payment.amount, payment.date) != (data.student_id, data.amount, data.date):
        raise HTTPException(409, 'Этот Idempotency-Key уже использован с другими данными')
    return payment


@app.get('/students/{student_id}/payments', response_model=list[PaymentOut], dependencies=protected)
def list_payments(student_id: int, db: DB, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    find_student(db, student_id)
    return db.scalars(select(Payment).where(Payment.student_id == student_id)
                      .order_by(Payment.date.desc(), Payment.id.desc()).offset(offset).limit(limit)).all()


@app.get('/debtors', response_model=list[Debtor], dependencies=protected)
def debtors(db: DB, month: str = Query(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    year, number = map(int, month.split('-'))
    try:
        start = date(year, number, 1)
        end = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    except ValueError:
        raise HTTPException(422, 'Месяц должен быть от 0001-01 до 9999-11')
    totals = (select(Payment.student_id, func.sum(Payment.amount).label('paid'))
              .where(Payment.date >= start, Payment.date < end)
              .group_by(Payment.student_id).subquery())
    paid = func.coalesce(totals.c.paid, 0)
    def load():
        rows = db.execute(select(Student, paid).outerjoin(totals, Student.id == totals.c.student_id)
                          .where(paid < Student.monthly_fee).order_by(Student.id)).all()
        return [Debtor(student_id=s.id, name=s.name, phone=s.phone, group=s.group,
                       monthly_fee=s.monthly_fee, paid=p, debt=s.monthly_fee - p) for s, p in rows]
    return cached(('debtors', month), load)
