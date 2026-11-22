"""BUGFIX-01：出金扣冻命运红测。

口径（2026-11-22 锁死，仅甲路径）：
  拨付入册则账户冻结必同步扣减；扣冻失败整笔正式零改，
  记「出金扣冻偏航」并拒写。后到争用只记争用。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VIEW = ROOT / "frontend/js/views/release_view.js"
BONDS_VIEW = ROOT / "frontend/js/views/bonds_view.js"
RECONCILE_VIEW = ROOT / "frontend/js/views/reconcile_view.js"
CAGES_VIEW = ROOT / "frontend/js/views/cages_view.js"
AUDIT_VIEW = ROOT / "frontend/js/views/audit_view.js"
FATE_MD = ROOT / "ops/出金扣冻命运说明.md"
FATE_JSON = ROOT / "ops/出金扣冻命运说明.json"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _seed_releasable(c: TestClient, h: dict) -> dict:
    """可出金种子：已冻结笼 → 核验通过，随后可整额解笼。"""
    cage = next(
        x
        for x in c.get("/api/cages", headers=h).json()["items"]
        if x["cage_code"] == "CG-P-EAST-001"
    )
    r = c.post(
        "/api/attestations",
        headers=h,
        json={"cage_id": cage["id"], "outcome": "通过", "evidence": "红测种子"},
    )
    assert r.status_code == 200, r.text
    return cage


def _release_count(cage_id: int) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM releases WHERE cage_id=?", (cage_id,)
        ).fetchone()["c"]


# --------------------------------------------------------------------------
# 红测甲：出金凭证入册后，当事人冻结额必须同步扣减（余额与冻结同降）
# --------------------------------------------------------------------------
def test_red_a_release_deducts_frozen():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_releasable(c, h)
    before = c.get("/api/bonds/P-EAST", headers=h).json()["account"]

    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 200, r.text

    after = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert before["frozen"] - after["frozen"] == pytest.approx(120000)
    assert before["balance"] - after["balance"] == pytest.approx(120000)


# --------------------------------------------------------------------------
# 红测乙：拨付册有行则冻结必降；互证须按真实冻结核验（不许假绿）
# --------------------------------------------------------------------------
def test_red_b_release_row_implies_frozen_drop():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_releasable(c, h)

    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 200, r.text
    rows = [
        x
        for x in c.get("/api/releases", headers=h).json()["items"]
        if x["cage_id"] == cage["id"]
    ]
    assert len(rows) == 1
    assert rows[0]["frozen_deducted"] == pytest.approx(120000)
    assert rows[0]["account_frozen_after"] == pytest.approx(0)

    acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert proof["live_cage_available"] == pytest.approx(0)
    # 拨付已成、笼可用已减，冻结也必须降到活笼可用（互证真绿）
    assert acct["frozen"] == pytest.approx(proof["live_cage_available"])
    assert proof["frozen_matches_live"] is True
    assert proof["proof"]["left"] == pytest.approx(proof["proof"]["right"])


# --------------------------------------------------------------------------
# 业务失败红测：扣冻不可能时必须正式零改，并记偏航拒写
# --------------------------------------------------------------------------
def test_red_business_failure_is_zero_change_and_deviation():
    c = TestClient(app)
    h = _auth(c)
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": "P-REDFAIL",
                "party_name": "扣冻失败测试",
                "initial_deposit": 100000,
            },
        ).status_code
        == 200
    )
    cage = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-REDFAIL",
            "contract_no": "HT-REDFAIL-1",
            "amount": 30000,
            "beneficiary": "失败受益人",
            "cage_code": "CG-P-REDFAIL-001",
        },
    ).json()
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": cage["id"], "outcome": "通过", "evidence": "可出金"},
        ).status_code
        == 200
    )
    # 制造扣冻不可能：账户冻结被清零（偏离命运说明的输入）
    with connect() as conn:
        conn.execute(
            "UPDATE bond_accounts SET frozen=0 WHERE party_key='P-REDFAIL'"
        )
        conn.commit()

    before_acct = c.get("/api/bonds/P-REDFAIL", headers=h).json()["account"]
    before_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    before_rows = _release_count(cage["id"])

    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 409, r.text
    assert "偏航" in r.text

    after_acct = c.get("/api/bonds/P-REDFAIL", headers=h).json()["account"]
    after_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert _release_count(cage["id"]) == before_rows
    assert after_cage["available"] == pytest.approx(before_cage["available"])
    assert after_cage["status"] == before_cage["status"]
    assert after_acct["balance"] == pytest.approx(before_acct["balance"])
    assert after_acct["frozen"] == pytest.approx(before_acct["frozen"])

    devs = [
        a
        for a in c.get("/api/audit?limit=200", headers=h).json()["items"]
        if a["action"] == "release_freeze_deviation"
    ]
    assert devs
    payload = json.loads(devs[0]["payload"])
    assert payload["label"] == "出金扣冻偏航"
    assert payload["path"] == "甲"


# --------------------------------------------------------------------------
# 争用失败红测：同笼双人并发出金，后到只记争用、不写业务
# --------------------------------------------------------------------------
def test_red_c_same_cage_concurrent_release_contention_only():
    c = TestClient(app)
    ha = _auth(c, "admin")
    hc = _auth(c, "clerk")
    cage = _seed_releasable(c, ha)
    lock_key = f"cage:{cage['id']}"

    # admin 的另一笔同笼出金在途（持有笼锁）
    assert (
        c.post(
            "/api/locks/acquire", headers=ha, json={"lock_key": lock_key}
        ).status_code
        == 200
    )
    before_acct = c.get("/api/bonds/P-EAST", headers=ha).json()["account"]
    before_cage = c.get(f"/api/cages/{cage['id']}", headers=ha).json()["cage"]
    before_rows = _release_count(cage["id"])

    r = c.post("/api/releases", headers=hc, json={"cage_id": cage["id"]})
    assert r.status_code == 409, r.text
    assert "争用" in r.text

    cont = [
        x
        for x in c.get("/api/contention", headers=ha).json()["items"]
        if x["lock_key"] == lock_key
    ]
    assert cont, "后到争用必须落库"
    assert cont[0]["actor"] == "clerk"
    assert cont[0]["action"] == "争用"

    after_acct = c.get("/api/bonds/P-EAST", headers=ha).json()["account"]
    after_cage = c.get(f"/api/cages/{cage['id']}", headers=ha).json()["cage"]
    assert _release_count(cage["id"]) == before_rows
    assert after_acct["frozen"] == pytest.approx(before_acct["frozen"])
    assert after_cage["available"] == pytest.approx(before_cage["available"])


# --------------------------------------------------------------------------
# 命运说明：选定甲路径、当日锁死、禁止第三路
# --------------------------------------------------------------------------
def test_fate_spec_chosen_and_locked():
    assert FATE_MD.exists(), "须写入出金扣冻命运说明"
    assert FATE_JSON.exists(), "须有机器可读的命运锁"
    spec = json.loads(FATE_JSON.read_text(encoding="utf-8"))
    assert spec["path"] == "甲"
    assert spec["locked_on"] == "2026-11-22"
    assert spec["deviation_action"] == "release_freeze_deviation"
    assert spec["deviation_label"] == "出金扣冻偏航"
    # 指纹按 LF 规范化后计算，换行风格不影响，内容一改即失效
    norm = FATE_MD.read_bytes().replace(b"\r\n", b"\n")
    assert spec["spec_sha256"] == hashlib.sha256(norm).hexdigest(), "命运说明已被改动，锁失效"
    assert "选定路径：甲" in FATE_MD.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 页面契约：解笼页与账户页必须同屏呈现拨付与冻结同降
# --------------------------------------------------------------------------
def test_pages_show_release_and_frozen_drop_together():
    rel = RELEASE_VIEW.read_text(encoding="utf-8")
    acc = BONDS_VIEW.read_text(encoding="utf-8")
    assert "甲路径" in rel
    assert "出金扣冻偏航" in rel
    assert "冻结扣减" in rel
    assert "活笼可用" in acc
    assert "冻结互证" in acc
    assert "冻结扣减" in acc


# --------------------------------------------------------------------------
# 收工核验：解笼 / 账户 / 对账 / 入笼 / 审计 五面同降
# --------------------------------------------------------------------------
def test_five_surfaces_release_and_frozen_drop_together():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_releasable(c, h)

    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frozen_deducted"] == pytest.approx(120000)
    assert body["account_frozen_after"] == pytest.approx(0)

    # 解笼页
    rel = next(
        x
        for x in c.get("/api/releases", headers=h).json()["items"]
        if x["cage_id"] == cage["id"]
    )
    assert rel["frozen_deducted"] == pytest.approx(rel["amount"])

    # 账户页
    bond = next(
        x
        for x in c.get("/api/bonds", headers=h).json()["items"]
        if x["party_key"] == "P-EAST"
    )
    assert bond["frozen"] == pytest.approx(0)
    assert bond["live_cage_available"] == pytest.approx(0)
    assert bond["frozen_matches_live"] is True
    detail = c.get("/api/bonds/P-EAST", headers=h).json()
    assert detail["release_freeze"][0]["frozen_deducted"] == pytest.approx(120000)

    # 对账页
    proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert proof["frozen_matches_live"] is True
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    assert recon["balanced"] is True
    assert recon["frozen_matches_live"] is True

    # 入笼页
    after_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after_cage["available"] == pytest.approx(0)
    assert after_cage["status"] == "已解笼"

    # 审计页
    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    entry = next(
        a for a in audits if a["action"] == "cage_release" and a["target"] == cage["cage_code"]
    )
    payload = json.loads(entry["payload"])
    assert payload["frozen_deducted"] == pytest.approx(120000)
    assert payload["account_frozen_after"] == pytest.approx(0)
    devs = c.get("/api/audit/deviations", headers=h).json()
    assert devs["items"] == []
    assert devs["fate"]["path"] == "甲"
