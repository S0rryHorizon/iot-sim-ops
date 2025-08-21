
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from datetime import datetime
import random
import pymysql, os

# Read DB config from environment (use a .env loader if preferred)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")
DB_NAME = os.getenv("DB_NAME", "iot_sim_ops")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

def get_conn():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor
    )

app = FastAPI(title="iot-sim-ops mock", version="0.1.0")

@app.get("/alive")
def alive():
    return {"ok": True, "service": "iot-sim-ops", "version": "0.1.0"}

def check_token(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No token")
    token = authorization.split(" ", 1)[1]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM auth_token WHERE token=%s AND NOW()<expires_at", (token,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=401, detail="Token expired/invalid")

class Cred(BaseModel):
    appid: str
    password: str

@app.post("/auth/token")
def auth_token(body: Cred):
    # Demo only: fixed demo account.
    if body.appid != "demo-app" or body.password != "demo-password":
        raise HTTPException(status_code=401, detail="invalid credential")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT LPAD(SUBSTRING(SHA2(UUID(),256),1,64),64,'a') AS tok")
        tok = cur.fetchone()["tok"]
        cur.execute(
            "INSERT INTO auth_token(token, appid, expires_at) VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR))",
            (tok, "demo-app"),
        )
        conn.commit()
    return {"code": "0", "msg": "ok", "data": {"token": tok, "token_type": "Bearer", "expires_in": 3600}, "trace": {"transid": "mock"}}

@app.get("/sims/{iccid}/usage")
def usage(iccid: str, month: str, authorization: str | None = Header(None)):
    check_token(authorization)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT iccid, month, effective_total_mb, used_mb, remain_mb, unit, last_update
               FROM v_usage_effective WHERE iccid=%s AND month=%s""",
            (iccid, month),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
    return {"code": "0", "msg": "ok", "data": row, "trace": {"transid": "mock"}}

class PurchaseReq(BaseModel):
    month: str
    package_mb: int
    product_id: str | None = None
    pay_amount_cent: int | None = None

@app.post("/sims/{iccid}/purchase")
def purchase(iccid: str, req: PurchaseReq, authorization: str | None = Header(None), x_transid: str | None = Header(None)):
    check_token(authorization)
    if not x_transid:
        # 统一用 JSON 返回错误，避免 Postman 解析失败
        return {"code": "E_MISSING_TRANSID", "msg": "missing X-TransId", "data": None, "trace": {"transid": None}}

    # 1) 用 Python 生成稳定的订单号（避开 SQL 里的函数差异）
    poid = "PO" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0,9999):04d}"
    price = req.pay_amount_cent or 0

    try:
        with get_conn() as conn, conn.cursor() as cur:
            # 2) 朴素 INSERT + 幂等：transid 唯一，重复则走查询返回首单
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
                # 命中了 uk_po_transid 幂等约束：忽略插入错误，直接查回第一次结果
                pass

            cur.execute(
                "SELECT order_id, iccid, month, package_mb, status, transid FROM purchase_order WHERE transid=%s",
                (x_transid,)
            )
            row = cur.fetchone()

        return {"code": "0", "msg": "ok", "data": row, "trace": {"transid": x_transid}}

    except Exception as e:
        # 保证前端永远拿到 JSON（便于 Postman 的断言与排错）
        return {"code": "E_PURCHASE_FAIL", "msg": str(e), "data": None, "trace": {"transid": x_transid}}

@app.get("/sims/{iccid}/status")
def get_status(iccid: str, authorization: str | None = Header(None)):
    check_token(authorization)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM sim_card WHERE iccid=%s", (iccid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, **row}, "trace": {"transid": "mock"}}

class StatusReq(BaseModel):
    action: str

@app.patch("/sims/{iccid}/status")
def patch_status(iccid: str, req: StatusReq, authorization: str | None = Header(None)):
    check_token(authorization)
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
    return {"code": "0", "msg": "ok", "data": {"iccid": iccid, "status": to}, "trace": {"transid": "mock"}}
