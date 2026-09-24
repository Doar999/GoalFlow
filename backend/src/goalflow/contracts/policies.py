"""生命周期相关的版本化策略参数（T04 决策 A9）。

这些值是"首版默认值"而非永久常量：取值变更时递增策略版本号，历史记录凭当时的
版本号解释当时的判定。需要按用户或按环境调整时，再升级为参数表，本模块的取值
改为读取该表的当前版本。

审计事件引用 CLOSURE_POLICY_VERSION 记录"当时按哪版策略判定"。
"""

from datetime import timedelta
from typing import Final

from goalflow.contracts.enums import ReviewPeriod

#: 结束操作的撤销窗口。只用于撤销误操作，不是通用的重新开启（12-goal-lifecycle.md 第 6 节）。
CLOSURE_UNDO_WINDOW: Final[timedelta] = timedelta(hours=24)

#: 维持型目标的默认回顾周期，与周投入预算同一节奏（12-goal-lifecycle.md 第 4 节）。
DEFAULT_REVIEW_PERIOD: Final[ReviewPeriod] = ReviewPeriod.WEEKLY

#: 上述取值的策略版本标识，写进审计事件。取值或语义变化时必须更换。
CLOSURE_POLICY_VERSION: Final[str] = "closure-policy-v1"


# —— 排期策略（T05 决策 A1；13-scheduling-engine.md 第 4 节）——

#: 排期策略版本标识，写入 agenda_revisions.scheduling_policy_version（Q08 可解释）。
SCHEDULING_POLICY_VERSION: Final[str] = "scheduling-policy-v1"

#: latest_date 距今日不超过该天数的弹性任务进入第一紧迫区间。
URGENT_WITHIN_DAYS: Final[int] = 2

#: latest_date 距今日不超过该天数的弹性任务进入第二紧迫区间；更晚为第三区间。
SOON_WITHIN_DAYS: Final[int] = 7

#: 本周投入缺口达到该分钟数时，弹性任务进入最高缺口区间（13 号第 4 节排序键 2）。
WEEKLY_GAP_HIGH_MINUTES: Final[int] = 60

#: 连续未获安排天数达到该值时，同等弹性条件下获得更高顺序（防长期饥饿，13 号第 9 节）。
STARVATION_AFTER_DAYS: Final[int] = 3

#: 任务规格未给 minimum_session_minutes 时，可拆分任务的单次分配下限（分钟）。
DEFAULT_MINIMUM_SESSION_MINUTES: Final[int] = 15
