"""데이터 오염 주입.

시뮬레이션이 생성한 '진실' 레코드를 시스템별 출력 직전에 오염시킨다.
오염은 반드시 출력 단계에서만 발생시키고 시뮬레이션 상태에는 손대지
않는다. 이 분리가 ground truth 보존의 핵심이다.
"""

import copy
from typing import Dict, List

import numpy as np

from .config import Corruption, LEGACY_CODE_MAP


def strip_hyphen(code: str) -> str:
    """MES 스타일 품목코드로 변환한다. 예: F-0012 -> F0012."""
    return code.replace("-", "")


def corrupt_mes_production(rows: List[dict], cor: Corruption,
                           rng: np.random.Generator) -> List[dict]:
    """MES 생산실적에 오염을 주입한다.

    적용 순서: 코드 스타일 변경 -> 구명칭 치환 -> 결측 -> 중복.

    Args:
        rows: 생산실적 레코드 (진실 데이터).
        cor: 오염 설정.
        rng: 난수 생성기.

    Returns:
        오염된 레코드 목록 (원본은 변경하지 않음).
    """
    out = [copy.copy(r) for r in rows]
    legacy_targets = set(LEGACY_CODE_MAP.keys())

    for r in out:
        original = r["item_code"]
        if cor.mes_strip_hyphen:
            r["item_code"] = strip_hyphen(original)
            r["equip_code"] = strip_hyphen(r["equip_code"])
        if (original in legacy_targets
                and rng.random() < cor.mes_legacy_usage_rate):
            r["item_code"] = LEGACY_CODE_MAP[original]
        if rng.random() < cor.mes_missing_rate:
            r["cycle_time_s"] = None
        if rng.random() < cor.mes_missing_rate:
            r["worker"] = None

    n_dup = int(len(out) * cor.mes_duplicate_rate)
    if n_dup > 0:
        dup_idx = rng.choice(len(out), size=n_dup, replace=False)
        out.extend(copy.copy(out[i]) for i in dup_idx)
    return out


def corrupt_mes_work_orders(rows: List[dict], cor: Corruption) -> List[dict]:
    """MES 작업지시 코드 스타일을 변환한다."""
    out = [copy.copy(r) for r in rows]
    if cor.mes_strip_hyphen:
        for r in out:
            r["item_code"] = strip_hyphen(r["item_code"])
            r["equip_code"] = strip_hyphen(r["equip_code"])
    return out


def corrupt_erp_shipments(rows: List[dict], cor: Corruption,
                          rng: np.random.Generator) -> List[dict]:
    """ERP 출하 수량에 수기 입력 오타를 주입한다.

    마지막 자리 오타 또는 자릿수 반복 입력을 흉내 낸다.
    결과적으로 MES 생산실적 합계와 대사가 어긋난다.
    """
    out = [copy.copy(r) for r in rows]
    for r in out:
        if rng.random() < cor.erp_ship_qty_typo_rate:
            q = r["qty_shipped"]
            if rng.random() < 0.5:
                r["qty_shipped"] = q + int(rng.integers(1, 10))
            else:
                r["qty_shipped"] = int(str(q) + str(q)[-1])
    return out


def messy_date(d, style: int) -> str:
    """검사 Excel용 날짜 문자열. 담당자마다 다른 표기 습관을 흉내 낸다."""
    styles = {
        0: f"{d:%Y-%m-%d}",
        1: f"{d.year % 100}.{d.month}.{d.day}",
        2: f"{d.month}/{d.day}",
        3: f"{d:%Y%m%d}",
    }
    return styles[style]


def typo_name(name: str, rng: np.random.Generator) -> str:
    """검사자명 오타. 공백 삽입 또는 끝 글자 누락."""
    if rng.random() < 0.5:
        pos = int(rng.integers(1, len(name)))
        return name[:pos] + " " + name[pos:]
    return name[:-1]


_INSPECTOR_STYLE: Dict[str, int] = {
    "강현우": 0, "노지은": 1, "문성호": 2, "배아름": 3,
}


def inspector_date_style(inspector: str) -> int:
    """검사자별 고정 날짜 표기 스타일을 반환한다."""
    return _INSPECTOR_STYLE.get(inspector, 0)
