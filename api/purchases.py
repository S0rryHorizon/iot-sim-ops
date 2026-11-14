import re
import secrets
import uuid
from typing import Optional

import pymysql
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from . import db
from .auth import current_user, require_owned_sim
from .schemas import PurchaseBody, ok, validated_month

router = APIRouter()
INT_MAX = 2_147_483_647
TRANSID_DUPLICATE_PATTERN = re.compile(
    r"for key ['`](?:[A-Za-z0-9_]+\.)?uk_transid['`](?:\s|$)", re.IGNORECASE
)


def new_order_id():
    # 120 random bits fit the existing VARCHAR(32), including the PO prefix.
    # The database unique constraint remains the final guard.
    return "PO" + secrets.token_hex(15)


def transid_duplicate(error: pymysql.err.IntegrityError) -> bool:
    return (
        len(error.args) >= 2
        and error.args[0] == 1062
        and bool(TRANSID_DUPLICATE_PATTERN.search(str(error.args[1])))
    )


def purchase_identity(
    iccid: str, month: str, package_mb: int, product_id: Optional[str], price_cent: Optional[int]
) -> tuple:
    return (iccid, month, package_mb, product_id, price_cent)


def replay_or_conflict(order, requested, transid):
    actual = purchase_identity(
        order["iccid"],
        order["month"],
        order["package_mb"],
        order["product_id"],
        order["price_cent"],
    )
    if actual != requested:
        raise HTTPException(status_code=409, detail="X-TransId conflict")
    return ok(order, transid)


@router.post("/sims/{iccid}/purchase")
def purchase(
    iccid: str,
    body: PurchaseBody,
    user: dict = Depends(current_user),
    x_transid: Optional[str] = Header(None),
):
    month = validated_month(body.month)
    pkg = body.package_mb
    if x_transid is not None and (not x_transid.strip() or len(x_transid) > 64):
        raise HTTPException(status_code=400, detail="invalid X-TransId")
    transid = x_transid if x_transid is not None else "AUTO-" + uuid.uuid4().hex
    requested = purchase_identity(iccid, month, pkg, body.product_id, body.pay_amount_cent)
    with db.get_conn() as conn, db.rollback_on_error(conn), conn.cursor() as cur:
        require_owned_sim(cur, iccid, user["user_id"])
        if x_transid is not None:
            cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
            existing = cur.fetchone()
            if existing:
                return replay_or_conflict(existing, requested, transid)

        order_id = new_order_id()
        try:
            cur.execute(
                """
                INSERT INTO sim_purchase
                    (order_id, iccid, month, package_mb, product_id, price_cent,
                     status, transid, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    order_id,
                    iccid,
                    month,
                    pkg,
                    body.product_id,
                    body.pay_amount_cent,
                    "SUCCESS",
                    transid,
                ),
            )
        except pymysql.err.IntegrityError as error:
            # Only this INSERT's uk_transid race may become an idempotent replay.
            if x_transid is None or not transid_duplicate(error):
                raise
            conn.rollback()  # End the repeatable-read snapshot before reading the winner.
            require_owned_sim(cur, iccid, user["user_id"])
            cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
            winner = cur.fetchone()
            if not winner:
                raise HTTPException(status_code=500, detail="DB_ERROR")
            return replay_or_conflict(winner, requested, transid)

        cur.execute(
            """
            INSERT INTO sim_usage (iccid, month, used_mb, package_mb)
            VALUES (%s, %s, 0, 0)
            ON DUPLICATE KEY UPDATE package_mb = package_mb
            """,
            (iccid, month),
        )
        cur.execute(
            """
            UPDATE sim_usage
            SET package_mb = package_mb + %s, updated_at = NOW()
            WHERE iccid = %s AND month = %s AND package_mb <= %s
            """,
            (pkg, iccid, month, INT_MAX - pkg),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="package_mb capacity exceeded")
        cur.execute("SELECT * FROM sim_purchase WHERE transid=%s", (transid,))
        data = cur.fetchone()
        if not data:
            raise HTTPException(status_code=500, detail="DB_ERROR")
        response = ok(data, transid)
        conn.commit()
    return response


@router.get("/sims/{iccid}/purchases")
def purchase_list(
    iccid: str,
    user: dict = Depends(current_user),
    month: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    if month is not None:
        month = validated_month(month)
    with db.get_conn() as conn, conn.cursor() as cur:
        require_owned_sim(cur, iccid, user["user_id"])

        where = ["iccid=%s"]
        args = [iccid]
        if month is not None:
            where.append("month=%s")
            args.append(month)

        sql = f"""
            SELECT order_id, iccid, month, package_mb, product_id, price_cent,
                   status, transid, created_at
            FROM sim_purchase
            WHERE {" AND ".join(where)}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """
        args.extend([limit, offset])
        cur.execute(sql, args)
        items = cur.fetchall() or []

    payload = {"items": items, "limit": limit, "offset": offset}
    return ok(payload)
