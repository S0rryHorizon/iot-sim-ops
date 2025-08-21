
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
import pymysql, os, bcrypt
from datetime import datetime, timedelta
import random

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")
DB_NAME = os.getenv("DB_NAME", "iot_sim_ops")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

def get_conn():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor, autocommit=False
    )

app = FastAPI(title="iot-sim-ops mock", version="0.3.0")

@app.get("/alive")
def alive():
    return {"ok": True, "service": "iot-sim-ops", "version": "0.3.0"}

# ---- Auth helpers ----

def get_user_id_from_token(authorization: str | None) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token")
    token = authorization.split(" ", 1)[1]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM auth_token WHERE token=%s AND NOW()<expires_at", (token,))
        row = cur.fetchone()
        if not row or not row["user_id"]:
            # 不允许旧模式（appid-only）的 token 访问
            raise HTTPException(status_code=401, detail="Token invalid or not user-bound")
        return int(row["user_id"])

def ensure_ownership(user_id: int, iccid: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="SIM not found")
        if row["owner_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: not your SIM")

# ---- Schemas ----
class Login(BaseModel):
    username: str
    password: str

class PurchaseReq(BaseModel):
    month: str
    package_mb: int
    product_id: str | None = None
    pay_amount_cent: int | None = None

class StatusReq(BaseModel):
    action: str

# ---- Routes ----

@app.post("/auth/login")
def auth_login(body: Login):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, password_hash, is_active FROM user_account WHERE username=%s", (body.username,))
        user = cur.fetchone()
        if not user or not user["is_active"]:
            raise HTTPException(status_code=401, detail="invalid credential")
        if not bcrypt.checkpw(body.password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            raise HTTPException(status_code=401, detail="invalid credential")

        # 生成 token，并绑定到用户；显式写 appid 与 created_at，更兼容
        cur.execute("SELECT LPAD(SUBSTRING(SHA2(UUID(),256),1,64),64,'a') AS tok")
        tok = cur.fetchone()["tok"]

        try:
            cur.execute(
                """
                INSERT INTO auth_token (token, user_id, appid, expires_at, created_at)
                VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR), NOW())
                """,
                (tok, user["user_id"], None),
            )
            cur.execute("UPDATE user_account SET last_login_at=NOW() WHERE user_id=%s", (user["user_id"],))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print("DB ERROR on /auth/login:", repr(e))
            raise HTTPException(status_code=500, detail=f"DB_ERROR:{e.__class__.__name__}")

    return {
        "code": "0",
        "msg": "ok",
        "data": {"token": tok, "token_type": "Bearer", "expires_in": 3600},
        "trace": {"transid": "login"},
    }


@app.get("/sims/{iccid}/usage")
def usage(iccid: str, month: str, authorization: str | None = Header(None)):
    user_id = get_user_id_from_token(authorization)
    ensure_ownership(user_id, iccid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT iccid, month, effective_total_mb, used_mb, remain_mb, unit, last_update
               FROM v_usage_effective WHERE iccid=%s AND month=%s""",
            (iccid, month),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
    return {"code": "0", "msg": "ok", "data": row, "trace": {"transid": "usage"}}

@app.post("/sims/{iccid}/purchase")
def purchase(iccid: str, req: PurchaseReq, authorization: str | None = Header(None), x_transid: str | None = Header(None)):
    user_id = get_user_id_from_token(authorization)
    ensure_ownership(user_id, iccid)
    if not x_transid:
        return {"code": "E_MISSING_TRANSID", "msg": "missing X-TransId", "data": None, "trace": {"transid": None}}

    poid = "PO" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0,9999):04d}"
    price = req.pay_amount_cent or 0

    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO purchase_order
                  (order_id, iccid, month, package_mb, price_cent, status, product_id, transid)
                VALUES
                  (%s, %s, %s, %s, %s, 'SUCCESS', %s, %s)
                """,
                (poid, iccid, req.month, req.package_mb, price, req.product_id, x_transid)
            )
            conn.commit()
        except pymysql.err.IntegrityError:
            pass

        cur.execute("SELECT order_id, iccid, month, package_mb, status, transid FROM purchase_order WHERE transid=%s", (x_transid,))
        row = cur.fetchone()

    return {"code": "0", "msg": "ok", "data": row, "trace": {"transid": x_transid}}

@app.get("/sims/{iccid}/status")
def get_status(iccid: str, authorization: str | None = Header(None)):
    user_id = get_user_id_from_token(authorization)
    ensure_ownership(user_id, iccid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, **row}, "trace": {"transid": "status"}}

@app.patch("/sims/{iccid}/status")
def patch_status(iccid: str, req: StatusReq, authorization: str | None = Header(None)):
    user_id = get_user_id_from_token(authorization)
    ensure_ownership(user_id, iccid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] == "DEACTIVATED":
            raise HTTPException(status_code=400, detail="E_STATUS_NOT_ALLOWED")
        to = "SUSPENDED" if req.action.upper() == "SUSPEND" else "ACTIVE"
        cur.execute("UPDATE sim_card SET status=%s WHERE iccid=%s", (to, iccid))
        conn.commit()
    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, "status": to}, "trace": {"transid": "patch_status"}}

@app.get("/sims/search")
def sim_search(
    authorization: str | None = Header(None),
    iccid: str | None = Query(None),
    imsi: str | None = Query(None),
    msisdn: str | None = Query(None)
):
    user_id = get_user_id_from_token(authorization)
    if not (iccid or imsi or msisdn):
        raise HTTPException(status_code=400, detail="Provide at least one of iccid/imsi/msisdn")

    where = ["owner_user_id=%s"]
    args = [user_id]
    if iccid:
        where.append("iccid=%s"); args.append(iccid)
    if imsi:
        where.append("imsi=%s"); args.append(imsi)
    if msisdn:
        where.append("msisdn=%s"); args.append(msisdn)

    sql = "SELECT iccid, imsi, msisdn, status, activated_at, deactivated_at, created_at FROM sim_card WHERE " + " AND ".join(where)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="not found")

    # 如果提供了多个条件且返回多行，提示歧义
    if (sum(1 for x in [iccid,imsi,msisdn] if x) >= 2) and len(rows) > 1:
        return {"code": "E_AMBIGUOUS", "msg": "conditions matched multiple rows", "data": rows, "trace": {"transid": "search"}}

    return {"code": "0", "msg": "ok", "data": rows[0], "trace": {"transid": "search"}}

@app.get("/sims/{iccid}/purchases")
def list_purchases(
    iccid: str,
    month: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None)
):
    user_id = get_user_id_from_token(authorization)
    ensure_ownership(user_id, iccid)
    where = ["iccid=%s"]
    args = [iccid]
    if month:
        where.append("month=%s"); args.append(month)
    sql = f"SELECT order_id, month, package_mb, price_cent, status, transid, created_at FROM purchase_order WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT %s OFFSET %s"
    args.extend([limit, offset])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return {"code": "0", "msg": "ok", "data": rows, "trace": {"transid": "purchases"}}
