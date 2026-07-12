"""마스터 데이터 생성.

완제품, 자재, BOM, 거래처, 공급사, 설비 마스터를 생성한다.
수요 가중치와 라우팅 배정을 통해 EQ-03이 고부하 병목 라인이 되도록
구성한다 (저하 이상이 납기 지연으로 이어지게 하기 위한 조건).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .config import SimConfig


@dataclass
class FinishedGood:
    """완제품 마스터."""

    item_code: str
    item_name: str
    item_group: str
    equip_code: str
    cycle_time_s: float
    demand_weight: float
    base_defect_rate: float
    unit_price: int
    # 품목군 개편 이전 그룹 (개편 대상이 아니면 None)
    item_group_old: Optional[str] = None


@dataclass
class Material:
    """자재 마스터."""

    item_code: str
    item_name: str
    supplier_code: str
    unit_cost: int
    lead_time_days: int
    reorder_point: float = 0.0
    order_lot: float = 0.0


@dataclass
class MasterData:
    """마스터 데이터 묶음."""

    finished_goods: Dict[str, FinishedGood] = field(default_factory=dict)
    materials: Dict[str, Material] = field(default_factory=dict)
    bom: Dict[str, Dict[str, int]] = field(default_factory=dict)
    customers: List[dict] = field(default_factory=list)
    suppliers: List[dict] = field(default_factory=list)
    equipment: List[dict] = field(default_factory=list)


_GROUPS = ["제어보드", "전원모듈", "센서유닛", "커넥터ASSY", "릴레이모듈"]
_CUSTOMER_NAMES = [
    "대한전자", "서울테크", "미래산업", "한빛시스템", "동양전기", "글로벌부품",
    "신성정밀", "우진테크", "제일전자", "코리아일렉", "성진산업", "태광시스템",
    "현대오토텍", "부성전자", "삼우기전", "에이스전장", "디케이테크", "유일전자",
    "정도산업", "케이엠시스", "일신전기", "한서테크", "동남전자", "명성기연",
    "지엔에스", "삼익전장", "우리시스템", "가온테크", "새한전자", "청우산업",
]
_SUPPLIER_NAMES = [
    "동일소재", "한국커넥터", "대성부품", "신영케미칼", "우주금속", "제이피씨비",
    "삼화콘덴서상사", "명진저항", "한라반도체유통", "성원와이어", "동부몰딩",
    "케이알소자", "태성인쇄회로", "광명단자", "유성패키징",
]


def build_master(cfg: SimConfig, rng: np.random.Generator) -> MasterData:
    """마스터 데이터를 생성한다.

    Args:
        cfg: 시뮬레이션 설정.
        rng: 난수 생성기.

    Returns:
        생성된 마스터 데이터.
    """
    md = MasterData()

    md.customers = [
        {"customer_code": f"C-{i + 1:03d}", "customer_name": _CUSTOMER_NAMES[i],
         "region": rng.choice(["수도권", "충청", "영남", "호남"]).item()}
        for i in range(cfg.n_customers)
    ]
    md.suppliers = [
        {"supplier_code": f"S-{i + 1:02d}", "supplier_name": _SUPPLIER_NAMES[i]}
        for i in range(cfg.n_suppliers)
    ]
    md.equipment = [
        {"equip_code": f"EQ-{i + 1:02d}", "equip_name": f"조립라인 {i + 1}호기",
         "install_year": int(rng.integers(2015, 2023))}
        for i in range(cfg.n_equipment)
    ]

    _build_materials(cfg, rng, md)
    _build_finished_goods(cfg, rng, md)
    _build_bom(cfg, rng, md)
    _set_reorder_policy(cfg, md)
    _apply_master_reorg(cfg, rng, md)
    return md


def _build_materials(cfg: SimConfig, rng: np.random.Generator,
                     md: MasterData) -> None:
    """자재 마스터를 생성한다. 공급사는 자재당 1곳(단일 소싱)으로 단순화."""
    lo, hi = cfg.material_lead_time_range
    for i in range(cfg.n_materials):
        code = f"M-{i + 1:04d}"
        supplier = f"S-{int(rng.integers(1, cfg.n_suppliers + 1)):02d}"
        md.materials[code] = Material(
            item_code=code,
            item_name=f"자재{i + 1:04d}",
            supplier_code=supplier,
            unit_cost=int(rng.integers(50, 5000)),
            lead_time_days=int(rng.integers(lo, hi + 1)),
        )


def _build_finished_goods(cfg: SimConfig, rng: np.random.Generator,
                          md: MasterData) -> None:
    """완제품 마스터를 생성하고 설비에 배정한다.

    기대 일일 부하(초)를 품목별로 계산하고, EQ-03의 목표 가동률(88%)에
    도달할 때까지 품목을 채운다. 나머지는 부하가 가장 낮은 라인에
    순차 배정해 라인 간 부하를 평준화한다.
    """
    weights = rng.pareto(2.0, cfg.n_finished_goods) + 0.3
    ct_lo, ct_hi = cfg.cycle_time_range
    cycle_times = rng.uniform(ct_lo, ct_hi, cfg.n_finished_goods)

    # 품목별 기대 일일 생산 소요시간(초) = 기대 일일 수량 x 사이클타임
    avg_lines = 1.5
    avg_qty = float(np.mean(cfg.order_qty_range))
    daily_units_total = cfg.orders_per_workday * avg_lines * avg_qty
    daily_units = weights / weights.sum() * daily_units_total
    daily_seconds = daily_units * cycle_times

    capacity_s = cfg.operating_minutes * 60.0
    target_bn_load = capacity_s * cfg.bottleneck_target_util

    equip_load = {f"EQ-{i + 1:02d}": 0.0 for i in range(cfg.n_equipment)}
    assign: Dict[int, str] = {}
    bn = "EQ-03"
    for idx in rng.permutation(cfg.n_finished_goods):
        load = float(daily_seconds[idx])
        if equip_load[bn] + load <= target_bn_load:
            assign[int(idx)] = bn
            equip_load[bn] += load
        else:
            others = {k: v for k, v in equip_load.items() if k != bn}
            dest = min(others, key=others.get)
            assign[int(idx)] = dest
            equip_load[dest] += load

    for i in range(cfg.n_finished_goods):
        code = f"F-{i + 1:04d}"
        group = _GROUPS[i % len(_GROUPS)]
        md.finished_goods[code] = FinishedGood(
            item_code=code,
            item_name=f"{group} {i + 1:02d}형",
            item_group=group,
            equip_code=assign[i],
            cycle_time_s=float(cycle_times[i]),
            demand_weight=float(weights[i]),
            base_defect_rate=float(rng.uniform(0.005, 0.02)),
            unit_price=int(rng.integers(3000, 30000)),
        )


def _build_bom(cfg: SimConfig, rng: np.random.Generator,
               md: MasterData) -> None:
    """BOM을 생성한다. 완제품당 자재 4~8종, 소요량 1~4."""
    mat_codes = list(md.materials.keys())
    for fg_code in md.finished_goods:
        n_comp = int(rng.integers(4, 9))
        comps = rng.choice(mat_codes, size=n_comp, replace=False)
        md.bom[fg_code] = {m: int(rng.integers(1, 5)) for m in comps}


def _set_reorder_policy(cfg: SimConfig, md: MasterData) -> None:
    """자재별 발주점과 발주 로트를 계산한다.

    일평균 소요량을 수요 가중치와 BOM으로 추정하고,
    발주점 = 일평균소요 x (리드타임 + 안전재고일수)로 둔다.
    핵심: 이 발주점은 '기본' 리드타임 기준으로 고정되어 있어
    공급사 지연 발생 시 구조적으로 결품이 반복된다.
    """
    total_weight = sum(f.demand_weight for f in md.finished_goods.values())
    avg_lines = 1.5
    avg_qty = float(np.mean(cfg.order_qty_range))
    daily_fg_units = (cfg.orders_per_workday * avg_lines * avg_qty
                      / total_weight)

    daily_usage: Dict[str, float] = {m: 0.0 for m in md.materials}
    for fg_code, comps in md.bom.items():
        fg_daily = md.finished_goods[fg_code].demand_weight * daily_fg_units
        for m_code, qty in comps.items():
            daily_usage[m_code] += fg_daily * qty

    for m_code, mat in md.materials.items():
        usage = max(daily_usage[m_code], 1.0)
        # 수요 변동 대비 20% 버퍼. 기본 상태에서 결품이 드물게 유지되어야
        # 공급 지연(체인 B) 발생 시의 결품 급증이 명확한 신호가 된다.
        mat.reorder_point = usage * (mat.lead_time_days
                                     + cfg.safety_stock_days) * 1.2
        mat.order_lot = usage * cfg.order_lot_days


def _apply_master_reorg(cfg: SimConfig, rng: np.random.Generator,
                        md: MasterData) -> None:
    """품목군 체계 개편을 적용한다 (SCD Type 2 소재).

    무작위로 선정된 완제품들의 그룹을 신설 그룹으로 변경하고,
    이전 그룹을 item_group_old에 보존한다. items 테이블에는 최종
    상태가, item_change_log에는 변경 이력이 출력된다.
    """
    fg_codes = list(md.finished_goods.keys())
    targets = rng.choice(fg_codes, size=cfg.reorg.n_items, replace=False)
    for code in targets:
        fg = md.finished_goods[str(code)]
        fg.item_group_old = fg.item_group
        fg.item_group = cfg.reorg.new_group
