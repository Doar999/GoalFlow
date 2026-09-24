"""排期生成作业的处理函数注册（13 号第 8 节 generation；T05 决策 A10）。

排期计算是毫秒级纯函数，但 ensure_agenda 的接口语义是"去重生成作业"，
与 T07 的 202 + JobResponse 模式统一；正确性靠数据库一侧的 revision 重查与
唯一约束，不靠队列。模块在 `celery_app.HANDLER_MODULES` 登记，Worker 启动时加载。

测试注意：处理函数经模块级 `get_database()` 取库（Worker 进程单例），
与 T07 的约定一致；service 层逻辑请直接测 `service.run_agenda_generation`，
它接受显式 Database，逻辑与本函数完全相同。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from goalflow.contracts.enums import AgendaRevisionStatus  # noqa: F401  status 语义提示
from goalflow.db.session import get_database
from goalflow.idempotency import ResultRef
from goalflow.jobs.handlers import InputStale, JobCommit, JobContext, JobOutcome
from goalflow.jobs.handlers import registry as default_registry
from goalflow.jobs.store import utc_now
from goalflow.scheduling.engine import calculate_agenda
from goalflow.scheduling.models import UserPlanningState
from goalflow.scheduling.service import (
    AGENDA_GENERATION_KIND,
    _user_timezone,
    build_snapshot,
    persist_agenda,
)


@default_registry.handler(AGENDA_GENERATION_KIND)
def generate_agenda(ctx: JobContext) -> JobCommit:
    """事务外组装快照并计算；commit 由作业模块在写事务里调用（E14）。"""
    local_date = str(ctx.job.input_refs.get("local_date", ""))
    if not local_date:
        raise ValueError("agenda_generation 作业缺少 local_date 输入")
    database = get_database()

    snapshot = build_snapshot(database, ctx.job.owner_id, _user_timezone(database, ctx.job.owner_id), local_date)
    if snapshot.planning_revision != ctx.job.input_revision:
        raise InputStale()
    result = calculate_agenda(snapshot)
    ctx.stage_completed("agenda_calculated")

    def commit(session: Session) -> JobOutcome:
        # 事务内重查 planning revision：输入已变化则作业转 stale，不落业务结果（13 号第 7 节）。
        state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == ctx.job.owner_id))
        if state is None or state.revision != ctx.job.input_revision:
            raise InputStale()
        revision_id = persist_agenda(
            session,
            owner_id=ctx.job.owner_id,
            local_date=local_date,
            timezone=snapshot.timezone,
            input_planning_revision=ctx.job.input_revision,
            result=result,
            now=utc_now(),
        )
        return JobOutcome(result_refs=[ResultRef("agenda_revision", revision_id)])

    return commit
