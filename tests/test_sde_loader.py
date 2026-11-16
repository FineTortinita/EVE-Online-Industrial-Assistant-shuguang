"""SDE 扩展数据加载器测试 — services/workers/sde_loader.py

测试覆盖:
  - initialize_database 建表
  - write_meta_groups 写入 meta_group 表
  - write_meta_groups 更新 item.meta_group_id
  - write_meta_groups 幂等性
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from services.importers.sde_loader import (
    initialize_database,
    write_meta_groups,
    write_universe,
)

# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def temp_db_path():
    """创建临时 reference.db，返回路径字符串"""
    tmpdir = tempfile.mkdtemp(prefix="eve_test_sde_loader_")
    db_path = Path(tmpdir) / "reference.db"
    sqlite3.connect(str(db_path)).close()
    yield str(db_path)
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)


# ─── Helpers ─────────────────────────────────────────────


async def _ensure_item_table(db_path: str):
    """在测试数据库中创建 item 表（write_meta_groups 依赖它）"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS item (
                type_id INTEGER PRIMARY KEY,
                en_name TEXT, zh_name TEXT,
                group_id INTEGER,
                en_group_name TEXT, zh_group_name TEXT,
                market_group_id INTEGER,
                en_market_group_name TEXT, zh_market_group_name TEXT,
                volume REAL, iconID INTEGER,
                meta_group_id INTEGER, category_id INTEGER
            )
        """)
        await db.commit()


# ─── Test: initialize_database ─────────────────────────────


class TestInitializeDatabase:
    """initialize_database — SDE 扩展建表"""

    @pytest.mark.asyncio
    async def test_creates_meta_group_table(self, temp_db_path):
        """调用后 meta_group 表存在"""
        with patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path):
            await initialize_database()

        conn = sqlite3.connect(temp_db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "meta_group" in tables

    @pytest.mark.asyncio
    async def test_idempotent(self, temp_db_path):
        """重复调用不报错"""
        with patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path):
            await initialize_database()
            await initialize_database()


# ─── Test: write_meta_groups ───────────────────────────────


class TestWriteMetaGroups:
    """write_meta_groups — meta_group 表写入与 item.meta_group_id 更新"""

    @pytest.mark.asyncio
    async def test_creates_records(self, temp_db_path):
        """写入 meta_group 表并正确填充字段"""
        await _ensure_item_table(temp_db_path)  # write_meta_groups 需要 item 表存在

        mock_data = {
            "1": {"nameID": {"en": "Tech I", "zh": "科技 I"}},
            "2": {"nameID": {"en": "Tech II", "zh": "科技 II"}},
        }

        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.load_yaml_async", AsyncMock(return_value=mock_data)),
        ):
            await initialize_database()
            await write_meta_groups()

        conn = sqlite3.connect(temp_db_path)
        rows = conn.execute("SELECT * FROM meta_group ORDER BY meta_group_id").fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0] == (1, "Tech I", "科技 I")
        assert rows[1] == (2, "Tech II", "科技 II")

    @pytest.mark.asyncio
    async def test_skips_when_data_exists(self, temp_db_path):
        """meta_group 表已有数据时跳过"""
        await _ensure_item_table(temp_db_path)  # write_meta_groups 需要 item 表存在

        mock_data = {"1": {"nameID": {"en": "Tech I"}}}

        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.load_yaml_async", AsyncMock(return_value=mock_data)),
        ):
            await initialize_database()
            await write_meta_groups()  # 首次调用写入
            await write_meta_groups()  # 第二次应当跳过

        conn = sqlite3.connect(temp_db_path)
        count = conn.execute("SELECT COUNT(*) FROM meta_group").fetchone()[0]
        conn.close()
        assert count == 1

    @pytest.mark.asyncio
    async def test_updates_item_meta_group_id(self, temp_db_path):
        """同时更新 item.meta_group_id"""
        # 先创建 item 表并插入测试数据
        async with aiosqlite.connect(temp_db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS item (
                    type_id INTEGER PRIMARY KEY,
                    en_name TEXT, zh_name TEXT,
                    group_id INTEGER,
                    en_group_name TEXT, zh_group_name TEXT,
                    market_group_id INTEGER,
                    en_market_group_name TEXT, zh_market_group_name TEXT,
                    volume REAL, iconID INTEGER,
                    meta_group_id INTEGER, category_id INTEGER
                )
            """)
            for tid in (12345, 12346, 12347):
                await db.execute("INSERT INTO item (type_id, en_name) VALUES (?, ?)", (tid, f"Item_{tid}"))
            await db.commit()

        mock_meta = {
            "1": {"nameID": {"en": "Tech I", "zh": "科技 I"}},
            "2": {"nameID": {"en": "Tech II", "zh": "科技 II"}},
        }
        mock_type_ids = {
            "12345": {"name": {"en": "Item A"}, "groupID": 1, "metaGroupID": 1, "volume": 1.0},
            "12346": {"name": {"en": "Item B"}, "groupID": 1, "metaGroupID": 2, "volume": 1.0},
            "12347": {"name": {"en": "Item C"}, "groupID": 1, "volume": 1.0},
        }

        async def _load_yaml_side_effect(name):
            return {"metaGroups.yaml": mock_meta, "typeIDs.yaml": mock_type_ids}[name]

        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.load_yaml_async") as mock_load,
        ):
            mock_load.side_effect = _load_yaml_side_effect
            await initialize_database()
            await write_meta_groups()

        conn = sqlite3.connect(temp_db_path)
        rows = conn.execute("SELECT type_id, meta_group_id FROM item ORDER BY type_id").fetchall()
        conn.close()

        assert rows == [
            (12345, 1),
            (12346, 2),
            (12347, None),
        ]


