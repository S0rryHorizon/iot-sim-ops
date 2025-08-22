import os
from pathlib import Path as SysPath
from datetime import datetime, timedelta
import random
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor
import bcrypt
from pydantic import BaseModel
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder


# -------- env & app base --------
# 自动加载与本文件同目录的 .env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI(title="iot-sim-ops", version="0.3.0")

# CORS（同网段演示，简单放开；生产请改白名单）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    package_mb: int
    product_id: Optional[str] = None
    pay_amount_cent: Optional[int] = None

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
            conn.commit()
        except Exception as e:
            conn.rollback()
            print("DB ERROR on change_status:", repr(e))
            raise HTTPException(status_code=500, detail="DB_ERROR")

    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, "status": new_status}, "trace": {}}

@app.get("/sims/{iccid}/usage")
def usage(iccid: str, month: str, authorization: Optional[str] = Header(None)):
    user = require_auth_user(authorization)
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
    month = body.month
    pkg = int(body.package_mb or 0)
    if pkg <= 0:
        raise HTTPException(status_code=400, detail="invalid package_mb")

    with get_conn() as conn, conn.cursor() as cur:
        # 归属校验
        cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")

        # 幂等：按 transid 查已存在订单
        if x_transid:
            cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (x_transid,))
            exist = cur.fetchone()
            if exist:
                return {"code": "0", "msg": "ok", "data": exist, "trace": {"transid": x_transid}}

        order_id = "PO" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(1000,9999)}"
        transid = x_transid or (datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{random.randint(1000,9999)}")

        try:
            cur.execute(
                """
                INSERT INTO sim_purchase (order_id, iccid, month, package_mb, status, transid, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                """,
                (order_id, iccid, month, pkg, "SUCCESS", transid),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            print("DB ERROR on purchase:", repr(e))
            raise HTTPException(status_code=500, detail="DB_ERROR")

        cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
        data = cur.fetchone()

    return {"code": "0", "msg": "ok", "data": jsonable_encoder(data), "trace": {"transid": transid}}

@app.get("/sims/{iccid}/purchases")
def purchase_list(
    iccid: str,
    authorization: Optional[str] = Header(None),
    month: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    user = require_auth_user(authorization)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["owner_user_id"] != user["user_id"]:
            raise HTTPException(status_code=403, detail="forbidden")

        where = ["iccid=%s"]
        args = [iccid]
        if month:
            where.append("month=%s")
            args.append(month)

        sql = f"""
            SELECT order_id, iccid, month, package_mb, status, transid, created_at
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


