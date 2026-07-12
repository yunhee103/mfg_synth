"""ETL 공통 모듈: 설정, 품질 리포트, 변환 유틸.

설계 근거: docs/schema_design.md의 STM 9장이 본 코드의 명세다.
원칙: 소스 불변(판단 3.7). 원본은 읽기만 하고 모든 변환은 흐름 중에.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pymysql


@dataclass(frozen=True)
class EtlConfig:
    """ETL 접속/경로 설정."""

    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    src_db: str = "erp_source"
    dw_db: str = "mfg_dw"
    data_dir: Path = Path("./output")


def connect(cfg: EtlConfig, db: str) -> pymysql.Connection:
    """지정 DB에 접속한다."""
    return pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                           password=cfg.password, database=db,
                           charset="utf8mb4", autocommit=False)


class QualityReport:
    """데이터 품질 리포트 수집기.

    ETL 각 단계가 계수와 상세를 기록하고, 종료 시 마크다운으로 출력한다.
    Q5(날짜 복원 가능성)의 답이 이 리포트로 대체된다 (판단 3.6).
    """

    def __init__(self) -> None:
        self.counters: Dict[str, int] = {}
        self.details: Dict[str, List[str]] = {}

    def count(self, key: str, n: int = 1) -> None:
        """계수를 누적한다."""
        self.counters[key] = self.counters.get(key, 0) + n

    def detail(self, key: str, msg: str) -> None:
        """상세 메시지를 기록한다 (키당 최대 30건)."""
        rows = self.details.setdefault(key, [])
        if len(rows) < 30:
            rows.append(msg)

    def to_markdown(self) -> str:
        """리포트를 마크다운 문자열로 반환한다."""
        lines = ["# ETL 데이터 품질 리포트", ""]
        lines.append("## 계수 요약")
        for k in sorted(self.counters):
            lines.append(f"- {k}: {self.counters[k]:,}")
        lines.append("")
        for k in sorted(self.details):
            lines.append(f"## {k}")
            lines.extend(f"- {m}" for m in self.details[k])
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 변환 유틸 (STM 변환 규칙의 구현)
# ---------------------------------------------------------------------------

def to_date_key(d: Optional[date]) -> int:
    """date -> YYYYMMDD 정수 키. 미발생(None)은 -1 (판단 3.3)."""
    if d is None:
        return -1
    return d.year * 10000 + d.month * 100 + d.day


def normalize_equip_code(mes_code: str) -> str:
    """MES 설비코드 정규화. EQ03 -> EQ-03 (STM 6.5/6.6)."""
    code = str(mes_code).strip()
    if re.fullmatch(r"EQ\d{2}", code):
        return f"EQ-{code[2:]}"
    return code


_DATE_PATTERNS = [
    ("iso", re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")),
    ("compact", re.compile(r"^(\d{4})(\d{2})(\d{2})$")),
    ("dotted", re.compile(r"^(\d{2})\.(\d{1,2})\.(\d{1,2})$")),
    ("slash", re.compile(r"^(\d{1,2})/(\d{1,2})$")),
]


def parse_messy_date(raw, file_year: int, file_month: int,
                     qr: QualityReport) -> Optional[date]:
    """검사 Excel의 혼재 날짜 형식을 파싱한다 (STM 6.3).

    형식 4종: ISO(2025-05-02) / 붙임(20250502) / 점(25.5.7) / 월일(5/2).
    연도 누락 형식은 파일명의 연월로 보정한다. 월일 형식에서 파일 월과
    다른 월이 나오면 모호 건으로 기록하되 파일 연도를 적용한다.

    Args:
        raw: 원본 셀 값 (문자열 또는 datetime).
        file_year: 파일명에서 추출한 연도.
        file_month: 파일명에서 추출한 월.
        qr: 품질 리포트.

    Returns:
        파싱된 날짜. 실패 시 None.
    """
    if raw is None:
        qr.count("검사일 파싱 실패(결측)")
        return None
    if isinstance(raw, datetime):
        qr.count("검사일 형식: excel-datetime")
        return raw.date()
    s = str(raw).strip()
    for name, pat in _DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        qr.count(f"검사일 형식: {name}")
        g = m.groups()
        try:
            if name == "iso" or name == "compact":
                return date(int(g[0]), int(g[1]), int(g[2]))
            if name == "dotted":
                return date(2000 + int(g[0]), int(g[1]), int(g[2]))
            # slash: 연도 없음 -> 파일 연월로 보정
            mm, dd = int(g[0]), int(g[1])
            if mm != file_month:
                qr.count("검사일 모호(파일 월과 불일치)")
                qr.detail("검사일 모호 사례", f"'{s}' in {file_year}-{file_month:02d}")
            return date(file_year, mm, dd)
        except ValueError:
            break
    qr.count("검사일 파싱 실패(형식 불명)")
    qr.detail("검사일 파싱 실패 사례", repr(raw))
    return None


def normalize_inspector(raw, roster: List[str], qr: QualityReport) -> Optional[str]:
    """검사자명 정규화 (STM 6.3): 공백 제거 + 표준 명단 대조.

    끝 글자 누락 오타(강현 -> 강현우)는 접두 일치로 복원한다.
    """
    if raw is None:
        return None
    name = str(raw).replace(" ", "").strip()
    if name in roster:
        return name
    candidates = [r for r in roster if r.startswith(name) or name.startswith(r)]
    if len(candidates) == 1:
        qr.count("검사자명 오타 정규화")
        return candidates[0]
    qr.count("검사자명 미해결")
    qr.detail("검사자명 미해결 사례", repr(raw))
    return name


def parse_int(raw, qr: QualityReport, key: str) -> Optional[int]:
    """문자열 숫자(콤마 포함) 정규화 (STM 6.3)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).replace(",", "").strip()
    if s.isdigit():
        qr.count(f"{key}: 문자열 숫자 정규화")
        return int(s)
    qr.count(f"{key}: 숫자 변환 실패")
    return None
