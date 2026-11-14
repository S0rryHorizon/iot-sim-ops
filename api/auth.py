import logging
import secrets
from typing import Optional

import bcrypt
from fastapi import APIRouter, Header, HTTPException

from . import db
from .schemas import Login, ok

router = APIRouter()
logger = logging.getLogger("biz")


def require_auth_user(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.token, t.user_id, u.username, u.is_active
            FROM auth_token t
            JOIN user_account u ON u.user_id=t.user_id
            WHERE t.token=%s AND t.expires_at>NOW()
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="token invalid or expired")
        if not row["is_active"]:
            raise HTTPException(status_code=403, detail="user disabled")
        return {"user_id": row["user_id"], "username": row["username"], "token": token}


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    return require_auth_user(authorization)


def require_owned_sim(cur, iccid, user_id, columns="owner_user_id", for_update=False):
    lock = " FOR UPDATE" if for_update else ""
    cur.execute(f"SELECT {columns} FROM sim_card WHERE iccid=%s{lock}", (iccid,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row["owner_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return row


@router.post("/auth/login")
def auth_login(body: Login):
    with db.get_conn() as conn, db.rollback_on_error(conn), conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, password_hash, is_active FROM user_account WHERE username=%s",
            (body.username,),
        )
        user = cur.fetchone()
        if not user or not user["is_active"]:
            raise HTTPException(status_code=401, detail="invalid credential")
        if not bcrypt.checkpw(body.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            raise HTTPException(status_code=401, detail="invalid credential")

        tok = secrets.token_hex(32)
        # Acquire the parent X lock before the token's foreign-key check takes an S lock.
        cur.execute(
            "UPDATE user_account SET last_login_at=NOW() WHERE user_id=%s",
            (user["user_id"],),
        )
        cur.execute(
            """
            INSERT INTO auth_token (token, user_id, appid, expires_at, created_at)
            VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR), NOW())
            """,
            (tok, user["user_id"], None),
        )
        conn.commit()
    logger.info("auth.login ok user=%s", body.username)
    return ok({"token": tok, "token_type": "Bearer", "expires_in": 3600})
