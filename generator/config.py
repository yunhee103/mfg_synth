"""시뮬레이션 설정.

가상 기업: 전자부품 조립 중소기업 (완제품 60종, 자재 120종, 조립라인 8기).
심어둔 이상(ground truth)과 데이터 오염(corruption)을 전부 이 파일에서
파라미터로 관리한다. 분석 코드는 이 파일을 참조하지 않는다.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class EquipDegradation:
    """특정 설비의 사이클타임 점진 저하.

    Attributes:
        equip_code: 대상 설비 코드.
        start: 저하 시작일.
        ramp_days: 최대 저하율까지 도달하는 기간(일).
        max_drift: 최대 사이클타임 증가율 (0.08 = +8%).
        defect_uplift: 저하 구간에서 불량률 상대 증가율.
    """

    equip_code: str = "EQ-03"
    start: date = date(2025, 6, 9)
    ramp_days: int = 56
    max_drift: float = 0.10
    defect_uplift: float = 0.3


@dataclass(frozen=True)
class SupplierDelay:
    """특정 공급사의 자재 리드타임 급증.

    발주점(reorder point)은 구 리드타임 기준으로 고정되어 있으므로
    리드타임이 늘면 반복적인 결품이 발생한다.
    """

    supplier_code: str = "S-07"
    start: date = date(2025, 8, 18)
    old_lead_time: int = 5
    new_lead_time: int = 13


@dataclass(frozen=True)
class MasterReorg:
    """품목군 체계 개편 (SCD Type 2 소재).

    change_date부로 일부 완제품의 item_group이 신설 그룹으로 변경된다.
    ERP items 테이블은 최종 상태를 담고, 변경 이력은 item_change_log
    감사 테이블에 남는다. ETL은 둘을 조합해 Type 2 차원을 구축해야 한다.
    """

    change_date: date = date(2025, 7, 1)
    new_group: str = "IoT모듈"
    n_items: int = 8


@dataclass(frozen=True)
class Corruption:
    """의도적 데이터 오염 파라미터.

    실무에서 흔히 발생하는 원인을 주석으로 병기한다.
    """

    # MES는 품목코드에서 하이픈을 제거해 저장 (시스템 도입 업체가 다름)
    mes_strip_hyphen: bool = True
    # 일부 MES 레코드는 마스터 등록 전 구명칭을 그대로 사용 (현장 수기 관행)
    # legacy_usage_rate: 구명칭 대상 품목의 레코드 중 구명칭으로 기록되는 비율
    mes_legacy_usage_rate: float = 0.3
    mes_legacy_item_count: int = 10
    # 야간조 기록 누락 (수기 전산 이관 과정의 결측)
    mes_missing_rate: float = 0.02
    # 인터페이스 재전송으로 인한 중복 행
    mes_duplicate_rate: float = 0.005
    # ERP 출하 수량 오타 (전표 수기 입력 오류) -> MES 생산실적과 불일치
    erp_ship_qty_typo_rate: float = 0.01
    # 검사 Excel: 날짜 포맷 혼재, 검사자명 오타, 숫자 문자열 저장
    excel_date_chaos: bool = True
    excel_inspector_typo_rate: float = 0.05
    excel_qty_as_text_rate: float = 0.1


@dataclass(frozen=True)
class SimConfig:
    """시뮬레이션 전체 설정."""

    seed: int = 20260711
    # 2024-10~12은 워밍업 구간: 초기 상태 과도기를 흡수한다.
    # 데이터는 전 구간 출력하되 분석 기준선은 2025년을 사용할 것.
    period_start: date = date(2024, 10, 1)
    period_end: date = date(2025, 12, 31)

    n_finished_goods: int = 60
    n_materials: int = 120
    n_customers: int = 30
    n_suppliers: int = 15
    n_equipment: int = 8

    # 수요: 영업일당 평균 수주 건수, 라인당 1~2 품목, 수량 범위
    # 전체 가동률이 약 70%가 되도록 캘리브레이션된 값
    orders_per_workday: float = 6.5
    # 월별 수요 계절성 (1월~12월). 연말 성수기, 여름 비수기의 완만한 곡선.
    # 분석 시 "수요 변동을 통제해도 이상 효과가 남는가"를 검증하게 만든다.
    demand_seasonality: Tuple[float, ...] = (
        0.92, 0.95, 1.00, 1.02, 1.00, 1.05,
        0.95, 0.90, 1.05, 1.08, 1.12, 1.10)
    order_qty_range: Tuple[int, int] = (100, 700)
    due_days_range: Tuple[int, int] = (7, 21)

    # 설비: 1일 유효 가동시간(분). 나머지는 준비교체/휴식으로 가정
    operating_minutes: int = 400
    # 완제품 기준 사이클타임 범위(초/개)
    cycle_time_range: Tuple[float, float] = (25.0, 45.0)

    # 자재: 기본 리드타임 범위(일), 발주 로트 = 일평균소요 x 이 값
    material_lead_time_range: Tuple[int, int] = (3, 7)
    order_lot_days: int = 14
    safety_stock_days: int = 3

    # 병목 라인(EQ-03) 목표 가동률. 저하 발생 전 기준
    bottleneck_target_util: float = 0.88
    normal_target_util: float = 0.70

    degradation: EquipDegradation = field(default_factory=EquipDegradation)
    reorg: MasterReorg = field(default_factory=MasterReorg)
    supplier_delay: SupplierDelay = field(default_factory=SupplierDelay)
    corruption: Corruption = field(default_factory=Corruption)

    # 2025년 한국 공휴일 (주말 외 휴무일)
    holidays: List[date] = field(default_factory=lambda: [
        date(2024, 10, 3), date(2024, 10, 9), date(2024, 12, 25),
        date(2025, 1, 1),
        date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
        date(2025, 3, 3),
        date(2025, 5, 1), date(2025, 5, 5), date(2025, 5, 6),
        date(2025, 6, 6),
        date(2025, 8, 15),
        date(2025, 10, 3), date(2025, 10, 6), date(2025, 10, 7),
        date(2025, 10, 8), date(2025, 10, 9),
        date(2025, 12, 25),
    ])

    def is_workday(self, d: date) -> bool:
        """주말과 공휴일을 제외한 영업일 여부를 반환한다."""
        return d.weekday() < 5 and d not in self.holidays


CONFIG = SimConfig()

# 구명칭 매핑: MES 현장에서 아직 쓰이는 옛 품목명 (마스터 미등록 코드)
LEGACY_CODE_MAP: Dict[str, str] = {
    f"F-{i:04d}": name for i, name in [
        (3, "PCB-A03"), (7, "PWR-B07"), (12, "CTRL-12"), (18, "SNSR-18"),
        (24, "PCB-B24"), (31, "RLY-31"), (39, "CONN-39"), (45, "PWR-C45"),
        (52, "CTRL-52"), (58, "SNSR-58"),
    ]
}
