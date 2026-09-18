import os
import logging
import sys
import time
import uuid
from pathlib import Path as SysPath
from datetime import datetime
import random
import re
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor
import bcrypt
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder

# -------- env & app base --------
# 自动加载与本文件同目录的 .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

def parse_cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip() for item in raw_value.split(",") if item.strip()]
    return origins or ["*"]


def parse_bool(raw_value: str, default: bool = False) -> bool:
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {raw_value!r}")


cors_origins = parse_cors_origins(os.getenv("CORS_ORIGINS", "*"))
cors_allow_credentials = parse_bool(
    os.getenv("CORS_ALLOW_CREDENTIALS", "false")
)
if "*" in cors_origins and cors_allow_credentials:
    raise RuntimeError(
        "CORS_ALLOW_CREDENTIALS=true requires explicit CORS_ORIGINS"
    )

app = FastAPI(title="iot-sim-ops", version="0.3.1")

biz_logger = logging.getLogger("biz")
if not biz_logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h.setFormatter(fmt)
    biz_logger.addHandler(h)
biz_logger.setLevel(logging.INFO)

@app.middleware("http")
async def access_log_mw(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    transid = request.headers.get("X-TransId", "")
    t0 = time.perf_counter()
    resp = await call_next(request)
    dur_ms = (time.perf_counter() - t0) * 1000
    ip = request.client.host if request.client else "-"
    # 统一的访问日志
    biz_logger.info(
        "http method=%s path=%s status=%s dur_ms=%.1f ip=%s rid=%s transid=%s",
        request.method, request.url.path, resp.status_code, dur_ms, ip, rid, transid
    )
    resp.headers["X-Request-Id"] = rid
    return resp
# === end logging setup ===

# CORS defaults to a credential-free demo configuration. Set explicit origins
# in .env before allowing browser credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态站点 /web -> web 目录（用于 login.html/index.html）
app.mount(
    "/web",
    StaticFiles(directory=SysPath(__file__).parent / "web", html=True),
    name="web",
)

# -------- DB helpers --------
def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS", ""),
        db=os.getenv("DB_NAME", "iot_sim_ops"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=DictCursor,
    )

def require_auth_user(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with get_conn() as conn, conn.cursor() as cur:
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

# -------- models --------
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


INT_MAX = 2_147_483_647
MONTH_PATTERN = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])\Z")
TRANSID_DUPLICATE_PATTERN = re.compile(
    r"for key ['`](?:[A-Za-z0-9_]+\.)?uk_transid['`](?:\s|$)", re.IGNORECASE
)


def validated_month(value: str) -> str:
    if not MONTH_PATTERN.fullmatch(value) or value[:4] == "0000":
        raise HTTPException(status_code=400, detail="invalid month")
    return value


def transid_duplicate(error: pymysql.err.IntegrityError) -> bool:
    return (
        len(error.args) >= 2
        and error.args[0] == 1062
        and bool(TRANSID_DUPLICATE_PATTERN.search(str(error.args[1])))
    )


def purchase_identity(iccid: str, month: str, package_mb: int,
                      product_id: Optional[str], price_cent: Optional[int]) -> tuple:
    return (iccid, month, package_mb, product_id, price_cent)


def purchase_response(order: dict, transid: str) -> dict:
    return {"code": "0", "msg": "ok", "data": jsonable_encoder(order), "trace": {"transid": transid}}

# -------- endpoints --------
@app.get("/alive")
def alive():
    return {"ok": True, "service": "iot-sim-ops", "version": app.version}

@app.post("/auth/login")
def auth_login(body: Login):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, password_hash, is_active FROM user_account WHERE username=%s",
            (body.username,),
        )
        user = cur.fetchone()
        if not user or not user["is_active"]:
            raise HTTPException(status_code=401, detail="invalid credential")
        if not bcrypt.checkpw(
            body.password.encode("utf-8"), user["password_hash"].encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="invalid credential")

        # 生成 64 字节十六进制 token
        cur.execute("SELECT LPAD(SUBSTRING(SHA2(UUID(),256),1,64),64,'a') AS tok")
        tok = cur.fetchone()["tok"]

        try:
            # appid 置 NULL 以避开外键（专用于“用户登录流”）
            cur.execute(
                """
                INSERT INTO auth_token (token, user_id, appid, expires_at, created_at)
                VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR), NOW())
                """,
                (tok, user["user_id"], None),
            )
            cur.execute(
                "UPDATE user_account SET last_login_at=NOW() WHERE user_id=%s",
                (user["user_id"],),
            )
            conn.commit()
            biz_logger.info("auth.login ok user=%s", body.username)

        except Exception as e:
            conn.rollback()
            print("DB ERROR on /auth/login:", repr(e))
            raise HTTPException(status_code=500, detail="DB_ERROR")
    return {
        "code": "0",
        "msg": "ok",
        "data": {"token": tok, "token_type": "Bearer", "expires_in": 3600},
        "trace": {"transid": "login"},
    }

