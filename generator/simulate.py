"""일 단위 운영 시뮬레이션.

수주 -> 작업지시 -> 자재 확인(BOM 소요) -> 생산 -> 검사 -> 출하의
흐름을 하루씩 진행한다. 두 개의 이상 인과 체인을 내장한다.

체인 A: EQ-03 사이클타임 점진 저하 -> 병목 라인 처리능력 하락
        -> 대기열 누적 -> 해당 품목군 납기 지연.
체인 B: S-07 리드타임 급증 -> 발주점이 구 리드타임 기준이라 반복 결품
        -> 해당 자재 포함 품목의 작업지시 보류 -> 착수 지연 -> 납기 지연.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np

from .config import SimConfig
from .master import MasterData

_WORKERS = ["김민수", "이서연", "박지훈", "최은영", "정우진",
            "한소미", "오태양", "임가람", "송재헌", "윤보라"]
_INSPECTORS = ["강현우", "노지은", "문성호", "배아름"]


@dataclass
class WorkOrder:
    """작업지시 상태 객체."""

    wo_no: str
    order_no: str
    line_no: int
    item_code: str
    equip_code: str
    qty: int
    create_date: date
    release_date: Optional[date] = None
    complete_date: Optional[date] = None
    produced: int = 0
    good: int = 0
    hold_reason: str = ""


@dataclass
class SimResult:
    """시뮬레이션 산출 레코드 묶음. (오염 전의 '진실' 데이터)"""

    sales_order_h: List[dict] = field(default_factory=list)
    sales_order_d: List[dict] = field(default_factory=list)
    work_orders: List[WorkOrder] = field(default_factory=list)
    production_results: List[dict] = field(default_factory=list)
    purchase_orders: List[dict] = field(default_factory=list)
    material_receipts: List[dict] = field(default_factory=list)
    shipments: List[dict] = field(default_factory=list)
    inspections: List[dict] = field(default_factory=list)
    inventory_daily: List[dict] = field(default_factory=list)
    shortage_log: List[dict] = field(default_factory=list)


class Simulator:
    """일 단위 시뮬레이터.

    Args:
        cfg: 시뮬레이션 설정.
        md: 마스터 데이터.
        rng: 난수 생성기.
    """

    def __init__(self, cfg: SimConfig, md: MasterData,
                 rng: np.random.Generator) -> None:
        self.cfg = cfg
        self.md = md
        self.rng = rng
        self.res = SimResult()

        self.stock: Dict[str, float] = {}
        self.on_order: Dict[str, float] = {m: 0.0 for m in md.materials}
        self.pending_receipts: List[dict] = []
        self.queues: Dict[str, List[WorkOrder]] = {
            e["equip_code"]: [] for e in md.equipment}
        self.hold: List[WorkOrder] = []
        self.new_orders_buffer: List[dict] = []
        self.ship_schedule: Dict[date, List[dict]] = {}

        self._seq = {"so": 0, "wo": 0, "po": 0, "sh": 0}
        for m_code, mat in md.materials.items():
            self.stock[m_code] = mat.reorder_point + mat.order_lot

    def run(self) -> SimResult:
        """전체 기간을 하루씩 시뮬레이션한다."""
        d = self.cfg.period_start
        while d <= self.cfg.period_end:
            self._receive_materials(d)
            if self.cfg.is_workday(d):
                self._release_held_orders(d)
                self._create_work_orders(d)
                self._generate_sales_orders(d)
                self._run_purchasing(d)
                self._run_production(d)
                self._run_shipments(d)
                self._snapshot_inventory(d)
            d += timedelta(days=1)
        return self.res

    # ------------------------------------------------------------------
    # 자재 입고 / 구매
    # ------------------------------------------------------------------
    def _receive_materials(self, d: date) -> None:
        """도착 예정 자재를 입고 처리한다. 주말/휴일은 다음 영업일로 이월."""
        if not self.cfg.is_workday(d):
            return
        arrived = [r for r in self.pending_receipts if r["due"] <= d]
        self.pending_receipts = [r for r in self.pending_receipts
                                 if r["due"] > d]
        for r in arrived:
            self.stock[r["material_code"]] += r["qty"]
            self.on_order[r["material_code"]] -= r["qty"]
            self.res.material_receipts.append({
                "po_no": r["po_no"], "material_code": r["material_code"],
                "receipt_date": d, "qty": int(r["qty"]),
            })

    def _lead_time(self, mat_code: str, order_date: date) -> int:
        mat = self.md.materials[mat_code]
        delay = self.cfg.supplier_delay
        if (mat.supplier_code == delay.supplier_code
                and order_date >= delay.start):
            base = delay.new_lead_time
        else:
            base = mat.lead_time_days
        return base + int(self.rng.integers(-1, 2))

    def _run_purchasing(self, d: date) -> None:
        """발주점 방식에 보류 작업지시의 소요량(백로그)을 반영한다.

        가용량(재고 + 발주잔량)이 max(발주점, 백로그 소요)에 미달하면
        부족분을 로트 단위로 올림하여 발주한다. 백로그를 반영하지 않으면
        대형 작업지시가 발주점 위 재고 상태에서 영구 보류되는 교착이
        발생한다.
        """
        backlog: Dict[str, float] = {}
        for wo in self.hold:
            for m, per in self.md.bom[wo.item_code].items():
                backlog[m] = backlog.get(m, 0.0) + per * wo.qty

        for m_code, mat in self.md.materials.items():
            available = self.stock[m_code] + self.on_order[m_code]
            target = max(mat.reorder_point, backlog.get(m_code, 0.0))
            if available >= target:
                continue
            n_lots = int(np.ceil((target - available) / mat.order_lot))
            qty = float(n_lots * np.ceil(mat.order_lot))
            self._seq["po"] += 1
            po_no = f"PO{d:%y%m%d}-{self._seq['po']:04d}"
            lt = self._lead_time(m_code, d)
            self.on_order[m_code] += qty
            self.pending_receipts.append({
                "po_no": po_no, "material_code": m_code,
                "qty": qty, "due": d + timedelta(days=lt),
            })
            self.res.purchase_orders.append({
                "po_no": po_no, "supplier_code": mat.supplier_code,
                "material_code": m_code, "qty": int(qty),
                "order_date": d,
                "promised_date": d + timedelta(days=mat.lead_time_days),
            })

    # ------------------------------------------------------------------
    # 수주 / 작업지시
    # ------------------------------------------------------------------
    def _generate_sales_orders(self, d: date) -> None:
        fg_codes = list(self.md.finished_goods.keys())
        weights = np.array(
            [self.md.finished_goods[c].demand_weight for c in fg_codes])
        weights = weights / weights.sum()

        seasonal = self.cfg.demand_seasonality[d.month - 1]
        n_orders = int(self.rng.poisson(
            self.cfg.orders_per_workday * seasonal))
        for _ in range(n_orders):
            self._seq["so"] += 1
            order_no = f"SO{d:%y%m%d}-{self._seq['so']:04d}"
            cust = self.md.customers[
                int(self.rng.integers(0, len(self.md.customers)))]
            self.res.sales_order_h.append({
                "order_no": order_no,
                "customer_code": cust["customer_code"],
                "order_date": d,
            })
            n_lines = int(self.rng.integers(1, 3))
            items = self.rng.choice(fg_codes, size=n_lines,
                                    replace=False, p=weights)
            for ln, item in enumerate(items, start=1):
                qty = int(self.rng.integers(*self.cfg.order_qty_range))
                due = d + timedelta(
                    days=int(self.rng.integers(*self.cfg.due_days_range)))
                line = {"order_no": order_no, "line_no": ln,
                        "item_code": str(item), "qty": qty, "due_date": due}
                self.res.sales_order_d.append(line)
                self.new_orders_buffer.append(line)

    def _create_work_orders(self, d: date) -> None:
        """전 영업일 수주분에 대해 작업지시를 생성하고 자재를 확인한다."""
        buffered, self.new_orders_buffer = self.new_orders_buffer, []
        for line in buffered:
            self._seq["wo"] += 1
            fg = self.md.finished_goods[line["item_code"]]
            wo = WorkOrder(
                wo_no=f"WO{d:%y%m%d}-{self._seq['wo']:04d}",
                order_no=line["order_no"], line_no=line["line_no"],
                item_code=line["item_code"], equip_code=fg.equip_code,
                qty=line["qty"], create_date=d,
            )
            self.res.work_orders.append(wo)
            self._try_release(wo, d)

    def _try_release(self, wo: WorkOrder, d: date) -> None:
        """BOM 소요 자재가 충분하면 소진 후 설비 대기열에 투입한다."""
        comps = self.md.bom[wo.item_code]
        short = [m for m, per in comps.items()
                 if self.stock[m] < per * wo.qty]
        if short:
            wo.hold_reason = ",".join(sorted(short))
            self.hold.append(wo)
            for m in short:
                self.res.shortage_log.append({
                    "date": d, "wo_no": wo.wo_no, "material_code": m,
                    "required": comps[m] * wo.qty,
                    "on_hand": int(self.stock[m]),
                })
            return
        for m, per in comps.items():
            self.stock[m] -= per * wo.qty
        wo.release_date = d
        wo.hold_reason = ""
        self.queues[wo.equip_code].append(wo)

    def _release_held_orders(self, d: date) -> None:
        held, self.hold = self.hold, []
        for wo in held:
            self._try_release(wo, d)

    # ------------------------------------------------------------------
    # 생산 / 검사 / 출하
    # ------------------------------------------------------------------
    def _degradation_factor(self, equip_code: str, d: date) -> float:
        deg = self.cfg.degradation
        if equip_code != deg.equip_code or d < deg.start:
            return 0.0
        return min(1.0, (d - deg.start).days / deg.ramp_days)

    def _run_production(self, d: date) -> None:
        for equip_code, queue in self.queues.items():
            capacity = self.cfg.operating_minutes * 60.0
            progress = self._degradation_factor(equip_code, d)
            drift = 1.0 + self.cfg.degradation.max_drift * progress
            while queue and capacity > 0:
                wo = queue[0]
                fg = self.md.finished_goods[wo.item_code]
                ct = fg.cycle_time_s * drift * float(
                    self.rng.normal(1.0, 0.03))
                ct = max(ct, 1.0)
                producible = int(capacity // ct)
                if producible <= 0:
                    break
                run_qty = min(wo.qty - wo.good, producible)
                capacity -= run_qty * ct
                defect_rate = fg.base_defect_rate * (
                    1.0 + self.cfg.degradation.defect_uplift * progress
                    if equip_code == self.cfg.degradation.equip_code else 1.0)
                defects = int(self.rng.binomial(run_qty, defect_rate))
                wo.produced += run_qty
                wo.good += run_qty - defects
                self.res.production_results.append({
                    "wo_no": wo.wo_no, "work_date": d,
                    "equip_code": equip_code, "item_code": wo.item_code,
                    "qty_produced": run_qty, "qty_defect": defects,
                    "cycle_time_s": round(ct, 2),
                    "worker": _WORKERS[int(self.rng.integers(0, len(_WORKERS)))],
                })
                # 양품이 주문 수량에 도달해야 완료 (불량분은 추가 생산으로 보충)
                if wo.good >= wo.qty:
                    wo.complete_date = d
                    queue.pop(0)
                    judgment = self._inspect(wo, d)
                    self._schedule_shipment(wo, d, judgment)

    def _inspect(self, wo: WorkOrder, d: date) -> str:
        """완료 LOT을 샘플 검사하고 판정을 반환한다."""
        sample = min(wo.produced, 50 + wo.produced // 20)
        lot_rate = 1.0 - (wo.good / wo.produced) if wo.produced else 0.0
        found = int(self.rng.binomial(sample, max(lot_rate, 0.001)))
        judgment = "불합격" if found / sample > 0.03 else "합격"
        self.res.inspections.append({
            "insp_date": d, "wo_no": wo.wo_no, "item_code": wo.item_code,
            "lot_qty": wo.produced, "sample_qty": sample,
            "defect_qty": found,
            "judgment": judgment,
            "inspector": _INSPECTORS[int(self.rng.integers(0, len(_INSPECTORS)))],
        })
        return judgment

    def _schedule_shipment(self, wo: WorkOrder, d: date,
                           judgment: str) -> None:
        """출하를 예약한다. 불합격 LOT은 재작업으로 2영업일 지연된다."""
        delay_workdays = 3 if judgment == "불합격" else 1
        ship_date = d
        remaining = delay_workdays
        while remaining > 0:
            ship_date += timedelta(days=1)
            if self.cfg.is_workday(ship_date):
                remaining -= 1
        self.ship_schedule.setdefault(ship_date, []).append({
            "order_no": wo.order_no, "line_no": wo.line_no,
            "item_code": wo.item_code, "qty": wo.qty,
        })

    def _run_shipments(self, d: date) -> None:
        for item in self.ship_schedule.pop(d, []):
            self._seq["sh"] += 1
            self.res.shipments.append({
                "ship_no": f"SH{d:%y%m%d}-{self._seq['sh']:04d}",
                "order_no": item["order_no"], "line_no": item["line_no"],
                "item_code": item["item_code"],
                "qty_shipped": item["qty"], "ship_date": d,
            })

    def _snapshot_inventory(self, d: date) -> None:
        for m_code in self.md.materials:
            self.res.inventory_daily.append({
                "snap_date": d, "material_code": m_code,
                "qty_on_hand": int(self.stock[m_code]),
                "qty_on_order": int(self.on_order[m_code]),
            })
