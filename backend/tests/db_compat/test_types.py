"""B 类型与物理存储。

对应 T01 交接卡第 5 节的 B1—B6。

SQLite 只有 NULL / INTEGER / REAL / TEXT / BLOB 五种存储类，声明类型只影响**亲和性**，
不构成校验。因此这一组用例的目的不只是"能存能取"，还要把"数据库不会拦住写错的类型"
这件事固定成已知事实——见 B6。
"""

import json
import sqlite3

import pytest

# 文档 10 要求验证材料的抽取文本最长 20 万字符。
LONG_TEXT_LENGTH = 200_000


def test_b1_uuid_as_text_with_unique_index(raw: sqlite3.Connection) -> None:
    """B1：TEXT 存 36 字符 UUID 并建唯一索引，等值查询正常。

    不做 BINARY(16) 优化：SQLite 没有 MySQL utf8mb4 那种单列索引字节上限，
    原本为了绕开索引长度限制而准备的替代方案不再需要。
    """
    raw.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)")
    raw.execute("CREATE UNIQUE INDEX ux_goals_id ON goals(id)")

    goal_id = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    raw.execute("INSERT INTO goals VALUES (?, ?)", (goal_id, "11111111-1111-4111-8111-111111111111"))

    row = raw.execute("SELECT owner_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    assert row is not None
    assert len(goal_id) == 36

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO goals VALUES (?, ?)", (goal_id, "22222222-2222-4222-8222-222222222222"))


def test_b2_business_date_as_text_compares_and_sorts(raw: sqlite3.Connection) -> None:
    """B2：业务日期用 TEXT 'YYYY-MM-DD'，可直接比较与排序，无时区偏移。

    不用 INT(YYYYMMDD)：字符串形式可读、可直接比较，且跨日边界不涉及任何时区换算——
    业务日期本来就是用户本地日历上的一天，不是时间点。
    """
    raw.execute("CREATE TABLE agendas (owner_id TEXT NOT NULL, local_date TEXT NOT NULL)")
    dates = ["2026-09-30", "2026-10-01", "2026-09-23"]
    raw.executemany("INSERT INTO agendas VALUES ('u1', ?)", [(d,) for d in dates])

    ordered = [r[0] for r in raw.execute("SELECT local_date FROM agendas ORDER BY local_date")]
    assert ordered == ["2026-09-23", "2026-09-30", "2026-10-01"]

    in_range = raw.execute(
        "SELECT count(*) FROM agendas WHERE local_date >= ? AND local_date < ?",
        ("2026-09-23", "2026-10-01"),
    ).fetchone()[0]
    assert in_range == 2


def test_b3_utc_timestamp_keeps_microseconds(raw: sqlite3.Connection) -> None:
    """B3：事件时间以 ISO 8601 UTC 带微秒存取，精度不丢。

    精度要紧是因为作业事件要按时间排序。真丢了精度，排序就得改依赖
    job_events.sequence——所以这条要测，不能靠推断。
    """
    raw.execute("CREATE TABLE job_events (job_id TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL)")
    stamps = [
        "2026-09-23T10:20:30.123456+00:00",
        "2026-09-23T10:20:30.123457+00:00",
    ]
    raw.executemany("INSERT INTO job_events VALUES ('j1', ?, ?)", list(enumerate(stamps, start=1)))

    stored = [r[0] for r in raw.execute("SELECT occurred_at FROM job_events ORDER BY sequence")]
    assert stored == stamps
    # 相差 1 微秒的两条记录必须能区分出先后
    assert stored[0] < stored[1]


def test_b4_json_is_validated_on_write_and_queryable(raw: sqlite3.Connection) -> None:
    """B4：JSON 存 TEXT，写入前用 json() 校验，读取可用 json_extract 查询。

    不使用 PostgreSQL 专属的 JSONB 类型或语法——这条约束从 seekdb 方案原样保留。
    """
    raw.execute("CREATE TABLE routes (id TEXT PRIMARY KEY, derived_metrics_json TEXT NOT NULL)")
    payload = {"weekly_minutes": 300, "phases": [{"key": "p1", "weeks": 4}]}
    raw.execute("INSERT INTO routes VALUES ('r1', json(?))", (json.dumps(payload),))

    assert raw.execute("SELECT json_extract(derived_metrics_json, '$.weekly_minutes') FROM routes").fetchone()[0] == 300
    assert raw.execute("SELECT json_extract(derived_metrics_json, '$.phases[0].key') FROM routes").fetchone()[0] == "p1"

    stored = json.loads(raw.execute("SELECT derived_metrics_json FROM routes").fetchone()[0])
    assert stored == payload

    # 非法 JSON 在 json() 这一步就被拒，不会静默存成一段坏字符串
    with pytest.raises(sqlite3.OperationalError):
        raw.execute("INSERT INTO routes VALUES ('r2', json(?))", ("{not json",))


def test_b5_long_text_roundtrip(raw: sqlite3.Connection) -> None:
    """B5：长文本字段可存下文档 10 规定的 20 万字符抽取文本。"""
    raw.execute("CREATE TABLE artifacts (id TEXT PRIMARY KEY, extracted_text TEXT)")
    text_value = "材" * LONG_TEXT_LENGTH
    raw.execute("INSERT INTO artifacts VALUES ('a1', ?)", (text_value,))

    stored = raw.execute("SELECT extracted_text FROM artifacts").fetchone()[0]
    assert len(stored) == LONG_TEXT_LENGTH
    assert stored == text_value


def test_b6_type_affinity_does_not_reject_wrong_types(raw: sqlite3.Connection) -> None:
    """B6：类型亲和性不是校验——写错类型不会报错。

    **这条是本组最重要的一条，它固定的是一个"没有保护"的事实。**
    TEXT 亲和列写入整数会被悄悄转成 text；INTEGER 亲和列写入非数字字符串会原样保留为
    text，两种情况都没有异常。结论是：字段类型正确从数据库责任变成了应用层责任，
    由 SQLAlchemy 的类型层负责往返转换、Pydantic 负责入口校验。
    """
    raw.execute("CREATE TABLE affinity (t TEXT, n INTEGER)")

    raw.execute("INSERT INTO affinity VALUES (?, ?)", (20260923, "not-a-number"))

    stored_type, stored_number = raw.execute("SELECT typeof(t), typeof(n) FROM affinity").fetchone()
    assert stored_type == "text", "TEXT 亲和列把整数转成了 text，没有报错"
    assert stored_number == "text", "INTEGER 亲和列原样接受了非数字字符串，没有报错"
