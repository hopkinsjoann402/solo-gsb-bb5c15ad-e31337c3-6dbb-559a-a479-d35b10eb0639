"""三相配电最优分配求解器（纯标准库实现，便于独立测试）。

问题：每台设备不可拆分，只能接入其允许集合中的一个相位；
所有设备均须接入且各相负载不超过对应容量。
目标：最小化「三相负载最大值与最小值之差」；
差值相同时，取按设备编号排序后的相位序列字典序最小的方案。

求解方式：按设备编号排序后做深度优先搜索，相位按 A/B/C 顺序展开，
因此完整方案按相位序列的字典序依次产生；仅在差值严格更小时更新最优解，
即可保证同差值下保留字典序最小方案。同时使用容量约束与下界剪枝加速。

扩容问题（solve_capacity_upgrade）：原容量下无可行分配时，保持设备功率与
允许相位不变，逐相增加非负整数容量，使所有设备均可接入。目标依次为：
1) 三相扩容总量最小；2) 单相最大扩容量最小；3) 仍相同时取按设备编号排序的
相位序列字典序最小。枚举所有相位序列（至多 3^12），分两遍 DFS 配合
扩容总量 / 单相扩容量的上下界剪枝求解。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable, Optional

PHASES: tuple[str, str, str] = ("A", "B", "C")


@dataclass(frozen=True)
class Device:
    """待分配设备。"""

    code: str  # 设备唯一编号
    power: int  # 正整数功率
    allowed: tuple[str, ...]  # 可接入相位集合（PHASES 的非空子集）


@dataclass(frozen=True)
class SolveResult:
    """求解结果。feasible 为 False 时其余字段为 None。"""

    feasible: bool
    assignment: Optional[dict[str, str]]  # 设备编号 -> 相位
    loads: Optional[dict[str, int]]  # 相位 -> 负载
    imbalance: Optional[int]  # 最大相负载 - 最小相负载


def solve(devices: Iterable[Device], capacities: dict[str, int]) -> SolveResult:
    """求解最优分配方案。

    :param devices: 设备集合（编号唯一、功率为正整数、允许相位非空）。
    :param capacities: 各相容量，形如 {"A": int, "B": int, "C": int}。
    :return: SolveResult；无可行分配时 feasible=False。
    """
    ordered = sorted(devices, key=lambda d: d.code)
    n = len(ordered)
    powers = [d.power for d in ordered]
    # 每个设备的可接入相位按 A/B/C 固定顺序排列，保证 DFS 按字典序枚举方案
    allowed = [tuple(p for p in PHASES if p in d.allowed) for d in ordered]
    caps = {p: capacities[p] for p in PHASES}

    # suffix_addable[i][p]：第 i..n-1 台设备中可接入相位 p 的功率和（剪枝用）
    suffix_addable = [{p: 0 for p in PHASES} for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        prev, cur = suffix_addable[i + 1], suffix_addable[i]
        for p in PHASES:
            cur[p] = prev[p] + (powers[i] if p in allowed[i] else 0)

    # suffix_total[i]：第 i..n-1 台设备的功率总和
    suffix_total = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_total[i] = suffix_total[i + 1] + powers[i]

    loads = {p: 0 for p in PHASES}
    current: list[Optional[str]] = [None] * n
    best_imbalance: Optional[int] = None
    best_assignment: Optional[list[str]] = None

    def dfs(i: int, used: int) -> None:
        nonlocal best_imbalance, best_assignment
        if best_imbalance == 0:
            return  # 已达成理论最优（差值为 0），无需继续搜索
        if i == n:
            values = loads.values()
            imbalance = max(values) - min(values)
            # 仅严格更优才更新：同差值时先枚举到的方案字典序更小
            if best_imbalance is None or imbalance < best_imbalance:
                best_imbalance = imbalance
                best_assignment = current.copy()
            return
        if best_imbalance is not None:
            # 下界剪枝：
            #   最终最大负载 >= max(当前最大负载, 总功率平均值上取整)
            #   最终最小负载 <= min(当前各相负载 + 该相剩余可接入功率)
            remaining = suffix_total[i]
            lb_max = max(max(loads.values()), ceil((used + remaining) / 3))
            ub_min = min(loads[p] + suffix_addable[i][p] for p in PHASES)
            if lb_max - ub_min >= best_imbalance:
                return
        for phase in allowed[i]:
            if loads[phase] + powers[i] <= caps[phase]:
                loads[phase] += powers[i]
                current[i] = phase
                dfs(i + 1, used + powers[i])
                loads[phase] -= powers[i]
                current[i] = None

    dfs(0, 0)

    if best_assignment is None:
        return SolveResult(feasible=False, assignment=None, loads=None, imbalance=None)

    final_loads = {p: 0 for p in PHASES}
    for idx, phase in enumerate(best_assignment):
        final_loads[phase] += powers[idx]
    assignment = {ordered[i].code: best_assignment[i] for i in range(n)}
    return SolveResult(
        feasible=True,
        assignment=assignment,
        loads=final_loads,
        imbalance=best_imbalance,
    )


@dataclass(frozen=True)
class UpgradeResult:
    """扩容求解结果。

    original_capacities：原各相容量；upgrades：各相增加的非负整数容量；
    suggested_capacities：原容量 + 扩容量；assignment：设备编号 -> 相位；
    loads：相位 -> 实际负载。
    """

    original_capacities: dict[str, int]
    upgrades: dict[str, int]
    suggested_capacities: dict[str, int]
    assignment: dict[str, str]
    loads: dict[str, int]
    total_upgrade: int  # 三相扩容总量
    max_upgrade: int  # 单相最大扩容量


def _upgrade_bounds(
    i: int,
    loads: dict[str, int],
    caps: dict[str, int],
    suffix_forced: list[dict[str, int]],
    suffix_free_total: list[int],
) -> tuple[int, int]:
    """返回当前部分分配状态下扩容总量与单相扩容的乐观下界。

    返回 (lb_total, lb_max)：
    lb_total —— 三相扩容总量的下界；
    lb_max   —— 单相扩容量的下界，取 max(被迫超载量, ceil(lb_total/3))。

    后续只允许单一相位的设备所落相位已确定（被迫负载 forced），其超载必须
    扩容承担；其余自由设备（允许集合至少两个相位）的功率总和 F 忽略相位
    限制（视为可任意切分），先填充各相被迫负载之外的空闲容量，填不满的
    部分才必须扩容。忽略自由设备的相位限制只会低估扩容，故为合法下界。
    """
    forced_deficit = 0  # 被迫负载造成的必扩容总量
    spare = 0  # 各相可接纳自由设备功率的空闲容量之和
    forced_max = 0
    for p in PHASES:
        forced = loads[p] + suffix_forced[i][p]
        if forced > caps[p]:
            deficit = forced - caps[p]
            forced_deficit += deficit
            if deficit > forced_max:
                forced_max = deficit
        else:
            spare += caps[p] - forced
    lb_total = forced_deficit + max(0, suffix_free_total[i] - spare)
    return lb_total, max(forced_max, ceil(lb_total / 3))


def solve_capacity_upgrade(
    devices: Iterable[Device], capacities: dict[str, int]
) -> UpgradeResult:
    """在保持设备功率与允许相位不变的前提下，求解最小扩容方案。

    目标依次为：三相扩容总量最小 → 单相最大扩容量最小 →
    按设备编号排序的相位序列字典序最小。扩容后总有可行方案
    （每台设备至少存在一个允许相位，各相扩至足够大即可接入）。

    :param devices: 设备集合（编号唯一、功率为正整数、允许相位非空）。
    :param capacities: 各相原容量，形如 {"A": int, "B": int, "C": int}。
    :return: UpgradeResult。
    """
    ordered = sorted(devices, key=lambda d: d.code)
    n = len(ordered)
    powers = [d.power for d in ordered]
    # 每个设备的可接入相位按 A/B/C 固定顺序排列，保证 DFS 按字典序枚举方案
    allowed = [tuple(p for p in PHASES if p in d.allowed) for d in ordered]
    caps = {p: capacities[p] for p in PHASES}

    # suffix_forced[i][p]：第 i..n-1 台设备中只允许相位 p 的设备功率和
    # suffix_free_total[i]：第 i..n-1 台设备中允许集合非单一的设备功率总和
    suffix_forced = [{p: 0 for p in PHASES} for _ in range(n + 1)]
    suffix_free_total = [0] * (n + 1)
    for idx in range(n - 1, -1, -1):
        prev_f, cur_f = suffix_forced[idx + 1], suffix_forced[idx]
        for p in PHASES:
            cur_f[p] = prev_f[p]
            if p in allowed[idx] and len(allowed[idx]) == 1:
                cur_f[p] += powers[idx]
        suffix_free_total[idx] = suffix_free_total[idx + 1] + (
            powers[idx] if len(allowed[idx]) > 1 else 0
        )

    loads = {p: 0 for p in PHASES}
    current: list[Optional[str]] = [None] * n

    # 第一遍：最小化三相扩容总量
    best_total: Optional[int] = None
    root_lb_total, _ = _upgrade_bounds(
        0, loads, caps, suffix_forced, suffix_free_total
    )
    optimal_total_found = False

    def dfs_total(i: int) -> None:
        nonlocal best_total, optimal_total_found
        if optimal_total_found:
            return  # 已命中空状态理论最优下界，且回溯由各层正常完成
        lb_total, _ = _upgrade_bounds(
            i, loads, caps, suffix_forced, suffix_free_total
        )
        # 已无法严格改善当前最优（下界不会随分配下降，剪枝安全）
        if best_total is not None and lb_total >= best_total:
            return
        if i == n:
            total = sum(max(0, loads[p] - caps[p]) for p in PHASES)
            if best_total is None or total < best_total:
                best_total = total
                if total == root_lb_total:
                    optimal_total_found = True
            return
        for phase in allowed[i]:
            loads[phase] += powers[i]
            current[i] = phase
            dfs_total(i + 1)
            loads[phase] -= powers[i]
            current[i] = None
            if optimal_total_found:
                break

    dfs_total(0)

    assert best_total is not None  # 扩容后必有解
    target_total = best_total

    # 第二遍：在扩容总量固定最小的方案中，以 (单相最大扩容量, 相位序列) 为
    # 键取字典序最小，即先最小化单相最大扩容、再最小化相位序列。
    best_max: Optional[int] = None
    best_assignment: Optional[list[str]] = None
    best_key: Optional[tuple[int, tuple[str, ...]]] = None

    def dfs_max(i: int) -> None:
        nonlocal best_max, best_assignment, best_key
        lb_total, lb_max = _upgrade_bounds(
            i, loads, caps, suffix_forced, suffix_free_total
        )
        if lb_total > target_total:
            return
        # 仅当子树不可能得到「严格更小」的单相最大扩容时剪枝；
        # lb_max == best_max 时子树仍可能含有同值但字典序更小的方案，不可剪枝
        if best_max is not None and lb_max > best_max:
            return
        if i == n:
            extras = {p: max(0, loads[p] - caps[p]) for p in PHASES}
            if sum(extras.values()) != target_total:
                return
            current_max = max(extras.values())
            # 以 (单相最大扩容, 相位序列) 为键严格更优才更新，
            # 同 max 时保留字典序更小的完整方案
            candidate_key = (current_max, tuple(current))
            if best_key is None or candidate_key < best_key:
                best_key = candidate_key
                best_max = current_max
                best_assignment = current.copy()
            return
        for phase in allowed[i]:
            loads[phase] += powers[i]
            current[i] = phase
            dfs_max(i + 1)
            loads[phase] -= powers[i]
            current[i] = None

    dfs_max(0)

    assert best_assignment is not None
    final_loads = {p: 0 for p in PHASES}
    for idx, phase in enumerate(best_assignment):
        final_loads[phase] += powers[idx]
    upgrades = {p: max(0, final_loads[p] - caps[p]) for p in PHASES}
    suggested = {p: caps[p] + upgrades[p] for p in PHASES}
    assignment = {ordered[i].code: best_assignment[i] for i in range(n)}
    return UpgradeResult(
        original_capacities=dict(caps),
        upgrades=upgrades,
        suggested_capacities=suggested,
        assignment=assignment,
        loads=final_loads,
        total_upgrade=sum(upgrades.values()),
        max_upgrade=max(upgrades.values()),
    )
