from datetime import date
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class Student(Base):
    __tablename__ = 'students'
    __table_args__ = (CheckConstraint('monthly_fee >= 0'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(40))
    group: Mapped[str] = mapped_column(String(100))
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class Payment(Base):
    __tablename__ = 'payments'
    __table_args__ = (
        CheckConstraint('amount > 0'),
        Index('ix_payments_student_date', 'student_id', 'date'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id', ondelete='CASCADE'))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    date: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)


class StudentCreation(Base):
    """Durable deduplication of a student-creation request, including concurrent retries."""
    __tablename__ = 'student_creation_requests'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    student_id: Mapped[int] = mapped_column(ForeignKey('students.id', ondelete='CASCADE'))
