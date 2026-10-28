from datetime import date
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Group = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Fee = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Amount = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]


class StudentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Name
    phone: Phone
    group: Group
    monthly_fee: Fee


class StudentPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Name | None = None
    phone: Phone | None = None
    group: Group | None = None
    monthly_fee: Fee | None = None


class StudentOut(StudentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    student_id: int = Field(gt=0)
    amount: Amount
    date: date


class PaymentOut(PaymentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class Debtor(BaseModel):
    student_id: int
    name: str
    phone: str
    group: str
    monthly_fee: Decimal
    paid: Decimal
    debt: Decimal
