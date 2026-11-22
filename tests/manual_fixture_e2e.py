"""本机夹具端到端：真服务 + data/app.db 副本，验甲路径与争用。"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:10608"
DB = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/data/app.db")


def call(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def login(user):
    st, b = call("/api/login", "POST", {"username": user, "password": "123456"})
    assert st == 200, (st, b)
    return b["token"]


def q(sql, args=()):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def main() -> int:
    ha, hc = login("admin"), login("clerk")
    bonds = call("/api/bonds", token=ha)[1]["items"]
    east = next(b for b in bonds if b["party_key"] == "P-EAST")
    print("seed account:", {k: east[k] for k in ("balance", "frozen", "live_cage_available", "frozen_matches_live")})
    assert east["frozen_matches_live"] is True, "种子互证须真绿"

    cage = next(c for c in call("/api/cages", token=ha)[1]["items"] if c["cage_code"] == "CG-P-EAST-001")
    st, att = call("/api/attestations", "POST", {"cage_id": cage["id"], "outcome": "通过", "evidence": "本机夹具"}, ha)
    assert st == 200, (st, att)

    # 争用段：admin 持锁，clerk 后到只记争用
    st, _ = call("/api/locks/acquire", "POST", {"lock_key": f"cage:{cage['id']}"}, ha)
    assert st == 200
    before = call(f"/api/bonds/P-EAST", token=ha)[1]["account"]
    st, body = call("/api/releases", "POST", {"cage_id": cage["id"]}, hc)
    assert st == 409 and "争用" in json.dumps(body, ensure_ascii=False), (st, body)
    cont = [x for x in call("/api/contention", token=ha)[1]["items"] if x["lock_key"] == f"cage:{cage['id']}"]
    assert cont and cont[0]["actor"] == "clerk"
    assert call("/api/releases", token=ha)[1]["items"] == []
    mid = call("/api/bonds/P-EAST", token=ha)[1]["account"]
    assert mid["frozen"] == before["frozen"], "争用后冻结不得动"
    print("contention ok:", cont[0])

    # 命运取舍段：释放锁后正式出金，冻结同降
    assert call("/api/locks/release", "POST", {"lock_key": f"cage:{cage['id']}"}, ha)[0] == 200
    st, rel = call("/api/releases", "POST", {"cage_id": cage["id"]}, ha)
    assert st == 200, (st, rel)
    print("release:", {k: rel[k] for k in ("release_ref", "amount", "frozen_deducted", "account_frozen_after")})
    assert rel["frozen_deducted"] == rel["amount"]

    after = call("/api/bonds/P-EAST", token=ha)[1]["account"]
    proof = call("/api/proof/P-EAST", token=ha)[1]
    detail = call("/api/bonds/P-EAST", token=ha)[1]
    cage_after = call(f"/api/cages/{cage['id']}", token=ha)[1]
    recon = call(f"/api/reconcile/{cage['cage_code']}", token=ha)[1]
    devs = call("/api/audit/deviations", token=ha)[1]
    audits = call("/api/audit?limit=200", token=ha)[1]["items"]
    entry = next(a for a in audits if a["action"] == "cage_release")
    payload = json.loads(entry["payload"])

    checks = {
        "账户冻结同降": after["frozen"] == east["frozen"] - rel["amount"],
        "账户余额同降": after["balance"] == east["balance"] - rel["amount"],
        "活笼可用同降": after["live_cage_available"] == proof["live_cage_available"] == 0,
        "账户互证真绿": after["frozen_matches_live"] is True and proof["frozen_matches_live"] is True,
        "账户扣冻留痕": detail["release_freeze"][0]["frozen_deducted"] == rel["amount"],
        "解笼页行": [r for r in call("/api/releases", token=ha)[1]["items"] if r["cage_id"] == cage["id"]][0]["frozen_deducted"] == rel["amount"],
        "对账笼平衡": recon["balanced"] is True,
        "对账冻结一致": recon["frozen_matches_live"] is True,
        "入笼页冻结一致": cage_after["account"]["frozen_matches_live"] is True,
        "入笼页可用归零": cage_after["cage"]["available"] == 0 and cage_after["cage"]["status"] == "已解笼",
        "审计载荷同降": payload["frozen_deducted"] == rel["amount"] and payload["account_frozen_after"] == after["frozen"],
        "审计路径甲": payload["path"] == "甲",
        "无偏航": devs["items"] == [] and devs["fate"]["path"] == "甲",
        "库内留痕": q("SELECT frozen_deducted,account_frozen_after FROM releases WHERE cage_id=?", (cage["id"],))[0]["frozen_deducted"] == rel["amount"],
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    if not all(checks.values()):
        return 1

    # 业务失败段：另开当事人，扣冻不可能时零改 + 偏航
    st, b = call("/api/bonds", "POST", {"party_key": "P-LOCALFAIL", "party_name": "本机偏航", "initial_deposit": 50000}, ha)
    assert st == 200, (st, b)
    st, cg = call("/api/cages/freeze", "POST", {"party_key": "P-LOCALFAIL", "contract_no": "HT-LOCALFAIL", "amount": 20000, "beneficiary": "偏航受益人", "cage_code": "CG-P-LOCALFAIL-001"}, ha)
    assert st == 200, (st, cg)
    assert call("/api/attestations", "POST", {"cage_id": cg["id"], "outcome": "通过", "evidence": "本机"}, ha)[0] == 200
    con = sqlite3.connect(DB)
    con.execute("UPDATE bond_accounts SET frozen=frozen-1000 WHERE party_key='P-LOCALFAIL'")
    con.commit()
    con.close()
    b0 = call("/api/bonds/P-LOCALFAIL", token=ha)[1]["account"]
    cg0 = call(f"/api/cages/{cg['id']}", token=ha)[1]["cage"]
    rels0 = len(call("/api/releases", token=ha)[1]["items"])
    st, body = call("/api/releases", "POST", {"cage_id": cg["id"]}, ha)
    b1 = call("/api/bonds/P-LOCALFAIL", token=ha)[1]["account"]
    cg1 = call(f"/api/cages/{cg['id']}", token=ha)[1]["cage"]
    rels1 = len(call("/api/releases", token=ha)[1]["items"])
    devs1 = call("/api/audit/deviations", token=ha)[1]["items"]
    fail_checks = {
        "拒写 409": st == 409 and "出金扣冻偏航" in json.dumps(body, ensure_ascii=False),
        "拨付册零增": rels1 == rels0,
        "笼零改": cg1["available"] == cg0["available"] and cg1["status"] == cg0["status"],
        "账户零改": b1["balance"] == b0["balance"] and b1["frozen"] == b0["frozen"],
        "偏航入审计": any(d["payload"]["ledger_written"] is False for d in devs1),
    }
    for k, v in fail_checks.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(fail_checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
