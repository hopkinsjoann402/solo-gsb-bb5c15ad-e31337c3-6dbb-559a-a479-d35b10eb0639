"""API 请求 / 响应数据模型（Pydantic v2）。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Phase = Literal["A", "B", "C"]

_PHASE_ORDER = {"A": 0, "B": 1, "C": 2}


class Capacities(BaseModel):
    """A/B/C 各相容量（非负整数）。"""

    A: int = Field(..., ge=0, description="A 相容量")
    B: int = Field(..., ge=0, description="B 相容量")
    C: int = Field(..., ge=0, description="C 相容量")


class DeviceIn(BaseModel):
    """单台待分配设备。"""

    id: str = Field(..., min_length=1, max_length=64, description="设备唯一编号")
    power: int = Field(..., gt=0, description="设备功率（正整数）")
    allowed_phases: list[Phase] = Field(
        ..., min_length=1, description="可接入相位集合，非空，元素取自 A/B/C"
    )

    @field_validator("allowed_phases")
    @classmethod
    def _dedupe_and_order(cls, value: list[str]) -> list[str]:
        """去重并按 A/B/C 固定顺序排列。"""
        return sorted(set(value), key=_PHASE_ORDER.__getitem__)


class CalculationRequest(BaseModel):
    """创建一次排布计算的请求体。"""

    capacities: Capacities
    devices: list[DeviceIn] = Field(
        ..., min_length=1, max_length=12, description="设备列表，1~12 台"
    )

    @model_validator(mode="after")
    def _unique_device_ids(self) -> "CalculationRequest":
        _require_unique_device_ids(self.devices)
        return self


def _require_unique_device_ids(devices: list) -> None:
    """设备编号必须唯一。"""
    ids = [d.id for d in devices]
    if len(ids) != len(set(ids)):
        raise ValueError("设备编号必须唯一")


class Loads(BaseModel):
    """A/B/C 各相负载。"""

    A: int
    B: int
    C: int


class AssignmentItem(BaseModel):
    """单台设备的分配结果。"""

    device_id: str
    phase: Phase


class CalculationSummary(BaseModel):
    """计算记录摘要（列表接口使用）。"""

    id: int
    created_at: str
    capacities: Loads
    feasible: bool
    imbalance: Optional[int] = None
    loads: Optional[Loads] = None


class CalculationResult(CalculationSummary):
    """计算结果（创建接口返回）。"""

    message: str
    assignments: list[AssignmentItem]


class DeviceRecord(BaseModel):
    """持久化的设备输入及其分配结果。"""

    device_id: str
    power: int
    allowed_phases: list[str]
    assigned_phase: Optional[Phase] = None


class CalculationDetail(CalculationResult):
    """计算记录详情（含原始输入）。"""

    devices: list[DeviceRecord]


class CapacityUpgradeResult(BaseModel):
    """容量扩容建议结果（原计算记录不做任何修改）。"""

    id: int
    created_at: str
    original_capacities: Loads = Field(..., description="原各相容量")
    capacity_upgrades: Loads = Field(..., description="各相需要增加的非负整数容量")
    suggested_capacities: Loads = Field(..., description="扩容后的建议容量")
    total_upgrade: int = Field(..., ge=0, description="三相扩容总量")
    max_upgrade: int = Field(..., ge=0, description="单相最大扩容量")
    assignments: list[AssignmentItem] = Field(..., description="扩容后的设备分配")
    loads: Loads = Field(..., description="建议容量下各相的实际负载")
    message: str


class RobustDeviceIn(BaseModel):
    """稳健预演中的单台设备：功率在正整数区间 [min_power, max_power] 内变化。"""

    id: str = Field(..., min_length=1, max_length=64, description="设备唯一编号")
    min_power: int = Field(..., gt=0, description="设备功率下界（正整数）")
    max_power: int = Field(..., gt=0, description="设备功率上界（正整数，不小于下界）")
    allowed_phases: list[Phase] = Field(
        ..., min_length=1, description="可接入相位集合，非空，元素取自 A/B/C"
    )

    @field_validator("allowed_phases")
    @classmethod
    def _dedupe_and_order(cls, value: list[str]) -> list[str]:
        """去重并按 A/B/C 固定顺序排列。"""
        return sorted(set(value), key=_PHASE_ORDER.__getitem__)

    @model_validator(mode="after")
    def _min_not_greater_than_max(self) -> "RobustDeviceIn":
        if self.min_power > self.max_power:
            raise ValueError(
                f"设备 {self.id} 的 min_power 不能大于 max_power："
                f"{self.min_power} > {self.max_power}"
            )
        return self


class RobustPreviewRequest(BaseModel):
    """稳健分相预演请求体（仅预演，不写入历史计算记录）。"""

    capacities: Capacities
    devices: list[RobustDeviceIn] = Field(
        ..., min_length=1, max_length=12, description="设备列表，1~12 台"
    )

    @model_validator(mode="after")
    def _unique_device_ids(self) -> "RobustPreviewRequest":
        _require_unique_device_ids(self.devices)
        return self


class PhaseLoadRange(BaseModel):
    """单相负载的取值范围。"""

    lower: int = Field(..., ge=0, description="该相负载下界（该相设备功率下界之和）")
    upper: int = Field(..., ge=0, description="该相负载上界（该相设备功率上界之和）")


class LoadRanges(BaseModel):
    """A/B/C 各相负载的下界与上界。"""

    A: PhaseLoadRange
    B: PhaseLoadRange
    C: PhaseLoadRange


class RobustPreviewResult(BaseModel):
    """稳健分相预演结果（不持久化，无记录 id / 创建时间）。"""

    capacities: Loads = Field(..., description="各相容量")
    feasible: bool = Field(..., description="是否存在稳健可行的分配")
    worst_imbalance: Optional[int] = Field(
        None,
        ge=0,
        description="最坏负载差：任意功率组合下三相最大负载与最小负载之差的最大值",
    )
    load_ranges: Optional[LoadRanges] = Field(
        None, description="该分配下各相负载的下界与上界"
    )
    assignments: list[AssignmentItem] = Field(
        default_factory=list, description="设备的稳健相位分配（按设备编号排序）"
    )
    message: str
