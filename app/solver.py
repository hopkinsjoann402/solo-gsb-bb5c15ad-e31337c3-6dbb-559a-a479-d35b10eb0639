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

稳健分相预演（solve_robust）：每台设备功率不是定值，而是正整数区间
[min_power, max_power]，各设备功率可在区间内独立变化。固定一种相位分配后，
各相负载的可能范围为 [该相设备下界和, 该相设备上界和]，且因各设备独立
变化，「某相取上界、某相取下界」可同时实现，故该分配的最坏负载差为
max_p 上界_p − min_p 下界_p。稳健可行要求各相负载上界均不超容量；
在此基础上最小化最坏负载差，同值取按设备编号排序的相位序列字典序最小者。
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


@dataclass(frozen=True)
class RobustDevice:
    """稳健预演中的待分配设备：功率为正整数区间 [min_power, max_power]。"""

    code: str  # 设备唯一编号
    min_power: int  # 功率下界（正整数，<= max_power）
    max_power: int  # 功率上界（正整数）
    allowed: tuple[str, ...]  # 可接入相位集合（PHASES 的非空子集）


@dataclass(frozen=True)
class RobustSolveResult:
    """稳健预演求解结果。feasible 为 False 时其余字段为 None。

    assignment：设备编号 -> 相位；
    load_lower / load_upper：各相负载的下界与上界（该相设备功率下界 / 上界之和）；
    worst_imbalance：任意功率组合可能产生的三相最大负载与最小负载之差的最大值。
    """

    feasible: bool
    assignment: Optional[dict[str, str]]
    load_lower: Optional[dict[str, int]]
    load_upper: Optional[dict[str, int]]
    worst_imbalance: Optional[int]


