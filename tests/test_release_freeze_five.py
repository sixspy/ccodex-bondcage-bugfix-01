"""BUGFIX-01 五面收工核验：解笼 / 账户 / 对账 / 入笼 / 审计 同降。

另含业务失败与争用失败两段（与命运取舍分开验收）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "frontend/js/views"


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


def _seed(c: TestClient, h: dict) -> dict:
    cage = next(
        x
        for x in c.get("/api/cages", headers=h).json()["items"]
        if x["cage_code"] == "CG-P-EAST-001"
    )
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": cage["id"], "outcome": "通过", "evidence": "五面核验"},
        ).status_code
        == 200
    )
    return cage


def test_five_pages_render_contract():
    rel = (VIEWS / "release_view.js").read_text(encoding="utf-8")
    acc = (VIEWS / "bonds_view.js").read_text(encoding="utf-8")
    rec = (VIEWS / "reconcile_view.js").read_text(encoding="utf-8")
    cag = (VIEWS / "cages_view.js").read_text(encoding="utf-8")
    aud = (VIEWS / "audit_view.js").read_text(encoding="utf-8")
    for text, needle in (
        (rel, "冻结扣减"),
        (rel, "出金扣冻偏航"),
        (rel, "甲路径"),
        (acc, "冻结互证"),
        (acc, "解笼扣冻留痕"),
        (acc, "活笼可用"),
        (rec, "冻结互证"),
        (rec, "解笼冻结扣减合计"),
        (cag, "冻结一致"),
        (aud, "出金扣冻偏航"),
    ):
        assert needle in text, f"页面缺契约：{needle}"


def test_account_page_detail_exposes_release_freeze_rows():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    assert c.post("/api/releases", headers=h, json={"cage_id": cage["id"]}).status_code == 200
    detail = c.get("/api/bonds/P-EAST", headers=h).json()
    row = detail["release_freeze"][0]
    assert row["amount"] == pytest.approx(120000)
    assert row["frozen_deducted"] == pytest.approx(120000)
    assert row["account_frozen_after"] == pytest.approx(0)
    assert detail["account"]["frozen_matches_live"] is True


def test_cages_page_detail_exposes_frozen_match():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    assert c.post("/api/releases", headers=h, json={"cage_id": cage["id"]}).status_code == 200
    detail = c.get(f"/api/cages/{cage['id']}", headers=h).json()
    assert detail["account"]["frozen"] == pytest.approx(0)
    assert detail["account"]["live_cage_available"] == pytest.approx(0)
    assert detail["account"]["frozen_matches_live"] is True
    assert detail["releases"][0]["frozen_deducted"] == pytest.approx(120000)


def test_reconcile_summary_counts_frozen_mismatch_zero_after_release():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    assert c.post("/api/releases", headers=h, json={"cage_id": cage["id"]}).status_code == 200
    recon = c.get("/api/reconcile", headers=h).json()
    assert recon["summary"]["frozen_mismatch"] == 0
    item = next(i for i in recon["items"] if i["cage_id"] == cage["id"])
    assert item["frozen_matches_live"] is True
    assert item["frozen_proof"]["left"] == pytest.approx(item["frozen_proof"]["right"])


def test_partial_release_deducts_proportionally():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    r = c.post(
        "/api/releases", headers=h, json={"cage_id": cage["id"], "amount": 50000}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frozen_deducted"] == pytest.approx(50000)
    assert body["account_frozen_after"] == pytest.approx(70000)
    acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert acct["frozen"] == pytest.approx(70000)
    assert acct["live_cage_available"] == pytest.approx(70000)
    assert acct["frozen_matches_live"] is True
    cage_after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert cage_after["available"] == pytest.approx(70000)
    assert cage_after["status"] == "部分解笼"


def test_deviation_leaves_no_frozen_drop_and_is_visible():
    c = TestClient(app)
    h = _auth(c)
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={
                "party_key": "P-DEV",
                "party_name": "偏航测试",
                "initial_deposit": 50000,
            },
        ).status_code
        == 200
    )
    cage = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-DEV",
            "contract_no": "HT-DEV-1",
            "amount": 20000,
            "beneficiary": "偏航受益人",
            "cage_code": "CG-P-DEV-001",
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
    with connect() as conn:
        conn.execute("UPDATE bond_accounts SET frozen=frozen-5000 WHERE party_key='P-DEV'")
        conn.commit()
    before = c.get("/api/bonds/P-DEV", headers=h).json()["account"]
    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 409
    assert "出金扣冻偏航" in r.json()["detail"]
    after = c.get("/api/bonds/P-DEV", headers=h).json()["account"]
    assert after["frozen"] == pytest.approx(before["frozen"])
    devs = c.get("/api/audit/deviations", headers=h).json()
    assert len(devs["items"]) == 1
    assert devs["items"][0]["payload"]["ledger_written"] is False
    assert devs["items"][0]["payload"]["attempt_amount"] == pytest.approx(20000)
    assert c.get("/api/releases", headers=h).json()["items"] == []


def test_contention_second_writer_writes_nothing_even_after_retry():
    c = TestClient(app)
    ha = _auth(c, "admin")
    hc = _auth(c, "clerk")
    cage = _seed(c, ha)
    lk = f"cage:{cage['id']}"
    assert c.post("/api/locks/acquire", headers=ha, json={"lock_key": lk}).status_code == 200
    before = c.get("/api/bonds/P-EAST", headers=ha).json()["account"]
    assert c.post("/api/releases", headers=hc, json={"cage_id": cage["id"]}).status_code == 409
    assert c.post("/api/releases", headers=hc, json={"cage_id": cage["id"]}).status_code == 409
    cont = c.get("/api/contention", headers=ha).json()["items"]
    assert len([x for x in cont if x["lock_key"] == lk and x["actor"] == "clerk"]) == 2
    after = c.get("/api/bonds/P-EAST", headers=ha).json()["account"]
    assert after["frozen"] == pytest.approx(before["frozen"])
    assert c.get("/api/releases", headers=ha).json()["items"] == []
    # 持锁者释放后，后到方可正式出金且扣冻同额
    assert c.post("/api/locks/release", headers=ha, json={"lock_key": lk}).status_code == 200
    ok = c.post("/api/releases", headers=hc, json={"cage_id": cage["id"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["frozen_deducted"] == pytest.approx(120000)


def test_frozen_proof_turns_red_on_mismatch():
    """互证灯按真实冻结计算：冻结与活笼可用不等时必须报失衡。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    ok = c.get("/api/proof/P-EAST", headers=h).json()
    assert ok["frozen_matches_live"] is True
    with connect() as conn:
        conn.execute("UPDATE bond_accounts SET frozen=frozen-1 WHERE party_key='P-EAST'")
        conn.commit()
    bad = c.get("/api/proof/P-EAST", headers=h).json()
    assert bad["frozen_matches_live"] is False
    assert bad["proof"]["left"] != bad["proof"]["right"]
    bond = next(
        b
        for b in c.get("/api/bonds", headers=h).json()["items"]
        if b["party_key"] == "P-EAST"
    )
    assert bond["frozen_matches_live"] is False
    recon = c.get(f"/api/reconcile/{cage['cage_code']}", headers=h).json()
    assert recon["frozen_matches_live"] is False
    summary = c.get("/api/reconcile", headers=h).json()["summary"]
    assert summary["frozen_mismatch"] >= 1
    # 拨付仍被拒：冻结不足以扣减，正式零改
    r = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert r.status_code == 409


def test_release_audit_payload_matches_ledger_and_account():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed(c, h)
    assert c.post("/api/releases", headers=h, json={"cage_id": cage["id"]}).status_code == 200
    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    entry = next(a for a in audits if a["action"] == "cage_release")
    payload = json.loads(entry["payload"])
    rel = c.get("/api/releases", headers=h).json()["items"][0]
    acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert payload["frozen_deducted"] == rel["frozen_deducted"] == rel["amount"]
    assert payload["account_frozen_after"] == rel["account_frozen_after"] == acct["frozen"]
    assert payload["path"] == "甲"