@app.get("/sims/search")
def sims_search(
    authorization: Optional[str] = Header(None),
    iccid: Optional[str] = Query(None),
    imsi: Optional[str] = Query(None),
    msisdn: Optional[str] = Query(None),
):
    user = require_auth_user(authorization)

    if not any([iccid, imsi, msisdn]):
        raise HTTPException(status_code=400, detail="at least one of iccid/imsi/msisdn")

    where = ["owner_user_id=%s"]
    args = [user["user_id"]]
    if iccid:
        where.append("iccid=%s")
        args.append(iccid)
    if imsi:
        where.append("imsi=%s")
        args.append(imsi)
    if msisdn:
        where.append("msisdn=%s")
        args.append(msisdn)

    sql = f"""
        SELECT iccid, imsi, msisdn, status, activated_at, deactivated_at, created_at
        FROM sim_card
        WHERE {' AND '.join(where)}
        LIMIT 1
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
        if not row:
            # 不泄露别人卡的存在性：直接给 404
            raise HTTPException(status_code=404, detail="not found")

    return {"code": "0", "msg": "ok", "data": jsonable_encoder(row), "trace": {"transid": "search"}}

@app.get("/sims/{iccid}/status")
def sim_status(iccid: str, authorization: Optional[str] = Header(None)):
    user = require_auth_user(authorization)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id, status FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")
        
        # >>> 新增：业务日志（就在 return 之前）
        biz_logger.info("sims.status get iccid=%s user=%s", iccid, user["username"])
        # <<< 新增结束

        return {"code": "0", "msg": "ok", "data": {"iccid": iccid, "status": row["status"]}, "trace": {}}

@app.patch("/sims/{iccid}/status")
def change_status(iccid: str, body: ChangeStatus, authorization: Optional[str] = Header(None)):
    user = require_auth_user(authorization)
    action = body.action.upper().strip()
    if action not in ("SUSPEND", "RESUME"):
        raise HTTPException(status_code=400, detail="action must be SUSPEND or RESUME")
    new_status = "SUSPENDED" if action == "SUSPEND" else "ACTIVE"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id, status FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")

        # >>> 新增：保存旧状态
        old_status = row["status"]
        # <<< 新增结束

        try:
            cur.execute("UPDATE sim_card SET status=%s WHERE iccid=%s", (new_status, iccid))
            # 记录操作日志（如果没有该表就忽略报错）
            try:
                cur.execute(
                    """
                    INSERT INTO sim_op_log (iccid, op_type, op_result, operator, created_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (iccid, action, "SUCCESS", user["username"]),
                )
            except Exception as _:
                pass

             # >>> 新增：业务日志（提交前更合适，确保与DB一致）
            biz_logger.info(
                "sims.status patch iccid=%s from=%s to=%s user=%s",
                iccid, old_status, new_status, user["username"]
            )
            # <<< 新增结束

            conn.commit()
        except Exception as e:
            conn.rollback()
            print("DB ERROR on change_status:", repr(e))
            raise HTTPException(status_code=500, detail="DB_ERROR")

    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, "status": new_status}, "trace": {}}

@app.get("/sims/{iccid}/usage")
def usage(iccid: str, month: str, authorization: Optional[str] = Header(None)):
    user = require_auth_user(authorization)
    month = validated_month(month)
    with get_conn() as conn, conn.cursor() as cur:
        # 先做归属校验
        cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")

        # 查询用量（根据你的库结构调整：这里假设 sim_usage 表）
        cur.execute(
            """
            SELECT iccid, month, used_mb, package_mb
            FROM sim_usage
            WHERE iccid=%s AND month=%s
            """,
            (iccid, month),
        )
        u = cur.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="no usage for month")
        return {"code": "0", "msg": "ok", "data": jsonable_encoder(u), "trace": {}}

