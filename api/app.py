import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import pymysql
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, purchases
from .auth import current_user, require_owned_sim
from .parsing import parse_bool
from .schemas import ChangeStatus, ok, validated_month

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

router = APIRouter()
biz_logger = logging.getLogger("biz")
if not biz_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    biz_logger.addHandler(handler)
biz_logger.setLevel(logging.INFO)


def parse_cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip() for item in raw_value.split(",") if item.strip()]
    return origins or ["*"]


async def access_log_mw(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    transid = request.headers.get("X-TransId", "")
    t0 = time.perf_counter()
    resp = await call_next(request)
    dur_ms = (time.perf_counter() - t0) * 1000
    ip = request.client.host if request.client else "-"
    biz_logger.info(
        "http method=%s path=%s status=%s dur_ms=%.1f ip=%s rid=%s transid=%s",
        request.method,
        request.url.path,
        resp.status_code,
        dur_ms,
        ip,
        rid,
        transid,
    )
    resp.headers["X-Request-Id"] = rid
    return resp


async def database_error(request: Request, error: pymysql.MySQLError):
    # Read-only routes have nothing to roll back; they only need the same error shape.
    biz_logger.exception("Database query failed", exc_info=error)
    http = db.http_error(error)
    return JSONResponse(status_code=http.status_code, content={"detail": http.detail})


@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        yield
    finally:
        db.dispose_pool()


@router.get("/alive")
def alive(request: Request):
    return {"ok": True, "service": "iot-sim-ops", "version": request.app.version}


@router.get("/sims/search")
def sims_search(
    user: dict = Depends(current_user),
    iccid: Optional[str] = Query(None),
    imsi: Optional[str] = Query(None),
    msisdn: Optional[str] = Query(None),
):
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
        WHERE {" AND ".join(where)}
        LIMIT 1
    """
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
        if not row:
            # 不泄露别人卡的存在性：直接给 404
            raise HTTPException(status_code=404, detail="not found")

    return ok(row)


@router.get("/sims/{iccid}/status")
def sim_status(iccid: str, user: dict = Depends(current_user)):
    with db.get_conn() as conn, conn.cursor() as cur:
        row = require_owned_sim(cur, iccid, user["user_id"], columns="owner_user_id, status")

        biz_logger.info("sims.status get iccid=%s user=%s", iccid, user["username"])

        return ok({"iccid": iccid, "status": row["status"]})


@router.patch("/sims/{iccid}/status")
def change_status(iccid: str, body: ChangeStatus, user: dict = Depends(current_user)):
    action = body.action.upper().strip()
    if action not in ("SUSPEND", "RESUME"):
        raise HTTPException(status_code=400, detail="action must be SUSPEND or RESUME")
    new_status = "SUSPENDED" if action == "SUSPEND" else "ACTIVE"

    with db.get_conn() as conn, db.rollback_on_error(conn), conn.cursor() as cur:
        row = require_owned_sim(
            cur, iccid, user["user_id"], columns="owner_user_id, status", for_update=True
        )
        old_status = row["status"]
        if old_status == "DEACTIVATED":
            raise HTTPException(status_code=409, detail="sim deactivated")
        changed = old_status != new_status
        if changed:
            cur.execute("UPDATE sim_card SET status=%s WHERE iccid=%s", (new_status, iccid))
        cur.execute(
            """
            INSERT INTO sim_op_log (iccid, op_type, op_result, operator, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (iccid, action, "SUCCESS" if changed else "NOOP", user["username"]),
        )
        conn.commit()
    biz_logger.info(
        "sims.status patch iccid=%s from=%s to=%s user=%s",
        iccid,
        old_status,
        new_status,
        user["username"],
    )
    return ok({"iccid": iccid, "status": new_status})


@router.get("/sims/{iccid}/usage")
def usage(iccid: str, month: str, user: dict = Depends(current_user)):
    month = validated_month(month)
    with db.get_conn() as conn, conn.cursor() as cur:
        require_owned_sim(cur, iccid, user["user_id"])

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
        return ok(u)


def create_app():
    cors_origins = parse_cors_origins(os.getenv("CORS_ORIGINS", "*"))
    cors_allow_credentials = parse_bool(os.getenv("CORS_ALLOW_CREDENTIALS", "false"))
    if "*" in cors_origins and cors_allow_credentials:
        raise RuntimeError("CORS_ALLOW_CREDENTIALS=true requires explicit CORS_ORIGINS")

    application = FastAPI(title="iot-sim-ops", version="0.4.0", lifespan=lifespan)
    application.middleware("http")(access_log_mw)
    application.add_exception_handler(pymysql.MySQLError, database_error)
    # CORS defaults to a credential-free demo configuration. Set explicit origins
    # in .env before allowing browser credentials.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.mount(
        "/web", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web"
    )
    application.include_router(router)
    application.include_router(auth.router)
    application.include_router(purchases.router)
    return application


app = create_app()
