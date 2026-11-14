import re
from typing import Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator


def parse_bool(raw_value: str, default: bool = False) -> bool:
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {raw_value!r}")


class Login(BaseModel):
    username: str
    password: str


class ChangeStatus(BaseModel):
    action: str  # "SUSPEND" or "RESUME"


class PurchaseBody(BaseModel):
    month: str
    package_mb: int = Field(gt=0, le=2_147_483_647)
    product_id: Optional[str] = Field(default=None, max_length=64)
    pay_amount_cent: Optional[int] = Field(default=None, ge=0, le=2_147_483_647)

    @field_validator("package_mb", "pay_amount_cent", mode="before")
    @classmethod
    def reject_boolean_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("amount must be an integer")
        return value


MONTH_PATTERN = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])\Z")


def validated_month(value: str) -> str:
    if not MONTH_PATTERN.fullmatch(value) or value[:4] == "0000":
        raise HTTPException(status_code=400, detail="invalid month")
    return value


def ok(data, transid=None):
    return {
        "code": "0",
        "msg": "ok",
        "data": jsonable_encoder(data),
        "trace": {"transid": transid} if transid is not None else {},
    }