def solve_robust(
    devices: Iterable[RobustDevice], capacities: dict[str, int]
) -> RobustSolveResult:
    """求解功率区间不确定下的稳健最优分配（预演，不落库）。

    各设备功率可在 [min_power, max_power] 内独立变化。一种相位分配稳健可行，
    当且仅当各相负载上界（该相设备功率上界之和）不超过对应容量；此时无论各
    设备实际功率如何取值，各相均不超容量。该分配的最坏负载差为
    ``max_p 上界_p − min_p 下界_p``（取上界的相与取下界的相互不冲突，故可
    同时实现）。目标：最小化最坏负载差；同值取按设备编号排序的相位序列字典序
    最小的分配。

    :param devices: 设备集合（编号唯一、功率区间为正整数且下界<=上界、
        允许相位非空）。
    :param capacities: 各相容量，形如 {"A": int, "B": int, "C": int}。
    :return: RobustSolveResult；无稳健可行分配时 feasible=False。
    """
    ordered = sorted(devices, key=lambda d: d.code)
    n = len(ordered)
    mins = [d.min_power for d in ordered]
    maxs = [d.max_power for d in ordered]
    # 每台设备的可接入相位以下标（0/1/2）按 A/B/C 顺序排列，
    # 保证 DFS 按相位序列字典序枚举方案
    allowed_idx = [
        tuple(k for k, p in enumerate(PHASES) if p in d.allowed) for d in ordered
    ]
    caps = [capacities[p] for p in PHASES]

    # suffix_low[i][k]：剩余设备中可接入相位 k 的功率下界和
    # suffix_forced_high[i][k]：剩余设备中只允许相位 k 的设备功率上界和
    suffix_low = [[0, 0, 0] for _ in range(n + 1)]
    suffix_forced_high = [[0, 0, 0] for _ in range(n + 1)]
    # suffix_pair_high[i][k]：剩余设备中「无法接入相位 k」（只能落在另两相）
    # 的设备功率上界和：这部分上界由另两相承担
    # suffix_pair_low[i][k]：剩余设备中「能接入除 k 外另两相之一」（不被迫
    # 落在 k）的设备功率下界和：这部分下界可被另两相承担
    suffix_pair_high = [[0, 0, 0] for _ in range(n + 1)]
    suffix_pair_low = [[0, 0, 0] for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = allowed_idx[i]
        for k in range(3):
            suffix_low[i][k] = suffix_low[i + 1][k] + (
                mins[i] if k in ai else 0
            )
            suffix_forced_high[i][k] = suffix_forced_high[i + 1][k] + (
                maxs[i] if len(ai) == 1 and ai[0] == k else 0
            )
            reach_others = any(j in ai for j in range(3) if j != k)
            suffix_pair_high[i][k] = suffix_pair_high[i + 1][k] + (
                maxs[i] if k not in ai else 0
            )
            suffix_pair_low[i][k] = suffix_pair_low[i + 1][k] + (
                mins[i] if reach_others else 0
            )

    # 剩余全部设备（i..n-1）的功率上界和 / 下界和
    suffix_total_high = [sum(maxs[i:]) for i in range(n + 1)]
    suffix_total_low = [sum(mins[i:]) for i in range(n + 1)]

    lows = [0, 0, 0]
    highs = [0, 0, 0]
    current: list[Optional[int]] = [None] * n
    best_worst: Optional[int] = None
    best_assignment: Optional[list[int]] = None

    def worst_lower_bound(i: int, high_sum: int, low_sum: int) -> int:
        """当前部分分配状态下最终最坏负载差的乐观下界。

        ``最终 max_p 上界_p`` 的下界取：
          当前最大上界；被迫单相位设备的最大叠加；ceil(上界总和/3)；
          以及子集对偶界——对每个 k，无法接入 k 相的剩余设备上界只能落在
          另两相（连同该两相当前上界），其中较大者至少承担 ceil(S/2)。

        ``最终 min_p 下界_p`` 的上界取：
          min_p(当前下界_p + 剩余可接入 p 的下界和)；floor(下界总和/3)；
          以及子集对偶界——对每个 k，除「只能接入 k」外的剩余设备下界连同
          另两相当前下界恰好由该两相分摊，其中较小者至多 floor(S/2)。
        """
        sl = suffix_low[i]
        sfh = suffix_forced_high[i]
        sph = suffix_pair_high[i]
        spl = suffix_pair_low[i]
        h0, h1, h2 = highs
        l0, l1, l2 = lows

        # max 相上界的下界：当前最大 / 被迫叠加 / ceil(总量/3) / 两相对偶
        pair_max_01 = h0 + h1 + sph[2]  # 无法接 C 的上界只能落 A、B
        pair_max_02 = h0 + h2 + sph[1]
        pair_max_12 = h1 + h2 + sph[0]
        lb_max = max(
            h0,
            h1,
            h2,
            h0 + sfh[0],
            h1 + sfh[1],
            h2 + sfh[2],
            (high_sum + suffix_total_high[i] + 2) // 3,
            (pair_max_01 + 1) // 2,
            (pair_max_02 + 1) // 2,
            (pair_max_12 + 1) // 2,
        )

        # min 相下界的上界：可达和 / floor(总量/3) / 两相对偶
        pair_min_01 = l0 + l1 + spl[2]  # 可落 A、B 的下界由该两相分摊
        pair_min_02 = l0 + l2 + spl[1]
        pair_min_12 = l1 + l2 + spl[0]
        ub_min = min(
            l0 + sl[0],
            l1 + sl[1],
            l2 + sl[2],
            (low_sum + suffix_total_low[i]) // 3,
            pair_min_01 // 2,
            pair_min_02 // 2,
            pair_min_12 // 2,
        )
        return lb_max - ub_min

    # 空状态理论最优下界：完整方案达到该值即可提前终止搜索
    root_lb = worst_lower_bound(0, 0, 0)
    optimal_found = False

    # 贪心构造一个稳健可行初始解（按上界降序的 LPT，容量优先），
    # 仅借用其最坏差值作为初始上界阈值，使 DFS 自首个节点起即可剪枝；
    # 不记录贪心相位序列，平局时仍由 DFS 保证字典序最小，不影响最优性。
    g_highs = [0, 0, 0]
    g_lows = [0, 0, 0]
    greedy_ok = True
    for j in sorted(range(n), key=lambda x: -maxs[x]):
        candidates = [
            k for k in allowed_idx[j] if g_highs[k] + maxs[j] <= caps[k]
        ]
        if not candidates:
            greedy_ok = False
            break
        k = min(candidates, key=lambda x: (g_highs[x], x))
        g_highs[k] += maxs[j]
        g_lows[k] += mins[j]
    if greedy_ok:
        # 只借用贪心解的最坏差值作为初始上界（阈值），相位序列仍由 DFS
        # 按字典序首个遇到的最优方案决定，保证平局规则不被破坏。
        # 不在此处命中 root_lb 提前终止——仍须由 DFS 找到字典序最小方案。
        best_worst = max(g_highs) - min(g_lows)

    def dfs(i: int, high_sum: int, low_sum: int) -> None:
        nonlocal best_worst, best_assignment, optimal_found
        if optimal_found:
            return
        # 可行性剪枝：只允许单一相位的剩余设备的上界负载无法转移，
        # 若其叠加当前上界后超过该相容量，则本子树必无稳健可行解。
        for k in range(3):
            if highs[k] + suffix_forced_high[i][k] > caps[k]:
                return

        if i == n:
            worst = max(highs) - min(lows)
            # 严格更优时同时更新阈值与方案；与初始贪心阈值同值时也记录，
            # 因 DFS 按字典序展开，首个同值可行方案即字典序最小方案。
            if best_assignment is None or worst < best_worst:
                best_worst = worst
                best_assignment = current.copy()
                if worst == root_lb:
                    optimal_found = True
            return

        if (
            best_worst is not None
            and worst_lower_bound(i, high_sum, low_sum) > best_worst
        ):
            # 严格大于才剪枝：界等于当前最优时子树仍可能含同值但字典序
            # 更小的方案（DFS 按字典序展开，首个同值方案才是最终答案）。
            return

        for k in allowed_idx[i]:
            if highs[k] + maxs[i] <= caps[k]:
                highs[k] += maxs[i]
                lows[k] += mins[i]
                current[i] = k
                dfs(i + 1, high_sum + maxs[i], low_sum + mins[i])
                highs[k] -= maxs[i]
                lows[k] -= mins[i]
                current[i] = None
                if optimal_found:
                    break

    dfs(0, 0, 0)

    if best_assignment is None:
        return RobustSolveResult(
            feasible=False,
            assignment=None,
            load_lower=None,
            load_upper=None,
            worst_imbalance=None,
        )

    final_lower = {p: 0 for p in PHASES}
    final_upper = {p: 0 for p in PHASES}
    for idx, k in enumerate(best_assignment):
        final_lower[PHASES[k]] += mins[idx]
        final_upper[PHASES[k]] += maxs[idx]
    assignment = {ordered[i].code: PHASES[best_assignment[i]] for i in range(n)}
    return RobustSolveResult(
        feasible=True,
        assignment=assignment,
        load_lower=final_lower,
        load_upper=final_upper,
        worst_imbalance=best_worst,
    )
