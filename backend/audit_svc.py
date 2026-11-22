"""审计只读面。"""
from __future__ import annotations

import json

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["audit"])

FATE_LABEL = "出金扣冻偏航"
DEVIATION_ACTION = "release_freeze_deviation"


@router.get("/api/audit")
def list_audit(authorization: AuthHeader = None, limit: int = 100):
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/actions")
def list_actions(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT action, COUNT(*) c FROM audit_trail
               GROUP BY action ORDER BY c DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/audit/deviations")
def list_deviations(authorization: AuthHeader = None, limit: int = 100):
    """出金扣冻偏航：任何一条都说明甲路径被破，须人工处置。"""
    require_user(authorization)
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM audit_trail WHERE action=?
               ORDER BY id DESC LIMIT ?""",
            (DEVIATION_ACTION, limit),
        ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        try:
            item["payload"] = json.loads(item["payload"]) if item["payload"] else {}
        except (TypeError, ValueError):
            item["payload"] = {"raw": item["payload"]}
        items.append(item)
    return {
        "items": items,
        "label": FATE_LABEL,
        "fate": {
            "path": "甲",
            "doctrine": "拨付入册则账户冻结必同步扣减；否则正式零改、记偏航并拒写",
            "locked_on": "2026-11-22",
        },
    }
