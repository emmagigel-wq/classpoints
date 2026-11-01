"""抽奖：资格筛选、权重抽取、抽取人数、随机抽人。

随机性来源可注入（`rand` 参数），所以抽取结果在测试里可复现——
上一代靠全局 Math.random，测试只能碰运气。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Sequence

from app.core.models import AppState, LotteryRecord, Prize, Student

Rng = Callable[[], float]


@dataclass
class DrawResult:
    ok: bool
    reason: str = ''
    prize: Prize | None = None
    winners: list[Student] = None
    record: LotteryRecord | None = None

    def __post_init__(self) -> None:
        if self.winners is None:
            self.winners = []


def _rng(rand: Rng | None) -> Rng:
    if rand is not None:
        return rand
    return random.random


def pick_weighted(prizes: Sequence[Prize], rand: Rng | None = None) -> Prize | None:
    """按权重抽一个奖品。

    与上一代一致：rand=0 时取第一个，rand→1 时取最后一个，
    全部权重为 0 时返回 None（而不是硬塞一个）。
    """
    r = _rng(rand)
    total = sum(max(0.0, p.weight) for p in prizes)
    if total <= 0:
        return None
    target = r() * total
    acc = 0.0
    for p in prizes:
        w = max(0.0, p.weight)
        if w <= 0:
            continue
        acc += w
        if target < acc:
            return p
    return prizes[-1]


def rank_eligible_pool(state: AppState, top_n: int | None = None) -> list[Student]:
    """排名资格池：积分前 top_n 名（标准竞赛排名 1,2,2,4…）。

    **并列语义（有意为之）**：积分相同的同学名次相同；若并列名次正跨在
    top_n 边界上，则并列者**全部**入选——否则同分不同命，老师没法解释
    「为什么张三能抽、李四不能」。所以返回人数可能多于 top_n（这正是
    「前 N 名」在竞赛排名下的自然结果）。

    排序键 `(-points, name, id)`：分数降序，姓名与 id 只是稳定次级键，
    保证同一份数据每次都得到同样的名单（可复现，测试不靠运气）。

    `top_n <= 0` 返回空列表：不能退化为「全部入选」——老师把「取前几名」
    调到 0 通常是想关掉资格，而不是让全班都能抽。空班级同理返回空列表。
    """
    n = state.settings.lottery_top_n if top_n is None else top_n
    if n <= 0 or not state.students:
        return []
    ordered = sorted(state.students, key=lambda s: (-int(s.points), s.name, s.id))
    pool = []
    for student in ordered:
        rank = 1 + sum(1 for other in ordered if int(other.points) > int(student.points))
        if rank <= n:
            pool.append(student)
    return pool


def eligible_pool(state: AppState, threshold: int | None = None) -> list[Student]:
    """抽奖资格池：按 settings.lottery_mode 选判定方式。

    * `"threshold"`（默认）：积分 >= 门槛，保持旧行为（现有测试依赖它）；
    * `"rank"`：积分前 settings.lottery_top_n 名；此时 `threshold` 参数被忽略
      ——排名模式下没有「门槛」概念，界面上判定分输入框也会隐藏。

    调用方（抽奖页/`draw`）不需要分支，传不传 threshold 都能拿到正确的池子。
    """
    mode = getattr(state.settings, 'lottery_mode', 'threshold')
    if mode == 'rank':
        return rank_eligible_pool(state)
    t = state.settings.prize_threshold if threshold is None else threshold
    return [s for s in state.students if int(s.points) >= t]


def sample_students(
    pool: Sequence[Student],
    count: int,
    rand: Rng | None = None,
) -> list[Student]:
    """从池中等概率抽 count 个，不重复。

    用「部分洗牌」实现：抽 k 个只需前 k 步，池子大时比整体洗牌省。
    上一代用的是同样的思路（逐位取随机下标交换）。
    """
    r = _rng(rand)
    pool = list(pool)
    n = len(pool)
    if count <= 0 or n == 0:
        return []
    count = min(count, n)
    for i in range(count):
        j = i + int(r() * (n - i))
        if j >= n:
            j = n - 1
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:count]


def pick_random_winners(
    state: AppState,
    count: int,
    rand: Rng | None = None,
) -> list[Student]:
    """随机抽人（不涉及奖品与积分），用于「随机点人」功能。"""
    return sample_students(state.students, count, rand)


def draw(
    state: AppState,
    *,
    pool: Sequence[Student] | None = None,
    rand: Rng | None = None,
    cost: int | None = None,
    threshold: int | None = None,
    count: int | None = None,
    record: bool = True,
) -> DrawResult:
    """抽一次奖：选奖品 → 定中奖人 → 扣分 → 写记录。

    失败一律返回带文案的 DrawResult，不抛异常、不静默——
    上一代这些分支散在各处，漏一个就会出现「扣了分没奖品」。
    """
    r = _rng(rand)
    cost = state.settings.prize_cost if cost is None else cost
    count = 1 if count is None else count

    if not state.prizes:
        return DrawResult(ok=False, reason='还没有配置奖品')

    prize = pick_weighted(state.prizes, r)
    if prize is None:
        return DrawResult(ok=False, reason='所有奖品权重都是 0，无法抽奖')

    candidates = list(pool) if pool is not None else eligible_pool(state, threshold)
    if not candidates:
        # 空池子的原因不同，给老师的提示也要不同
        if getattr(state.settings, 'lottery_mode', 'threshold') == 'rank':
            return DrawResult(
                ok=False,
                reason=f'排名模式下没有符合条件的同学（积分前 {state.settings.lottery_top_n} 名）',
            )
        return DrawResult(ok=False, reason='还没有同学达到抽奖门槛')

    winners = sample_students(candidates, count, r)
    if not winners:
        return DrawResult(ok=False, reason='抽奖人数不足')

    # 扣分前先确认所有人都付得起；有一人付不起就整体不扣，避免「抽到了却扣不动」
    if cost > 0:
        affordable = [s for s in winners if s.points >= cost]
        if not affordable:
            return DrawResult(ok=False, reason='中奖同学积分不足，未扣分')

    winner_ids = [s.id for s in winners]

    if record:
        from app.core.scoring import add_points_batch
        add_points_batch(
            state,
            winner_ids,
            -abs(cost),
            reason=f'抽奖：{prize.name}',
            source='lottery',
        )
        rec = LotteryRecord(
            prize_name=prize.name,
            winners=winner_ids,
            winner_names=[s.name for s in winners],
            cost=abs(cost),
        )
        state.lottery.append(rec)
    else:
        rec = None

    return DrawResult(ok=True, prize=prize, winners=list(winners), record=rec)


def clear_records(state: AppState) -> int:
    """清空抽奖记录，返回清掉的条数（学生、积分、奖品都不动）。"""
    count = len(state.lottery)
    if count:
        state.lottery.clear()
    return count


def draw_for_student(
    state: AppState,
    student_id: str,
    *,
    rand: Rng | None = None,
    cost: int | None = None,
    record: bool = True,
) -> DrawResult:
    """「点选某位同学」的抽奖：中奖人已定，仍要走奖品与扣分。"""
    student = state.student_by_id(student_id)
    if student is None:
        return DrawResult(ok=False, reason='没有找到这位同学')
    if not state.prizes:
        return DrawResult(ok=False, reason='还没有配置奖品')

    prize = pick_weighted(state.prizes, rand)
    if prize is None:
        return DrawResult(ok=False, reason='所有奖品权重都是 0，无法抽奖')

    real_cost = state.settings.prize_cost if cost is None else cost
    if real_cost > 0 and student.points < real_cost:
        return DrawResult(ok=False, reason=f'{student.name} 积分不足，未扣分')

    rec = None
    if record:
        from app.core.scoring import add_points_batch
        add_points_batch(
            state,
            [student.id],
            -abs(real_cost),
            reason=f'抽奖：{prize.name}',
            source='lottery',
        )
        rec = LotteryRecord(
            prize_name=prize.name,
            winners=[student.id],
            winner_names=[student.name],
            cost=abs(real_cost),
        )
        state.lottery.append(rec)

    return DrawResult(ok=True, prize=prize, winners=[student], record=rec)
