"""制造计算（开明网式）服务 —— 造买开关、自采不计价、作业费口径、边角料抵扣、批量购买文本。

真实库实测（2026-11-17，库副本，乌鸦级 ME10 / NPC 站 / 指数 0.14%）：原料 2.76 亿、作业费 3116 万
（制造 3008 万 + 反应 108 万，其中 SCC 2839 万）、总成本 3.08 亿，与开明网 2.76 亿 / 3110 万 / 3.07 亿一致。
这里用合成小 BOM 锁口径。
"""

from __future__ import annotations

import pytest

from services import industry_calc_service as ics

#: 成品 5001（制造，1/轮）吃 5002×10 + 矿 1001×100；5002 = 反应（200/轮）吃卫星原料 5003×100
_BOM = [
    (6001, "manufacturing", 3600, 5001, 1, [(5002, 10), (1001, 100)]),
    (6002, "reaction", 10800, 5002, 200, [(5003, 100)]),
]


@pytest.fixture
def calc_db(temp_db):
    with temp_db.connect("ref") as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(item)")}
        if "group_id" not in cols:
            conn.execute("ALTER TABLE item ADD COLUMN group_id INTEGER")
        conn.executemany(
            "INSERT OR REPLACE INTO item (type_id, zh_name, en_name, volume, group_id) VALUES (?,?,?,1,?)",
            [
                (5001, "成品", "P", None),
                (5002, "反应物", "R", 429),
                (5003, "卫星原料", "M", 427),
                (1001, "三钛合金", "T", 18),
            ],
        )
    with temp_db.connect("bp") as conn:
        for bp, act, t, prod, out, mats in _BOM:
            conn.execute("INSERT INTO blueprint_activities VALUES (?,?,?)", (bp, act, t))
            conn.execute("INSERT INTO blueprint_products VALUES (?,?,?,?)", (bp, act, prod, out))
            for m, q in mats:
                conn.execute("INSERT INTO blueprint_materials VALUES (?,?,?,?,10)", (bp, act, m, q))
    with temp_db.connect("mkt") as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO market_prices VALUES (?, 10000002, ?, ?, ?, 1000, 1000, '2026-01-01 00:00:00')",
            [(5001, 900_000, 1_000_000, 0), (5002, 400, 500, 450), (5003, 50, 60, 55)],
        )
    return temp_db


def _run(db, **kw):
    req = ics.CalcRequest(
        targets=[(5001, 4)],
        me_level=0,
        price_hub="Jita",
        manufacturing=ics.Facility(cost_index=0.01),
        reaction=ics.Facility(cost_index=0.02),
        **kw,
    )
    return ics.run(req, db=db)


def test_reaction_built_moon_free_vs_all_bought(calc_db):
    built = _run(calc_db)  # 默认：反应自己做、卫星原料自采不计价
    assert {j.type_id: (j.runs, j.leftover) for j in built.jobs} == {5001: (4, 0), 5002: (1, 160)}
    raws = {r.type_id: r for r in built.raws}
    assert (raws[5003].qty, raws[5003].free, raws[5003].cost) == (100, True, 0.0)
    assert raws[1001].cost == 400 * 5.0
    assert built.material_cost == 2000.0
    # 作业费：制造 EIV = (10×450 + 100×5)×4 = 20000 → ×(1% + 0.25% + 4%)；反应 EIV = 100×55 = 5500 → ×(2% + 0.25% + 4%)
    assert built.fee_manufacturing == pytest.approx(20_000 * 0.0525, abs=0.01)
    assert built.fee_reaction == pytest.approx(5_500 * 0.0625, abs=0.01)
    # 边角料 160 个反应物：自制单价 = 作业费 / 200（原料自采不计价）< 卖价 500 → 按自制价抵扣
    assert built.leftover_credit == pytest.approx(160 * (5_500 * 0.0625 / 200), abs=0.01)
    assert ics.multibuy_text(built.raws) == "三钛合金\t400"

    bought = _run(calc_db, toggles={**ics.DEFAULT_TOGGLES, ics.TOGGLE_REACTION: False})
    assert [j.type_id for j in bought.jobs] == [5001]
    assert {r.type_id: r.qty for r in bought.raws} == {1001: 400, 5002: 40}
    assert bought.material_cost == 400 * 5.0 + 40 * 500.0

    # 逐项覆盖优先于类别开关
    forced = _run(calc_db, toggles={**ics.DEFAULT_TOGGLES, ics.TOGGLE_REACTION: False}, overrides={5002: True})
    assert 5002 in {j.type_id for j in forced.jobs}


def test_revenue_after_taxes(calc_db):
    r = _run(calc_db, sales_tax_pct=3.6, broker_pct=1.5)
    assert r.revenue_sell == pytest.approx(4 * 1_000_000 * (1 - 0.036 - 0.015))
    assert r.revenue_buy == pytest.approx(4 * 900_000 * (1 - 0.036))
