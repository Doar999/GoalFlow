"""Celery 装配：一个执行任务、三个 Beat 定时任务（决策 E16、E17、E8）。

启动方式（部署编排属 T13）：

    celery -A goalflow.jobs.celery_app worker
    celery -A goalflow.jobs.celery_app beat        # 全局只开一个

Redis 只负责把 job_id 送到 Worker，正确性全在数据库一侧：消息丢了由恢复扫描补发，
重复了由领取的条件更新挡掉。所以这里**不开 acks_late**（E16）：Worker 崩溃后的恢复只走
"租约过期 → 恢复扫描"这一条路，不再叠加 broker 重投。
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from celery import Celery

from goalflow.core.config import get_settings
from goalflow.db.session import Database, get_database
from goalflow.idempotency import purge_expired
from goalflow.jobs.handlers import JobRegistry
from goalflow.jobs.handlers import registry as default_registry
from goalflow.jobs.service import Publisher, dispatch_outbox, recover
from goalflow.jobs.store import utc_now
from goalflow.jobs.worker import run_job

# 注册了作业处理函数的模块。Worker 启动时 import 它们，处理函数才会进入注册表。
# T08 起新增作业种类时在这里登记，例如 "goalflow.agent.jobs"。
HANDLER_MODULES: Final[tuple[str, ...]] = ()

RUN_JOB_TASK: Final = "goalflow.jobs.run_job"
DISPATCH_OUTBOX_TASK: Final = "goalflow.jobs.dispatch_outbox"
RECOVER_TASK: Final = "goalflow.jobs.recover"
PURGE_IDEMPOTENCY_TASK: Final = "goalflow.idempotency.purge_expired"

DISPATCH_INTERVAL: Final = timedelta(seconds=10)
RECOVERY_INTERVAL: Final = timedelta(seconds=30)
IDEMPOTENCY_PURGE_INTERVAL: Final = timedelta(days=1)


def publisher_for(app: Celery) -> Publisher:
    def publish(job_id: str) -> None:
        # retry=False：投递失败立即抛出，由 outbox 退避重投，不在这里阻塞请求线程。
        app.send_task(RUN_JOB_TASK, args=[job_id], retry=False)

    return publish


def create_celery_app(
    *,
    broker_url: str | None = None,
    database_factory: Callable[[], Database] = get_database,
    registry: JobRegistry = default_registry,
    clock: Callable[[], datetime] = utc_now,
) -> Celery:
    """测试里用 `memory://` 与临时库构造一个独立实例；生产用模块级的 `celery_app`。"""
    app = Celery("goalflow", broker=broker_url or get_settings().redis_url, set_as_current=False)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=False,
        # 一次只预取一条：作业可能跑几分钟，预取多条会让它们在一个忙碌的 Worker 上排队。
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        enable_utc=True,
        timezone="UTC",
        imports=HANDLER_MODULES,
        beat_schedule={
            "dispatch-outbox": {"task": DISPATCH_OUTBOX_TASK, "schedule": DISPATCH_INTERVAL.total_seconds()},
            "recover-jobs": {"task": RECOVER_TASK, "schedule": RECOVERY_INTERVAL.total_seconds()},
            "purge-idempotency": {
                "task": PURGE_IDEMPOTENCY_TASK,
                "schedule": IDEMPOTENCY_PURGE_INTERVAL.total_seconds(),
            },
        },
    )
    publish = publisher_for(app)

    # shared=False：Celery 默认把任务登记到进程里所有的 app 上，同名任务会互相覆盖，
    # 测试里构造的实例就会跑到模块级 celery_app 绑定的库上去。
    @app.task(name=RUN_JOB_TASK, shared=False)
    def run_job_task(job_id: str) -> None:
        run_job(database_factory(), job_id, registry=registry, clock=clock)

    @app.task(name=DISPATCH_OUTBOX_TASK, shared=False)
    def dispatch_outbox_task() -> None:
        dispatch_outbox(database_factory(), publish, now=clock())

    @app.task(name=RECOVER_TASK, shared=False)
    def recover_task() -> None:
        recover(database_factory(), now=clock())

    @app.task(name=PURGE_IDEMPOTENCY_TASK, shared=False)
    def purge_idempotency_task() -> None:
        purge_expired(database_factory(), now=clock())

    return app


celery_app: Final = create_celery_app()


def celery_publisher() -> Publisher:
    """API 进程在业务事务提交之后用它投递：`dispatch_outbox(db, celery_publisher(), ...)`。"""
    return publisher_for(celery_app)
