"""解笼拨付：核验通过后把笼内可用额拨付给受益人。

BUGFIX-01 口径（2026-11-22 锁定，见 ops/出金扣冻命运说明.md）：
  甲路径——拨付入册则账户冻结必同步扣减；扣冻不成则整笔正式零改，
  只记「出金扣冻偏航」并拒写。禁止乙路径与第三路混用。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import ReleaseIn

router = APIRouter(tags=["release"])

EPS = 1e-9


def _reject_deviation(conn, user: str, cage, amount: float, reason: str) -> None:
    """先回滚任何正式写入，再落偏航审计并拒写。"""
    conn.rollback()
    write_audit(
        conn,
        user,
        "release_freeze_deviation",
        cage["cage_code"],
        {
            "label": "出金扣冻偏航",
            "path": "甲",
            "cage_id": int(cage["id"]),
            "cage_generation": int(cage["generation"]),
            "attempt_amount": float(amount),
            "reason": reason,
            "ledger_written": False,
        },
    )
    conn.commit()
    raise HTTPException(409, f"出金扣冻偏航：{reason}，拨付册零增并拒写")


@router.get("/api/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, c.cage_code, c.contract_no
               FROM releases r
               JOIN cages c ON c.id=r.cage_id
               ORDER BY r.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/releases")
def do_release(body: ReleaseIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"cage:{body.cage_id}"
    conn = connect()
    try:
        require_lock(conn, lock_key, user)
        cage = conn.execute(
            "SELECT * FROM cages WHERE id=?", (body.cage_id,)
        ).fetchone()
        if not cage:
            raise HTTPException(404, "笼不存在")
        if cage["status"] != "核验通过":
            raise HTTPException(409, "仅核验通过的笼可解笼拨付")
        # 核验结论必须对齐当前笼代
        att = conn.execute(
            """SELECT * FROM attestations
               WHERE cage_id=? AND attest_gen=? AND outcome='通过'
               ORDER BY id DESC LIMIT 1""",
            (body.cage_id, int(cage["generation"])),
        ).fetchone()
        if not att:
            raise HTTPException(409, "缺少与笼代对齐的通过核验")
        avail = float(cage["available"])
        amount = float(body.amount) if body.amount is not None else avail
        if amount <= 0:
            raise HTTPException(400, "拨付金额须大于 0")
        if amount > avail + EPS:
            raise HTTPException(409, "拨付金额超过笼可用额")
        payee = body.payee or cage["beneficiary"]
        party = cage["party_key"]
        now = now_iso()
        gen = int(cage["generation"])
        ref = f"REL-{cage['cage_code']}-g{gen}-{int(now.replace(':', '').replace('-', '').replace('T', '')[-8:])}"

        # 甲路径闸门：先做扣冻条件更新。冻结不足即视为扣冻失败，
        # 此时尚未写任何正式账，回滚后只记偏航。
        gate = conn.execute(
            """UPDATE bond_accounts
               SET frozen = frozen - ?, balance = balance - ?
               WHERE party_key=? AND frozen + ? >= ?""",
            (amount, amount, party, EPS, amount),
        )
        if gate.rowcount != 1:
            acct = conn.execute(
                "SELECT frozen FROM bond_accounts WHERE party_key=?", (party,)
            ).fetchone()
            have = float(acct["frozen"]) if acct else 0.0
            _reject_deviation(
                conn,
                user,
                cage,
                amount,
                f"账户冻结 {have:.2f} 不足以扣减 {amount:.2f}",
            )

        after = conn.execute(
            "SELECT frozen FROM bond_accounts WHERE party_key=?", (party,)
        ).fetchone()
        frozen_after = round(float(after["frozen"]), 6)

        cur = conn.execute(
            """INSERT INTO releases(cage_id,amount,payee,cage_generation,status,
               release_ref,frozen_deducted,account_frozen_after,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                body.cage_id,
                amount,
                payee,
                gen,
                "已拨付",
                ref,
                amount,
                frozen_after,
                now,
            ),
        )
        new_avail = round(avail - amount, 6)
        new_status = "已解笼" if new_avail <= EPS else "部分解笼"
        conn.execute(
            "UPDATE cages SET available=?, status=? WHERE id=?",
            (max(new_avail, 0.0), new_status, body.cage_id),
        )
        write_audit(
            conn,
            user,
            "cage_release",
            cage["cage_code"],
            {
                "release_id": cur.lastrowid,
                "amount": amount,
                "payee": payee,
                "cage_generation": gen,
                "release_ref": ref,
                "frozen_deducted": amount,
                "account_frozen_after": frozen_after,
                "path": "甲",
            },
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM releases WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        release(conn, lock_key, user)
        conn.commit()
        conn.close()
