"""D 事务与并发。

对应 T01 交接卡第 5 节的 D1—D9。D1、D8、D9 是红线。

SQLite 没有 SELECT ... FOR UPDATE，也没有 SKIP LOCKED，所以数据模型第 7 节的并发协议
收敛为唯一一种形状：**短写事务 + 带 revision 条件的 UPDATE + 影响行数判定**。
这一组用例验证的就是这个协议本身成立，而不是某条 SQL 能跑。
"""

import contextlib
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

# D9 用真实子进程而不是线程：SQLite 的写锁是文件级的、跨进程的，而生产形态下
# API、Worker、Beat 本来就是不同进程。用线程测只能证明同进程内的串行。
WORKER_SOURCE = """
import random
import sqlite3
import sys
import time

path, rounds = sys.argv[1], int(sys.argv[2])
conn = sqlite3.connect(path, isolation_level=None, timeout=10)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=10000")

committed = 0
for _ in range(rounds):
    for attempt in range(10):
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
            conn.execute("COMMIT")
            committed += 1
            break
        except sqlite3.OperationalError:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            time.sleep(0.005 * (attempt + 1) + random.random() * 0.005)
    else:
        print("UNRECOVERABLE_BUSY")
        sys.exit(1)

conn.close()
print(committed)
"""


