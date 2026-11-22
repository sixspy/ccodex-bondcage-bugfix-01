"""保证金账户：开户、入金、查询。

BUGFIX-01：账户面呈现「活笼可用」与冻结互证，解笼拨付的冻结扣减须可对账。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import BondOpenIn, DepositIn
from reconcile_svc import _frozen_matches, _live_frozen

router = APIRouter(tags=["bonds"])


def _account_view(conn, row) -> dict:
    acct = dict(row)
    ok, proof = _frozen_matches(conn, row["party_key"], row)
    acct["live_cage_available"] = _live_frozen(conn, row["party_key"])
    acct["frozen_matches_live"] = ok
    acct["frozen_proof"] = proof
    return acct


@router.get("/api/bonds")
def list_bonds(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM bond_accounts ORDER BY id"""
        ).fetchall()
        return {"items": [_account_view(conn, r) for r in rows]}


@router.get("/api/bonds/{party_key}")
def get_bond(party_key: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "保证金户不存在")
        deposits = conn.execute(
            """SELECT * FROM deposit_ledger WHERE party_key=? ORDER BY id DESC LIMIT 50""",
            (party_key,),
        ).fetchall()
        cages = conn.execute(
            """SELECT id,cage_code,contract_no,amount,available,status,generation
               FROM cages WHERE party_key=? ORDER BY id DESC""",
            (party_key,),
        ).fetchall()
        release_freeze = conn.execute(
            """SELECT r.id, r.release_ref, r.cage_id, c.cage_code, r.amount,
                      r.frozen_deducted, r.account_frozen_after, r.created_at
               FROM releases r
               JOIN cages c ON c.id=r.cage_id
               WHERE c.party_key=? AND r.status='已拨付'
               ORDER BY r.id DESC LIMIT 50""",
            (party_key,),
        ).fetchall()
        return {
            "account": _account_view(conn, row),
            "deposits": [dict(d) for d in deposits],
            "cages": [dict(c) for c in cages],
            "release_freeze": [dict(x) for x in release_freeze],
        }


@router.post("/api/bonds")
def open_bond(body: BondOpenIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"bond:{body.party_key}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            exists = conn.execute(
                "SELECT id FROM bond_accounts WHERE party_key=?",
                (body.party_key,),
            ).fetchone()
            if exists:
                raise HTTPException(409, "当事人键已存在")
            now = now_iso()
            bal = float(body.initial_deposit)
            conn.execute(
                """INSERT INTO bond_accounts(party_key,party_name,balance,frozen,status,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.party_key, body.party_name, bal, 0.0, "正常", now),
            )
            if bal > 0:
                conn.execute(
                    """INSERT INTO deposit_ledger(party_key,amount,memo,created_at,actor)
                       VALUES (?,?,?,?,?)""",
                    (body.party_key, bal, "开户入金", now, user),
                )
            write_audit(
                conn,
                user,
                "bond_open",
                body.party_key,
                {"party_name": body.party_name, "initial_deposit": bal},
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (body.party_key,),
            ).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/bonds/deposit")
def deposit(body: DepositIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"bond:{body.party_key}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (body.party_key,),
            ).fetchone()
            if not acct:
                raise HTTPException(404, "保证金户不存在")
            if acct["status"] != "正常":
                raise HTTPException(409, "账户状态禁止入金")
            now = now_iso()
            amount = float(body.amount)
            conn.execute(
                "UPDATE bond_accounts SET balance = balance + ? WHERE party_key=?",
                (amount, body.party_key),
            )
            conn.execute(
                """INSERT INTO deposit_ledger(party_key,amount,memo,created_at,actor)
                   VALUES (?,?,?,?,?)""",
                (body.party_key, amount, body.memo or "手工入金", now, user),
            )
            write_audit(
                conn,
                user,
                "bond_deposit",
                body.party_key,
                {"amount": amount, "memo": body.memo},
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (body.party_key,),
            ).fetchone()
            return dict(row)
        finally:
            release(conn, lock_key, user)
            conn.commit()
