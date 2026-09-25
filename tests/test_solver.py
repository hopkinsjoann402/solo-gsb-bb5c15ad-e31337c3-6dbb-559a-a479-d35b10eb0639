"""求解器单元测试（仅依赖标准库，可直接运行：python3 -m unittest tests.test_solver）。"""

import itertools
import random
import unittest

from app.solver import (
    Device,
    RobustDevice,
    solve,
    solve_capacity_upgrade,
    solve_robust,
)

PHASES = ("A", "B", "C")


def brute_force(devices, capacities):
    """朴素全枚举参照实现：返回 (最优差值, 字典序最小相位序列) 或 None。"""
    ordered = sorted(devices, key=lambda d: d.code)
    allowed = [tuple(p for p in PHASES if p in d.allowed) for d in ordered]
    best = None
    for combo in itertools.product(*allowed):
        loads = {p: 0 for p in PHASES}
        ok = True
        for device, phase in zip(ordered, combo):
            loads[phase] += device.power
            if loads[phase] > capacities[phase]:
                ok = False
                break
        if not ok:
            continue
        imbalance = max(loads.values()) - min(loads.values())
        if best is None or imbalance < best[0]:
            best = (imbalance, combo, dict(loads))
    return best


class SolveBasicTest(unittest.TestCase):
    def test_balanced_assignment(self):
        devices = [
            Device("D1", 10, ("A", "B", "C")),
            Device("D2", 20, ("A", "B", "C")),
            Device("D3", 30, ("A", "B", "C")),
        ]
        result = solve(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        # 10/20/30 无法完全均分（无子集和为 20 的拆分），最优差值为 20
        self.assertEqual(result.imbalance, 20)
        self.assertEqual(result.loads, {"A": 10, "B": 20, "C": 30})
        # 字典序最小：D1->A, D2->B, D3->C
        self.assertEqual(result.assignment, {"D1": "A", "D2": "B", "D3": "C"})

    def test_perfect_balance(self):
        devices = [Device(f"D{i}", 10, PHASES) for i in range(1, 4)]
        result = solve(devices, {"A": 10, "B": 10, "C": 10})
        self.assertTrue(result.feasible)
        self.assertEqual(result.imbalance, 0)
        self.assertEqual(result.loads, {"A": 10, "B": 10, "C": 10})

    def test_restricted_phases(self):
        devices = [
            Device("X", 5, ("A",)),
            Device("Y", 7, ("B", "C")),
            Device("Z", 6, ("A", "C")),
        ]
        result = solve(devices, {"A": 20, "B": 20, "C": 20})
        self.assertTrue(result.feasible)
        self.assertEqual(result.assignment["X"], "A")
        self.assertEqual(result.loads["A"] + result.loads["B"] + result.loads["C"], 18)

    def test_infeasible_capacity(self):
        devices = [Device("D1", 50, ("A",)), Device("D2", 60, ("A",))]
        result = solve(devices, {"A": 100, "B": 0, "C": 0})
        self.assertFalse(result.feasible)
        self.assertIsNone(result.assignment)
        self.assertIsNone(result.loads)
        self.assertIsNone(result.imbalance)

    def test_infeasible_single_device_over_capacity(self):
        devices = [Device("BIG", 101, PHASES)]
        result = solve(devices, {"A": 100, "B": 100, "C": 100})
        self.assertFalse(result.feasible)

    def test_lexicographic_tie_break(self):
        # 两台同功率设备：多种最优解，应取编号排序后相位序列字典序最小者
        devices = [Device("B2", 10, PHASES), Device("A1", 10, PHASES)]
        result = solve(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        self.assertEqual(result.imbalance, 10)
        # 按编号排序为 A1, B2；字典序最小序列为 (A, B)
        self.assertEqual(result.assignment, {"A1": "A", "B2": "B"})

    def test_device_ids_sorted_as_strings(self):
        devices = [
            Device("DEV-10", 4, PHASES),
            Device("DEV-2", 4, PHASES),
            Device("DEV-1", 4, PHASES),
        ]
        result = solve(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        self.assertEqual(result.imbalance, 0)
        # 字符串排序 DEV-1 < DEV-10 < DEV-2，字典序最小序列 (A, B, C)
        self.assertEqual(
            result.assignment, {"DEV-1": "A", "DEV-10": "B", "DEV-2": "C"}
        )

    def test_capacity_forces_imbalance(self):
        devices = [Device("D1", 8, PHASES), Device("D2", 8, PHASES)]
        result = solve(devices, {"A": 8, "B": 8, "C": 0})
        self.assertTrue(result.feasible)
        self.assertEqual(result.loads, {"A": 8, "B": 8, "C": 0})
        self.assertEqual(result.imbalance, 8)


class SolveFuzzTest(unittest.TestCase):
    """与朴素全枚举参照实现做随机对拍。"""

    def test_against_brute_force(self):
        rng = random.Random(20260924)
        for case in range(300):
            n = rng.randint(1, 7)
            devices = []
            for i in range(n):
                allowed = tuple(p for p in PHASES if rng.random() < 0.7) or ("A",)
                devices.append(Device(f"DEV-{i}", rng.randint(1, 20), allowed))
            capacities = {p: rng.randint(0, 60) for p in PHASES}

            expected = brute_force(devices, capacities)
            result = solve(devices, capacities)

            if expected is None:
                self.assertFalse(result.feasible, f"case {case}: 应为无可行解")
                continue

            self.assertTrue(result.feasible, f"case {case}: 应有可行解")
            exp_imbalance, exp_combo, exp_loads = expected
            self.assertEqual(result.imbalance, exp_imbalance, f"case {case}: 差值不符")
            self.assertEqual(result.loads, exp_loads, f"case {case}: 负载不符")
            ordered = sorted(devices, key=lambda d: d.code)
            actual_combo = tuple(result.assignment[d.code] for d in ordered)
            self.assertEqual(actual_combo, exp_combo, f"case {case}: 字典序最小方案不符")

    def test_twelve_devices_performance(self):
        # 12 台设备、全相位可接、大容量：最恶劣情形，应在可接受时间内完成
        devices = [Device(f"D{i:02d}", 2**i, PHASES) for i in range(12)]
        result = solve(devices, {"A": 10**6, "B": 10**6, "C": 10**6})
        self.assertTrue(result.feasible)
        expected = brute_force(devices, {"A": 10**6, "B": 10**6, "C": 10**6})
        self.assertEqual(result.imbalance, expected[0])
        ordered = sorted(devices, key=lambda d: d.code)
        self.assertEqual(
            tuple(result.assignment[d.code] for d in ordered), expected[1]
        )


def brute_force_upgrade(devices, capacities):
    """扩容问题的朴素全枚举：返回 (总扩容, 单相最大扩容, 相位序列, 负载, 扩容量)。"""
    ordered = sorted(devices, key=lambda d: d.code)
    allowed = [tuple(p for p in PHASES if p in d.allowed) for d in ordered]
    best = None
    for combo in itertools.product(*allowed):
        loads = {p: 0 for p in PHASES}
        for device, phase in zip(ordered, combo):
            loads[phase] += device.power
        upgrades = {p: max(0, loads[p] - capacities[p]) for p in PHASES}
        key = (
            sum(upgrades.values()),
            max(upgrades.values()),
            combo,
        )
        if best is None or key < best[0]:
            best = (key, dict(loads), dict(upgrades))
    return best


class CapacityUpgradeTest(unittest.TestCase):
    def test_single_oversized_device(self):
        devices = [Device("D1", 50, ("A", "B", "C"))]
        result = solve_capacity_upgrade(devices, {"A": 5, "B": 0, "C": 0})
        # 总量 45 可全部加在 A 相；字典序最小为 A
        self.assertEqual(result.upgrades, {"A": 45, "B": 0, "C": 0})
        self.assertEqual(result.suggested_capacities, {"A": 50, "B": 0, "C": 0})
        self.assertEqual(result.assignment, {"D1": "A"})
        self.assertEqual(result.loads, {"A": 50, "B": 0, "C": 0})
        self.assertEqual(result.total_upgrade, 45)
        self.assertEqual(result.max_upgrade, 45)

    def test_forced_phase_requires_upgrade(self):
        devices = [Device("X", 50, ("A",)), Device("Y", 60, ("A",))]
        result = solve_capacity_upgrade(devices, {"A": 100, "B": 0, "C": 0})
        # 两台设备都只能接 A，A 相必须扩至 110（总量、单相最大均为 10）
        self.assertEqual(result.upgrades, {"A": 10, "B": 0, "C": 0})
        self.assertEqual(result.loads, {"A": 110, "B": 0, "C": 0})
        self.assertEqual(result.total_upgrade, 10)
        self.assertEqual(result.max_upgrade, 10)

    def test_minimize_total_then_max_single_phase(self):
        # 总量 20 最优：可拆成 (20,0,0) 或 (10,10,0) 等，应取单相最大最小的
        # (10,10,0)；同 max 下相位序列字典序最小，即两台设备都接 A（(A,A)
        # 字典序小于 (A,B)），扩容量 (20,0,0) 虽使两台同接 A，但 max=20
        # 并非最优 —— 故最优 max=10 由 (A,B) 实现。
        devices = [
            Device("D1", 10, ("A", "B", "C")),
            Device("D2", 10, ("A", "B", "C")),
        ]
        result = solve_capacity_upgrade(devices, {"A": 0, "B": 0, "C": 0})
        self.assertEqual(result.total_upgrade, 20)
        self.assertEqual(result.max_upgrade, 10)
        # max=10 的方案要求两设备落在不同相位；序列字典序最小为 (A,B)
        self.assertEqual(result.upgrades, {"A": 10, "B": 10, "C": 0})
        self.assertEqual(result.assignment, {"D1": "A", "D2": "B"})
        self.assertEqual(result.loads, {"A": 10, "B": 10, "C": 0})

    def test_lexicographic_tie_break(self):
        # 两台同功率全相位设备，原容量充裕时无需扩容，max 同为 0，
        # 此时没有均衡约束，取纯字典序最小相位序列 (A,A)
        devices = [Device("B2", 10, PHASES), Device("A1", 10, PHASES)]
        result = solve_capacity_upgrade(devices, {"A": 100, "B": 100, "C": 100})
        self.assertEqual(result.upgrades, {"A": 0, "B": 0, "C": 0})
        self.assertEqual(result.total_upgrade, 0)
        self.assertEqual(result.max_upgrade, 0)
        self.assertEqual(result.assignment, {"A1": "A", "B2": "A"})

    def test_restricted_phases_shape_upgrade(self):
        devices = [
            Device("X", 30, ("A",)),
            Device("Y", 30, ("B",)),
            Device("Z", 30, ("C",)),
        ]
        result = solve_capacity_upgrade(devices, {"A": 10, "B": 10, "C": 10})
        # 每相被迫扩 20：总量 60，单相最大 20
        self.assertEqual(result.upgrades, {"A": 20, "B": 20, "C": 20})
        self.assertEqual(result.total_upgrade, 60)
        self.assertEqual(result.max_upgrade, 20)
        self.assertEqual(result.assignment, {"X": "A", "Y": "B", "Z": "C"})

    def test_existing_capacity_reused(self):
        # 总量最小为 8：两台都接 A（扩 8，序列 (A,A)）与分接 A/B（扩 B 8，
        # 序列 (A,B)）max 相同，字典序取 (A,A)
        devices = [
            Device("D1", 8, PHASES),
            Device("D2", 8, PHASES),
        ]
        result = solve_capacity_upgrade(devices, {"A": 8, "B": 0, "C": 0})
        self.assertEqual(result.total_upgrade, 8)
        self.assertEqual(result.max_upgrade, 8)
        self.assertEqual(result.upgrades, {"A": 8, "B": 0, "C": 0})
        self.assertEqual(result.assignment, {"D1": "A", "D2": "A"})
        self.assertEqual(result.loads, {"A": 16, "B": 0, "C": 0})


class CapacityUpgradeFuzzTest(unittest.TestCase):
    """扩容求解与朴素全枚举做随机对拍。"""

    def test_against_brute_force(self):
        rng = random.Random(20260925)
        for case in range(400):
            n = rng.randint(1, 7)
            devices = []
            for i in range(n):
                allowed = tuple(p for p in PHASES if rng.random() < 0.7) or ("A",)
                devices.append(Device(f"DEV-{i}", rng.randint(1, 20), allowed))
            capacities = {p: rng.randint(0, 40) for p in PHASES}

            expected = brute_force_upgrade(devices, capacities)
            result = solve_capacity_upgrade(devices, capacities)

            (exp_total, exp_max, exp_combo), exp_loads, exp_upgrades = expected
            self.assertEqual(
                result.total_upgrade, exp_total, f"case {case}: 扩容总量不符"
            )
            self.assertEqual(
                result.max_upgrade, exp_max, f"case {case}: 单相最大扩容不符"
            )
            self.assertEqual(result.upgrades, exp_upgrades, f"case {case}: 扩容量不符")
            self.assertEqual(result.loads, exp_loads, f"case {case}: 负载不符")
            ordered = sorted(devices, key=lambda d: d.code)
            actual_combo = tuple(result.assignment[d.code] for d in ordered)
            self.assertEqual(actual_combo, exp_combo, f"case {case}: 方案不符")
            # 一致性校验：扩容量与负载、建议容量自洽
            for p in PHASES:
                self.assertEqual(
                    result.suggested_capacities[p],
                    result.original_capacities[p] + result.upgrades[p],
                )
                self.assertGreaterEqual(
                    result.suggested_capacities[p], result.loads[p]
                )

    def test_twelve_devices_performance(self):
        # 12 台设备、全相位可接、原容量为 0：方案空间最大，应快速完成
        devices = [Device(f"D{i:02d}", 2**i, PHASES) for i in range(12)]
        result = solve_capacity_upgrade(devices, {"A": 0, "B": 0, "C": 0})
        expected = brute_force_upgrade(devices, {"A": 0, "B": 0, "C": 0})
        (exp_total, exp_max, exp_combo), _, _ = expected
        self.assertEqual(result.total_upgrade, exp_total)
        self.assertEqual(result.max_upgrade, exp_max)
        ordered = sorted(devices, key=lambda d: d.code)
        self.assertEqual(
            tuple(result.assignment[d.code] for d in ordered), exp_combo
        )

    def test_twelve_devices_feasible_performance(self):
        # 12 台设备在原容量下本就可行（扩容为 0）：第二遍仍须在全部可行方案
        # 中选出字典序最小者，枚举规模最大
        devices = [
            Device(f"D{i:02d}", 2 * i + 1, PHASES) for i in range(12)
        ]
        capacities = {"A": 1000, "B": 1000, "C": 1000}
        result = solve_capacity_upgrade(devices, capacities)
        expected = brute_force_upgrade(devices, capacities)
        (exp_total, exp_max, exp_combo), exp_loads, _ = expected
        self.assertEqual(result.total_upgrade, exp_total)
        self.assertEqual(result.max_upgrade, exp_max)
        self.assertEqual(result.loads, exp_loads)
        ordered = sorted(devices, key=lambda d: d.code)
        self.assertEqual(
            tuple(result.assignment[d.code] for d in ordered), exp_combo
        )


def brute_force_robust(devices, capacities):
    """稳健预演的朴素全枚举：返回 (最坏负载差, 相位序列, 下界, 上界) 或 None。"""
    ordered = sorted(devices, key=lambda d: d.code)
    allowed = [tuple(p for p in PHASES if p in d.allowed) for d in ordered]
    best = None
    for combo in itertools.product(*allowed):
        lo = {p: 0 for p in PHASES}
        hi = {p: 0 for p in PHASES}
        for device, phase in zip(ordered, combo):
            lo[phase] += device.min_power
            hi[phase] += device.max_power
        if any(hi[p] > capacities[p] for p in PHASES):
            continue
        worst = max(hi.values()) - min(lo.values())
        key = (worst, combo)
        if best is None or key < best[0]:
            best = (key, dict(lo), dict(hi))
    return best


class RobustSolveBasicTest(unittest.TestCase):
    def test_robust_ranges(self):
        devices = [
            RobustDevice("D1", 8, 12, PHASES),
            RobustDevice("D2", 8, 12, PHASES),
            RobustDevice("D3", 8, 12, PHASES),
        ]
        result = solve_robust(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        # 每相恰好一台：上界 12，下界 8；最坏差 = 12 - 8 = 4
        self.assertEqual(result.load_lower, {"A": 8, "B": 8, "C": 8})
        self.assertEqual(result.load_upper, {"A": 12, "B": 12, "C": 12})
        self.assertEqual(result.worst_imbalance, 4)
        self.assertEqual(result.assignment, {"D1": "A", "D2": "B", "D3": "C"})

    def test_upper_bound_capacity_constraint(self):
        # 容量按上界校验：下界放得下但上界超容 -> 无稳健可行方案
        devices = [RobustDevice("D1", 5, 10, ("A",))]
        result = solve_robust(devices, {"A": 9, "B": 0, "C": 0})
        self.assertFalse(result.feasible)
        self.assertIsNone(result.assignment)
        self.assertIsNone(result.load_lower)
        self.assertIsNone(result.load_upper)
        self.assertIsNone(result.worst_imbalance)

    def test_worst_imbalance_uses_cross_phase_extremes(self):
        # 一台宽区间设备 + 一台只能接 A 的小设备：最坏情形由「大设备取上界」
        # 的相与「空载相（负载恒为 0）」共同决定
        devices = [
            RobustDevice("BIG", 10, 40, ("A", "B", "C")),
            RobustDevice("SMALL", 5, 5, ("A",)),
        ]
        result = solve_robust(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        # BIG 独占 B：B 上界 40、C 恒空为 0 -> 最坏差 40
        self.assertEqual(result.worst_imbalance, 40)
        self.assertEqual(result.assignment, {"BIG": "B", "SMALL": "A"})
        self.assertEqual(result.load_lower, {"A": 5, "B": 10, "C": 0})
        self.assertEqual(result.load_upper, {"A": 5, "B": 40, "C": 0})

    def test_lexicographic_tie_break(self):
        # 两台区间相同的全相位设备：最坏差恒为上界，最优序列字典序最小
        devices = [
            RobustDevice("B2", 10, 10, PHASES),
            RobustDevice("A1", 10, 10, PHASES),
        ]
        result = solve_robust(devices, {"A": 100, "B": 100, "C": 100})
        self.assertTrue(result.feasible)
        self.assertEqual(result.worst_imbalance, 10)
        self.assertEqual(result.assignment, {"A1": "A", "B2": "B"})

    def test_restricted_phase_infeasible(self):
        devices = [
            RobustDevice("X", 10, 20, ("A",)),
            RobustDevice("Y", 10, 20, ("A",)),
        ]
        result = solve_robust(devices, {"A": 39, "B": 100, "C": 100})
        self.assertFalse(result.feasible)

    def test_fixed_power_matches_ordinary_solve(self):
        # min_power == max_power 时应与普通 solve 的最优结果一致
        devices = [
            Device("D1", 10, PHASES),
            Device("D2", 20, PHASES),
            Device("D3", 30, ("B", "C")),
        ]
        robust = [
            RobustDevice(d.code, d.power, d.power, d.allowed) for d in devices
        ]
        capacities = {"A": 100, "B": 100, "C": 100}
        r1 = solve(devices, capacities)
        r2 = solve_robust(robust, capacities)
        self.assertEqual(r1.imbalance, r2.worst_imbalance)
        self.assertEqual(r1.assignment, r2.assignment)
        self.assertEqual(r1.loads, r2.load_lower)
        self.assertEqual(r1.loads, r2.load_upper)

    def test_single_device_worst_imbalance_zero(self):
        devices = [RobustDevice("ONLY", 3, 9, PHASES)]
        result = solve_robust(devices, {"A": 9, "B": 9, "C": 9})
        self.assertTrue(result.feasible)
        # 字典序最小接 A；空载相负载恒为 0 -> 最坏差 9
        self.assertEqual(result.assignment, {"ONLY": "A"})
        self.assertEqual(result.load_upper, {"A": 9, "B": 0, "C": 0})
        self.assertEqual(result.worst_imbalance, 9)


class RobustSolveFuzzTest(unittest.TestCase):
    """与朴素全枚举参照实现做随机对拍。"""

    def test_against_brute_force(self):
        rng = random.Random(20260925)
        for case in range(600):
            n = rng.randint(1, 7)
            devices = []
            for i in range(n):
                low = rng.randint(1, 18)
                high = low + rng.randint(0, 12)
                allowed = tuple(p for p in PHASES if rng.random() < 0.7) or ("A",)
                devices.append(RobustDevice(f"DEV-{i}", low, high, allowed))
            capacities = {p: rng.randint(0, 55) for p in PHASES}

            expected = brute_force_robust(devices, capacities)
            result = solve_robust(devices, capacities)

            if expected is None:
                self.assertFalse(result.feasible, f"case {case}: 应为无解")
                continue

            self.assertTrue(result.feasible, f"case {case}: 应有解")
            (exp_worst, exp_combo), exp_lo, exp_hi = expected
            self.assertEqual(
                result.worst_imbalance, exp_worst, f"case {case}: 最坏负载差不符"
            )
            self.assertEqual(result.load_lower, exp_lo, f"case {case}: 下界不符")
            self.assertEqual(result.load_upper, exp_hi, f"case {case}: 上界不符")
            ordered = sorted(devices, key=lambda d: d.code)
            actual_combo = tuple(result.assignment[d.code] for d in ordered)
            self.assertEqual(
                actual_combo, exp_combo, f"case {case}: 字典序最小方案不符"
            )

    def test_twelve_devices_performance(self):
        # 12 台设备、全相位可接、大容量：最恶劣枚举规模，应快速完成
        devices = [
            RobustDevice(f"D{i:02d}", 2 * i + 1, 2 * i + 5, PHASES)
            for i in range(12)
        ]
        capacities = {"A": 10**6, "B": 10**6, "C": 10**6}
        result = solve_robust(devices, capacities)
        expected = brute_force_robust(devices, capacities)
        self.assertTrue(result.feasible)
        self.assertEqual(result.worst_imbalance, expected[0][0])
        ordered = sorted(devices, key=lambda d: d.code)
        self.assertEqual(
            tuple(result.assignment[d.code] for d in ordered), expected[0][1]
        )

    def test_twelve_devices_infeasible_performance(self):
        # 12 台宽区间设备、容量紧张：无解时也应快速剪枝完成
        devices = [
            RobustDevice(f"D{i:02d}", 1, 20, PHASES) for i in range(12)
        ]
        result = solve_robust(devices, {"A": 79, "B": 79, "C": 79})
        self.assertFalse(result.feasible)


if __name__ == "__main__":
    unittest.main()
