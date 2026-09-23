"""G3 向量扩展能力现状。

对应 T01 交接卡第 5 节的 G3。

**首版不开启语义检索**，产品范围不变。这一组只回答一个问题：将来想开的时候，
sqlite-vec 这条路是不是通的——扩展能不能在目标平台上加载、vec0 虚拟表能不能建、
近邻查询能不能跑。答案记进结论表，不产生任何生产代码。

之所以现在就测而不是留到启用时再测，是因为"能不能加载"取决于 Python 发行版编译
sqlite3 时有没有开 `enable_load_extension`，这是个环境事实，越晚发现越贵。
"""

import sqlite3
from pathlib import Path

import pytest
import sqlite_vec

EMBEDDING_DIMENSIONS = 4


@pytest.fixture
def vec_conn(db_path: Path):
    """单独开一条加载了 sqlite-vec 的连接。

    扩展加载必须显式开关：`enable_load_extension(True)` 只在加载那一刻打开，加载完
    立即关掉——常开着等于给任意 SQL 一个加载本地动态库的入口。
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()


def test_g3_extension_loads_on_this_platform(vec_conn, record_property) -> None:
    """G3：扩展能在当前平台加载。

    标准库 sqlite3 是否提供 enable_load_extension 取决于 Python 发行版的编译选项，
    所以先断言这个能力存在，再断言扩展真的加载成功了。
    """
    assert hasattr(sqlite3.Connection, "enable_load_extension")
    version = vec_conn.execute("SELECT vec_version()").fetchone()[0]
    assert version
    record_property("sqlite_vec_version", version)


def test_g3_vec0_table_supports_nearest_neighbour_query(vec_conn) -> None:
    """G3：vec0 虚拟表能建、能写、能按距离取近邻。

    用例本身不代表首版要做语义检索——它只是把"这条路通不通"从推测变成事实。
    """
    vec_conn.execute(f"CREATE VIRTUAL TABLE context_chunks USING vec0(embedding float[{EMBEDDING_DIMENSIONS}])")

    rows = {
        1: [0.10, 0.20, 0.30, 0.40],
        2: [0.90, 0.80, 0.70, 0.60],
        3: [0.11, 0.21, 0.31, 0.41],
    }
    vec_conn.executemany(
        "INSERT INTO context_chunks(rowid, embedding) VALUES (?, ?)",
        [(rowid, sqlite_vec.serialize_float32(vector)) for rowid, vector in rows.items()],
    )

    query = sqlite_vec.serialize_float32([0.10, 0.20, 0.30, 0.41])
    nearest = vec_conn.execute(
        "SELECT rowid, distance FROM context_chunks WHERE embedding MATCH ? ORDER BY distance LIMIT 2",
        (query,),
    ).fetchall()

    assert [rowid for rowid, _ in nearest] == [1, 3], "最近的两条应当是 1 和 3，向量 2 明显更远"
    assert nearest[0][1] < nearest[1][1]


def test_g3_vector_table_lives_in_the_same_file_as_business_data(vec_conn, raw: sqlite3.Connection) -> None:
    """G3：向量表与业务表在同一个库文件里。

    这正是选 sqlite-vec 而不是独立向量服务的理由——同文件意味着同一次备份、同一个
    事务边界，不需要为一个尚未启用的功能再引入一个要部署、要备份的外部依赖。
    """
    raw.execute("CREATE TABLE context_summaries (id TEXT PRIMARY KEY, summary TEXT NOT NULL)")
    raw.execute("INSERT INTO context_summaries VALUES ('s1', '最近两周以基础练习为主')")

    vec_conn.execute(f"CREATE VIRTUAL TABLE chunk_vectors USING vec0(embedding float[{EMBEDDING_DIMENSIONS}])")

    # 同一条连接既能读业务表，也能读向量表
    assert vec_conn.execute("SELECT summary FROM context_summaries WHERE id = 's1'").fetchone()[0]
    assert vec_conn.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == 0
