"""对账互证：笼面可用 + 已拨付 + 已罚没 须等于笼面金额（同代口径）。

BUGFIX-01：互证按真实账户冻结与活笼可用合计计算，禁止恒绿。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["reconcile"])

LIVE_STATUSES = ("已冻结", "核验通过", "核验违约", "部分解笼", "部分罚没")


def _live_frozen(conn, party_key: str) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(available),0) s FROM cages
           WHERE party_key=? AND status IN ('已冻结','核验通过','核验违约','部分解笼','部分罚没')""",
        (party_key,),
    ).fetchone()
    return round(float(row["s"]), 6)


def _frozen_matches(conn, party_key: str, acct) -> tuple[bool, dict]:
    live = _live_frozen(conn, party_key)
    frozen = round(float(acct["frozen"]), 6) if acct else 0.0
    ok = abs(frozen - live) < 1e-6
    return ok, {
        "formula": "account.frozen == sum(live cage available)",
        "left": frozen,
        "right": live,
    }


def _reconcile_cage(conn, cage) -> dict:
    cage_id = cage["id"]
    gen = int(cage["generation"])
    amount = float(cage["amount"])
    available = float(cage["available"])
    released = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM releases
           WHERE cage_id=? AND status='已拨付' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    forfeited = conn.execute(
        """SELECT COALESCE(SUM(amount),0) s FROM forfeits
           WHERE cage_id=? AND status='已罚没' AND cage_generation=?""",
        (cage_id, gen),
    ).fetchone()["s"]
    accounted = round(float(available) + float(released) + float(forfeited), 6)
    ok = abs(accounted - amount) < 1e-6
    party = cage["party_key"]
    acct = conn.execute(
        "SELECT balance, frozen FROM bond_accounts WHERE party_key=?",
        (party,),
    ).fetchone()
    frozen_ok, frozen_proof = _frozen_matches(conn, party, acct)
    return {
        "cage_id": cage_id,
        "cage_code": cage["cage_code"],
        "party_key": party,
        "contract_no": cage["contract_no"],
        "generation": gen,
        "status": cage["status"],
        "amount": amount,
        "available": available,
        "released": float(released),
        "forfeited": float(forfeited),
        "accounted": accounted,
        "balanced": ok,
        "gap": round(amount - accounted, 6),
        "account_balance": float(acct["balance"]) if acct else None,
        "account_frozen": float(acct["frozen"]) if acct else None,
        "live_cage_available": frozen_proof["right"],
        "frozen_matches_live": frozen_ok,
        "proof": {
            "formula": "available + released + forfeited == amount（同笼代）",
            "left": accounted,
            "right": amount,
        },
        "frozen_proof": frozen_proof,
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cages = conn.execute("SELECT * FROM cages ORDER BY id").fetchall()
        items = [_reconcile_cage(conn, c) for c in cages]
        bad = [i for i in items if not i["balanced"]]
        frozen_bad = [i for i in items if not i["frozen_matches_live"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
                "frozen_mismatch": len(frozen_bad),
            },
        }


@router.get("/api/reconcile/{cage_code}")
def reconcile_one(cage_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        cage = conn.execute(
            "SELECT * FROM cages WHERE cage_code=?", (cage_code,)
        ).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        return _reconcile_cage(conn, cage)


@router.get("/api/proof/{party_key}")
def party_proof(party_key: str, authorization: AuthHeader = None):
    """当事人级互证：账户冻结 vs 活笼可用合计（真实比对）。"""
    require_user(authorization)
    with connect() as conn:
        acct = conn.execute(
            "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
        ).fetchone()
        if not acct:
            raise HTTPException(404, "保证金户不存在")
        frozen_ok, frozen_proof = _frozen_matches(conn, party_key, acct)
        released = conn.execute(
            """SELECT COALESCE(SUM(r.amount),0) s FROM releases r
               JOIN cages c ON c.id=r.cage_id
               WHERE c.party_key=? AND r.status='已拨付'""",
            (party_key,),
        ).fetchone()["s"]
        forfeited = conn.execute(
            """SELECT COALESCE(SUM(f.amount),0) s FROM forfeits f
               JOIN cages c ON c.id=f.cage_id
               WHERE c.party_key=? AND f.status='已罚没'""",
            (party_key,),
        ).fetchone()["s"]
        deducted = conn.execute(
            """SELECT COALESCE(SUM(r.frozen_deducted),0) s FROM releases r
               JOIN cages c ON c.id=r.cage_id
               WHERE c.party_key=? AND r.status='已拨付'""",
            (party_key,),
        ).fetchone()["s"]
        return {
            "party_key": party_key,
            "account": dict(acct),
            "live_cage_available": frozen_proof["right"],
            "released_total": float(released),
            "forfeited_total": float(forfeited),
            "release_frozen_deducted_total": float(deducted),
            "frozen_matches_live": frozen_ok,
            "proof": frozen_proof,
        }
