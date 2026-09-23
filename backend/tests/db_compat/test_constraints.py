"""C 约束与唯一性。

对应 T01 交接卡第 5 节的 C1—C6。C1 是红线：复合唯一约束是整个去重机制的地基。
"""

import sqlite3

import pytest


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    conn.execute("""
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE goal_profiles (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            goal_id TEXT NOT NULL REFERENCES goals(id),
            version_no INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'superseded')),
            UNIQUE (goal_id, version_no)
        )
    """)
    conn.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            dedupe_key TEXT,
            UNIQUE (owner_id, kind, dedupe_key)
        )
    """)
    conn.execute("INSERT INTO users VALUES ('u1')")
    conn.execute("INSERT INTO goals VALUES ('g1', 'u1')")


def test_c1_composite_unique_constraints_hold(raw: sqlite3.Connection) -> None:
    """C1（红线）：复合唯一约束生效。

    数据模型里的去重全靠这些键：(goal_id, version_no)、(owner_id, kind, dedupe_key)、
    (agenda_id, version_no)、(owner_id, local_date)、(job_id, sequence)。
    这条失败，去重机制就失去地基。
    """
    _create_schema(raw)
    raw.execute("INSERT INTO goal_profiles VALUES ('p1', 'u1', 'g1', 1, 'active')")

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO goal_profiles VALUES ('p2', 'u1', 'g1', 1, 'draft')")

    # 换一个 version_no 就应当放行，证明约束卡的是组合而不是单列
    raw.execute("INSERT INTO goal_profiles VALUES ('p3', 'u1', 'g1', 2, 'draft')")
    assert raw.execute("SELECT count(*) FROM goal_profiles").fetchone()[0] == 2

    raw.execute("INSERT INTO jobs VALUES ('j1', 'u1', 'generate_plan', 'g1:rev7')")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO jobs VALUES ('j2', 'u1', 'generate_plan', 'g1:rev7')")


def test_c2_unique_violation_identifies_the_specific_key(raw: sqlite3.Connection) -> None:
    """C2：唯一冲突异常能区分是哪个键冲突的。

    能区分，去重就可以直接"插入，冲突则按已存在处理"；不能区分才需要退回
    "先查后插 + 事务内重试"，那会引入额外竞态。SQLite 的报错直接列出冲突的列名。
    """
    _create_schema(raw)
    raw.execute("INSERT INTO goal_profiles VALUES ('p1', 'u1', 'g1', 1, 'active')")
    raw.execute("INSERT INTO jobs VALUES ('j1', 'u1', 'generate_plan', 'g1:rev7')")

    with pytest.raises(sqlite3.IntegrityError) as profile_conflict:
        raw.execute("INSERT INTO goal_profiles VALUES ('p2', 'u1', 'g1', 1, 'draft')")
    assert "goal_profiles.goal_id" in str(profile_conflict.value)
    assert "goal_profiles.version_no" in str(profile_conflict.value)

    with pytest.raises(sqlite3.IntegrityError) as job_conflict:
        raw.execute("INSERT INTO jobs VALUES ('j2', 'u1', 'generate_plan', 'g1:rev7')")
    assert "jobs.dedupe_key" in str(job_conflict.value)
    # 两个冲突的消息不同，说明调用方可以据此分辨
    assert str(profile_conflict.value) != str(job_conflict.value)


def test_c3_multiple_nulls_allowed_in_unique_index(raw: sqlite3.Connection) -> None:
    """C3：唯一索引中 NULL 互不相等，多行 NULL 都允许。

    这决定了 dedupe_key 为空的作业不会互相挤掉，也决定了 C4 不能靠"NULL 表示非 active"来做。
    """
    _create_schema(raw)
    raw.execute("INSERT INTO jobs VALUES ('j1', 'u1', 'rebuild_agenda', NULL)")
    raw.execute("INSERT INTO jobs VALUES ('j2', 'u1', 'rebuild_agenda', NULL)")

    assert raw.execute("SELECT count(*) FROM jobs WHERE dedupe_key IS NULL").fetchone()[0] == 2


def test_c4_partial_unique_index_expresses_at_most_one_active(raw: sqlite3.Connection) -> None:
    """C4：条件唯一用部分唯一索引直接表达。

    原方案是加一个 active_marker 列（active 行写固定值、非 active 行写自身 id），
    靠复合唯一绕出条件唯一。SQLite 支持部分唯一索引，那个冗余列连同"marker 写错就
    静默失效"的故障模式一起消失。
    """
    _create_schema(raw)
    raw.execute("CREATE UNIQUE INDEX ux_goal_one_active_profile ON goal_profiles(goal_id) WHERE status = 'active'")

    raw.execute("INSERT INTO goal_profiles VALUES ('p1', 'u1', 'g1', 1, 'active')")

    # 同一目标的第二个 active 被拦下
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO goal_profiles VALUES ('p2', 'u1', 'g1', 2, 'active')")

    # 非 active 不受该索引约束，历史版本想留多少留多少
    raw.execute("INSERT INTO goal_profiles VALUES ('p3', 'u1', 'g1', 2, 'superseded')")
    raw.execute("INSERT INTO goal_profiles VALUES ('p4', 'u1', 'g1', 3, 'superseded')")

    # 切换当前版本：旧的降级后新的才能升，且整个过程在一个事务里完成
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE goal_profiles SET status = 'superseded' WHERE id = 'p1'")
    raw.execute("UPDATE goal_profiles SET status = 'active' WHERE id = 'p3'")
    raw.execute("COMMIT")

    active = raw.execute("SELECT id FROM goal_profiles WHERE status = 'active'").fetchall()
    assert active == [("p3",)]


def test_c5_foreign_keys_enforced_only_when_pragma_is_on(db_path, raw, open_second) -> None:
    """C5：外键生效，但**只在开了 PRAGMA foreign_keys 的连接上**生效。

    这是 SQLite 最容易造成"以为有保护其实没有"的一处：pragma 是连接级的、默认关闭，
    漏设不会有任何报错，外键就只是一句注释。所以这条同时测两个方向。
    """
    _create_schema(raw)

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO goals VALUES ('g2', 'nonexistent-user')")

    bare = sqlite3.connect(db_path)
    try:
        assert bare.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        # 没开 pragma 的连接会静默写进一条悬空引用
        bare.execute("INSERT INTO goals VALUES ('g3', 'nonexistent-user')")
        bare.commit()
    finally:
        bare.close()

    checked = open_second()
    orphans = checked.execute("PRAGMA foreign_key_check").fetchall()
    assert orphans, "漏设 pragma 的连接确实写进了悬空引用，foreign_key_check 能事后查出来"


def test_c6_check_constraints_hold(raw: sqlite3.Connection) -> None:
    """C6：CHECK 约束生效，状态枚举可以由数据库兜底。"""
    _create_schema(raw)

    with pytest.raises(sqlite3.IntegrityError) as conflict:
        raw.execute("INSERT INTO goal_profiles VALUES ('p1', 'u1', 'g1', 1, 'bogus')")
    assert "CHECK constraint failed" in str(conflict.value)

    raw.execute("INSERT INTO goal_profiles VALUES ('p1', 'u1', 'g1', 1, 'draft')")
    assert raw.execute("SELECT status FROM goal_profiles").fetchone()[0] == "draft"