# ─── Test: write_universe ─────────────────────────────────


class TestWriteUniverse:
    """write_universe — 以「是否存在非空星系名」为判空，全空名表触发补写"""

    @pytest.mark.asyncio
    async def test_repairs_when_names_all_empty(self, temp_db_path):
        """solar_system 表全空名 → 判空不跳过，用 universe 数据补名"""
        async with aiosqlite.connect(temp_db_path) as db:
            await db.execute(
                "CREATE TABLE solar_system (solar_system_id INTEGER PRIMARY KEY,"
                " solar_system_name TEXT, region_id INTEGER, constellation_id INTEGER, security REAL)"
            )
            await db.execute("INSERT INTO solar_system (solar_system_id, solar_system_name) VALUES (30000142, '')")
            await db.commit()

        fake = (
            [],
            [],
            [{"solar_system_id": 30000142, "solar_system_name": "Jita", "security": 0.9}],
            [],
        )
        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.ensure_universe_cache", new=AsyncMock(return_value=fake)),
        ):
            await write_universe()

        conn = sqlite3.connect(temp_db_path)
        name = conn.execute("SELECT solar_system_name FROM solar_system WHERE solar_system_id=30000142").fetchone()[0]
        conn.close()
        assert name == "Jita"

    @pytest.mark.asyncio
    async def test_skips_when_names_present(self, temp_db_path):
        """solar_system 表已有非空名 → 跳过（不重新解析 universe）"""
        async with aiosqlite.connect(temp_db_path) as db:
            await db.execute(
                "CREATE TABLE solar_system (solar_system_id INTEGER PRIMARY KEY, solar_system_name TEXT, security REAL)"
            )
            await db.execute("INSERT INTO solar_system (solar_system_id, solar_system_name) VALUES (30000142, 'Jita')")
            await db.commit()

        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.ensure_universe_cache", new=AsyncMock()) as m,
        ):
            await write_universe()

        m.assert_not_awaited()
        conn = sqlite3.connect(temp_db_path)
        name = conn.execute("SELECT solar_system_name FROM solar_system WHERE solar_system_id=30000142").fetchone()[0]
        conn.close()
        assert name == "Jita"  # 未被覆盖


# ─── Test: 新扁平源 station 名保护（02-03 B 段）────────────────