def _create_budget(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE daily_budget (
            owner_id TEXT NOT NULL,
            local_date TEXT NOT NULL,
            remaining_minutes INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (owner_id, local_date)
        )
    """)
    conn.execute("INSERT INTO daily_budget VALUES ('u1', '2026-09-23', 60, 1)")


def test_d1_commit_and_rollback_leave_no_residue(raw: sqlite3.Connection) -> None:
    """D1（红线）：事务提交与回滚，回滚后无残留。"""
    raw.execute("CREATE TABLE checkins (id TEXT PRIMARY KEY, note TEXT)")

    raw.execute("BEGIN IMMEDIATE")
    raw.execute("INSERT INTO checkins VALUES ('c1', 'kept')")
    raw.execute("COMMIT")

    raw.execute("BEGIN IMMEDIATE")
    raw.execute("INSERT INTO checkins VALUES ('c2', 'discarded')")
    raw.execute("UPDATE checkins SET note = 'tampered' WHERE id = 'c1'")
    raw.execute("ROLLBACK")

    assert raw.execute("SELECT id, note FROM checkins").fetchall() == [("c1", "kept")]


def test_d2_wal_reader_sees_a_stable_snapshot(raw: sqlite3.Connection, open_second) -> None:
    """D2：实测隔离行为，不靠推断。

    WAL 下读事务看到的是事务开始时的快照：写方提交后，已经在事务里的读方仍然看到旧值，
    直到它自己结束事务。事务协议依赖这一点——"事务中重新检查 planning revision"才有意义。
    """
    _create_budget(raw)
    reader = open_second()

    reader.execute("BEGIN")
    assert reader.execute("SELECT remaining_minutes FROM daily_budget").fetchone()[0] == 60

    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE daily_budget SET remaining_minutes = 30, revision = 2")
    raw.execute("COMMIT")

    # 读方仍在自己的快照里
    assert reader.execute("SELECT remaining_minutes FROM daily_budget").fetchone()[0] == 60
    reader.execute("COMMIT")
    # 结束事务后才看到新值
    assert reader.execute("SELECT remaining_minutes FROM daily_budget").fetchone()[0] == 30


def test_d3_deferred_upgrade_conflicts_while_immediate_serializes(raw: sqlite3.Connection, open_second) -> None:
    """D3：为什么写事务必须用 BEGIN IMMEDIATE。

    DEFERRED 事务先拿读锁、写第一行时才升级为写锁。如果期间别人写并提交了，升级会立刻
    失败，而且**这种失败不受 busy_timeout 保护、也不能靠等待解决**。BEGIN IMMEDIATE 把
    竞争提前到事务开头，变成可等待、可重试的情形。
    """
    _create_budget(raw)
    other = open_second()

    # DEFERRED：先读后写，中间被人抢先提交
    other.execute("BEGIN")
    other.execute("SELECT remaining_minutes FROM daily_budget").fetchone()

    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE daily_budget SET remaining_minutes = 30, revision = 2")
    raw.execute("COMMIT")

    with pytest.raises(sqlite3.OperationalError):
        other.execute("UPDATE daily_budget SET remaining_minutes = 10")
    with contextlib.suppress(sqlite3.OperationalError):
        other.execute("ROLLBACK")

    # IMMEDIATE：重试即可成功，因为竞争在事务开头就解决了
    other.execute("BEGIN IMMEDIATE")
    other.execute("UPDATE daily_budget SET remaining_minutes = 10, revision = 3")
    other.execute("COMMIT")
    assert raw.execute("SELECT remaining_minutes, revision FROM daily_budget").fetchone() == (10, 3)


def test_d4_conditional_update_rowcount_counts_matches_not_changes(raw: sqlite3.Connection) -> None:
    """D4：条件更新的影响行数语义——匹配即计数，即使新值等于旧值。

    这条在 MySQL 协议下是最隐蔽的陷阱：影响行数默认返回"实际改变的行数"，幂等重提时
    新值等于旧值会返回 0，`expected_revision` 校验就会**误判为版本冲突**，而且不报异常。
    SQLite 返回的是匹配行数，不存在需要在连接参数里固定语义的问题。
    """
    _create_budget(raw)

    unchanged = raw.execute(
        "UPDATE daily_budget SET remaining_minutes = 60 WHERE owner_id = 'u1' AND revision = 1"
    ).rowcount
    assert unchanged == 1, "新值等于旧值时仍然计入影响行数，幂等重提不会被误判为版本冲突"

    changed = raw.execute(
        "UPDATE daily_budget SET remaining_minutes = 30 WHERE owner_id = 'u1' AND revision = 1"
    ).rowcount
    assert changed == 1

    stale = raw.execute(
        "UPDATE daily_budget SET remaining_minutes = 0 WHERE owner_id = 'u1' AND revision = 99"
    ).rowcount
    assert stale == 0, "版本不匹配返回 0，这是判定 stale 的唯一依据"


def test_d5_error_kinds_are_distinguishable(raw: sqlite3.Connection, open_second) -> None:
    """D5：约束冲突与锁等待超时是两类不同的异常，可以分别识别。

    这条要紧是因为重试策略依赖它：锁等待超时可以重试，约束冲突重试多少次都一样，
    把后者当成前者重试会把一个确定性失败变成一串无意义的重试。
    """
    _create_budget(raw)
    raw.execute("CREATE TABLE uniq (k TEXT PRIMARY KEY)")
    raw.execute("INSERT INTO uniq VALUES ('a')")

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO uniq VALUES ('a')")

    blocked = open_second(busy_timeout_ms=100)
    raw.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError) as busy:
            blocked.execute("BEGIN IMMEDIATE")
        assert "locked" in str(busy.value) or "busy" in str(busy.value)
    finally:
        raw.execute("ROLLBACK")

    # 约束冲突不是 OperationalError，重试逻辑不会把两者混淆
    assert not issubclass(sqlite3.IntegrityError, sqlite3.OperationalError)


def test_d6_busy_timeout_is_honoured(raw: sqlite3.Connection, open_second) -> None:
    """D6：记录锁等待超时的实际时长与可配置性。

    按实测值设置作业超时——写方持锁期间，另一方确实会等满配置的时间再放弃。
    """
    _create_budget(raw)
    waiter = open_second(busy_timeout_ms=400)

    raw.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        with pytest.raises(sqlite3.OperationalError):
            waiter.execute("BEGIN IMMEDIATE")
        waited = time.perf_counter() - started
    finally:
        raw.execute("ROLLBACK")

    assert waited >= 0.35, f"实际只等了 {waited:.3f}s，busy_timeout 没有生效"


def test_d7_lease_claim_replaces_skip_locked(raw: sqlite3.Connection, open_second) -> None:
    """D7：SKIP LOCKED 不可用，用租约式抢占替代。

    先选候选，再 `UPDATE ... WHERE status='queued' AND id=?`，靠影响行数判断是否抢到。
    jobs 表已有 lease_token / lease_until 支撑这一点。
    """
    raw.execute("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            lease_token TEXT,
            lease_until TEXT
        )
    """)
    raw.execute("INSERT INTO jobs VALUES ('j1', 'queued', NULL, NULL)")
    rival = open_second()

    claim_sql = """
        UPDATE jobs SET status = 'running', lease_token = ?, lease_until = ?
        WHERE id = 'j1' AND status = 'queued'
    """
    first = raw.execute(claim_sql, ("worker-a", "2026-09-23T10:25:00+00:00")).rowcount
    second = rival.execute(claim_sql, ("worker-b", "2026-09-23T10:25:00+00:00")).rowcount

    assert (first, second) == (1, 0), "只有一个 Worker 能抢到，第二个靠影响行数 0 得知没抢到"
    assert raw.execute("SELECT lease_token FROM jobs").fetchone()[0] == "worker-a"

    # 旧 Worker 即使晚返回也提交不了：租约不匹配，条件更新影响 0 行
    stale_submit = rival.execute(
        "UPDATE jobs SET status = 'succeeded' WHERE id = 'j1' AND lease_token = ?", ("worker-b",)
    ).rowcount
    assert stale_submit == 0


