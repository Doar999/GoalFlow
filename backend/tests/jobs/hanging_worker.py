"""由 test_job_recovery.py 以子进程启动、领取作业后卡住、随后被强行杀掉的 Worker。

用法：python hanging_worker.py <数据库连接串> <job_id>
领取成功后向标准输出打印一行 claimed，父进程据此确认租约已经落库再杀掉它。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs_support import KIND

from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database
from goalflow.jobs import JobCommit, JobContext, JobRegistry, run_job


def _hang(context: JobContext) -> JobCommit:
    print("claimed", flush=True)
    time.sleep(3600)
    raise AssertionError("应当在这之前被杀掉")


def main(url: str, job_id: str) -> None:
    registry = JobRegistry()
    registry.register(KIND, _hang)
    run_job(Database(create_database_engine(url)), job_id, registry=registry, heartbeat_interval=None)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