class TestStationNameProtection:
    """新源 npcStations.yaml 无 stationName：重导不得清空旧站名。"""

    @pytest.mark.asyncio
    async def test_new_source_keeps_existing_station_names(self, temp_db_path):
        """旧名保留（upsert COALESCE）+ 新源字段改名 typeID/ownerID 正确落库。"""
        from services.importers import sde_loader

        new_stations = {
            60000004: {"solarSystemID": 30002780, "operationID": 26, "typeID": 1531, "ownerID": 1000002},
            60000005: {"solarSystemID": 30000142, "operationID": 26, "typeID": 1531, "ownerID": 1000003},
        }
        yaml_by_name = {
            "staStations.yaml": new_stations,
            "stationServices.yaml": {1: {"serviceName": {"en": "Bounty Missions"}}},
            "stationOperations.yaml": {26: {"name": {"en": "Caldari Navy", "zh": "加达里海军"}, "services": [1]}},
        }

        async def fake_load(name):
            return yaml_by_name.get(name, {})

        with (
            patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
            patch("services.importers.sde_loader.load_yaml_async", side_effect=fake_load),
        ):
            await initialize_database()
            conn = sqlite3.connect(temp_db_path)
            conn.execute(
                "INSERT INTO station (station_id, station_name, solar_system_id) "
                "VALUES (60000004, '旧站名不要被清空', 30000142)"
            )
            conn.commit()
            conn.close()
            await sde_loader.write_stations()

        conn = sqlite3.connect(temp_db_path)
        kept = conn.execute(
            "SELECT station_name, solar_system_id, operation_id, station_type_id, corporation_id "
            "FROM station WHERE station_id = 60000004"
        ).fetchone()
        unnamed = conn.execute("SELECT station_name FROM station WHERE station_id = 60000005").fetchone()
        conn.close()
        assert kept[0] == "旧站名不要被清空", "新源缺 stationName 时不得覆盖已有站名"
        assert kept[1:] == (30002780, 26, 1531, 1000002)
        assert unnamed[0] == "", "新站没有名字就留空，不伪造"


@pytest.mark.asyncio
async def test_stargates_feed_the_jump_graph(temp_db_path, tmp_path, monkeypatch):
    """快照 mapStargates.yaml → stargate 表 → logistics 跳数图连通。

    守的是列口径：`destination_system_id` 必须存**目标星门 id**（logistics 再 join 一次拿星系），
    存成星系 id 图会整张断开、跳数全回退到兜底表。缺目标的脏条目跳过，不写半截行。
    """
    import zipfile

    import services.logistics as lg
    from services.importers import sde_loader

    zip_path = tmp_path / "sde.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "mapStargates.yaml",
            "50000001: {solarSystemID: 30000142, destination: {stargateID: 50000002, solarSystemID: 30000144}}\n"
            "50000002: {solarSystemID: 30000144, destination: {stargateID: 50000001, solarSystemID: 30000142}}\n"
            "50000003: {solarSystemID: 30000144, destination: {}}\n",
        )
    with (
        patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
        patch("services.importers.sde_loader.SDE_ZIP_PATH", str(zip_path)),
    ):
        await initialize_database()
        conn = sqlite3.connect(temp_db_path)
        conn.executemany(
            "INSERT INTO solar_system (solar_system_id, solar_system_name, security) VALUES (?, ?, 0.9)",
            [(30000142, "Jita"), (30000144, "Perimeter")],
        )
        conn.commit()
        conn.close()
        assert await sde_loader.write_stargates() == 2
        assert await sde_loader.write_stargates() == 0, "已有星门时幂等跳过"

    class _Db:
        def connect(self, _alias):
            from contextlib import closing

            return closing(sqlite3.connect(temp_db_path))

    monkeypatch.setattr(lg, "_default_db", lambda: _Db())
    assert lg.compute_jumps(30000142, 30000144) == 1


@pytest.mark.asyncio
async def test_region_names_seeds_empty_table_from_snapshot(temp_db_path, tmp_path):
    """回归（2026-11-16 曙光首装实测）：`write_region_names` 只 UPDATE 不 INSERT，全新库
    region 表永远 0 行 → 合同页星域无候选。快照有 mapRegions.yaml 时要把缺的行补进来，
    已有行（及其中文名）不覆盖，且全部有中文名时不发 ESI 请求。"""
    import zipfile

    from services.importers import sde_loader

    zip_path = tmp_path / "sde.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "mapRegions.yaml",
            "10000002: {name: {en: The Forge, zh: 伏尔戈}}\n10000043: {name: {en: Domain, zh: 多美}}\n",
        )
    conn = sqlite3.connect(temp_db_path)
    conn.execute("CREATE TABLE region (region_id INTEGER PRIMARY KEY, region_name TEXT, zh_name TEXT)")
    conn.execute("INSERT INTO region VALUES (10000043, 'Domain', '已有中文名')")
    conn.commit()
    conn.close()

    with (
        patch("services.importers.sde_loader.DATABASE_PATH", temp_db_path),
        patch("services.importers.sde_loader.SDE_ZIP_PATH", str(zip_path)),
        patch("services.client.APIClient", side_effect=AssertionError("名字齐全时不该请求 ESI")),
    ):
        assert await sde_loader.write_region_names() == 0

    conn = sqlite3.connect(temp_db_path)
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT * FROM region")}
    conn.close()
    assert rows == {10000002: ("The Forge", "伏尔戈"), 10000043: ("Domain", "已有中文名")}