def test_d8_concurrent_budget_decrement_lets_only_one_win(raw: sqlite3.Connection, open_second) -> None:
    """D8（红线）：两个连接同时扣同一日容量，只有一个成功（对应 PRD Q02）。"""
    _create_budget(raw)
    rival = open_second()

    outcomes: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def contend(conn: sqlite3.Connection) -> None:
        try:
            barrier.wait(timeout=5)
            conn.execute("BEGIN IMMEDIATE")
            claimed = conn.execute(
                """
                UPDATE daily_budget SET remaining_minutes = remaining_minutes - 60, revision = revision + 1
                WHERE owner_id = 'u1' AND local_date = '2026-09-23' AND revision = 1 AND remaining_minutes >= 60
                """
            ).rowcount
            time.sleep(0.05)
            conn.execute("COMMIT")
            outcomes.append(claimed)
        except BaseException as exc:
            errors.append(exc)
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ROLLBACK")

    threads = [threading.Thread(target=contend, args=(conn,)) for conn in (raw, rival)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"并发扣减出现异常：{errors}"
    assert sorted(outcomes) == [0, 1], f"应当恰好一个成功一个失败，实际 {outcomes}"
    assert raw.execute("SELECT remaining_minutes, revision FROM daily_budget").fetchone() == (0, 2)


def test_d9_multiple_processes_write_without_unrecoverable_busy(raw: sqlite3.Connection, db_path) -> None:
    """D9（红线）：多进程并发写不产生无法通过重试恢复的 SQLITE_BUSY。

    这是 SQLite 相对 seekdb 新引入的风险。生产形态下 API、Worker、Beat、outbox 分发器
    是不同进程，写在库级别串行。这条验证"串行"退化成的是等待与重试，而不是丢写。
    """
    raw.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
    raw.execute("INSERT INTO counter VALUES (1, 0)")

    processes = 4
    rounds = 25
    running = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER_SOURCE, str(db_path), str(rounds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(processes)
    ]
    results = [proc.communicate(timeout=120) for proc in running]

    for proc, (stdout, stderr) in zip(running, results, strict=True):
        assert proc.returncode == 0, f"子进程失败：{stdout.strip()} {stderr.strip()}"
        assert "UNRECOVERABLE_BUSY" not in stdout
        assert stdout.strip() == str(rounds)

    assert raw.execute("SELECT n FROM counter").fetchone()[0] == processes * rounds