@app.post("/sims/{iccid}/purchase")
def purchase(iccid: str, body: PurchaseBody, authorization: Optional[str] = Header(None), x_transid: Optional[str] = Header(None)):
    user = require_auth_user(authorization)
    month = validated_month(body.month)
    pkg = body.package_mb
    if x_transid is not None and (not x_transid.strip() or len(x_transid) > 64):
        raise HTTPException(status_code=400, detail="invalid X-TransId")
    transid = x_transid if x_transid is not None else (
        datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{random.randint(1000,9999)}"
    )
    requested = purchase_identity(iccid, month, pkg, body.product_id, body.pay_amount_cent)
    with get_conn() as conn, conn.cursor() as cur:
        try:
            def check_ownership():
                cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="not found")
                if row["owner_user_id"] != user["user_id"]:
                    raise HTTPException(status_code=403, detail="forbidden")

            def replay_or_conflict(order):
                actual = purchase_identity(
                    order["iccid"], order["month"], order["package_mb"],
                    order["product_id"], order["price_cent"],
                )
                if actual != requested:
                    raise HTTPException(status_code=409, detail="X-TransId conflict")
                return purchase_response(order, transid)

            check_ownership()
            if x_transid is not None:
                cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
                existing = cur.fetchone()
                if existing:
                    return replay_or_conflict(existing)
        except pymysql.MySQLError as error:
            conn.rollback()
            raise HTTPException(status_code=500, detail="DB_ERROR") from error

        order_id = "PO" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(1000,9999)}"
        try:
            cur.execute(
                """
                INSERT INTO sim_purchase
                    (order_id, iccid, month, package_mb, product_id, price_cent, status, transid, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (order_id, iccid, month, pkg, body.product_id, body.pay_amount_cent, "SUCCESS", transid),
            )
        except pymysql.err.IntegrityError as error:
            # Only this INSERT's uk_transid race may become an idempotent replay.
            conn.rollback()  # End the repeatable-read snapshot before reading the winner.
            if x_transid is None or not transid_duplicate(error):
                raise HTTPException(status_code=500, detail="DB_ERROR") from error
            try:
                check_ownership()
                cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
                winner = cur.fetchone()
            except pymysql.MySQLError as read_error:
                conn.rollback()
                raise HTTPException(status_code=500, detail="DB_ERROR") from read_error
            if not winner:
                raise HTTPException(status_code=500, detail="DB_ERROR")
            return replay_or_conflict(winner)
        except pymysql.MySQLError as error:
            conn.rollback()
            raise HTTPException(status_code=500, detail="DB_ERROR") from error

        try:
            # Lock any current usage row and reject a sum that cannot fit MySQL INT.
            cur.execute(
                "SELECT package_mb FROM sim_usage WHERE iccid=%s AND month=%s FOR UPDATE",
                (iccid, month),
            )
            current_usage = cur.fetchone()
            if current_usage and current_usage["package_mb"] > INT_MAX - pkg:
                conn.rollback()
                raise HTTPException(status_code=409, detail="package_mb capacity exceeded")
            cur.execute(
                """
                INSERT INTO sim_usage (iccid, month, used_mb, package_mb)
                VALUES (%s, %s, 0, %s)
                ON DUPLICATE KEY UPDATE
                    package_mb = package_mb + VALUES(package_mb),
                    updated_at = NOW()
                """,
                (iccid, month, pkg),
            )
            conn.commit()
            cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
            data = cur.fetchone()
            if not data:
                raise HTTPException(status_code=500, detail="DB_ERROR")
        except pymysql.MySQLError as error:
            conn.rollback()
            raise HTTPException(status_code=500, detail="DB_ERROR") from error

    return purchase_response(data, transid)

@app.get("/sims/{iccid}/purchases")
def purchase_list(
    iccid: str,
    authorization: Optional[str] = Header(None),
    month: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    user = require_auth_user(authorization)
    if month is not None:
        month = validated_month(month)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")

        where = ["iccid=%s"]
        args = [iccid]
        if month is not None:
            where.append("month=%s")
            args.append(month)

        sql = f"""
            SELECT order_id, iccid, month, package_mb, product_id, price_cent, status, transid, created_at
            FROM sim_purchase
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        args.extend([limit, offset])
        cur.execute(sql, args)
        items = cur.fetchall() or []

    payload = {"items": items or [], "limit": limit, "offset": offset}
    return {"code": "0", "msg": "ok", "data": jsonable_encoder(payload), "trace": {}}
