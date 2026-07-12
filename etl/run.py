"""무결성 검증 + ETL 실행 진입점.
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

검증 항목은 설계 문서 7절 7번(SCD)과 A-Z 수업의 무결성 쿼리 패턴을
구현한다. 실패 항목은 품질 리포트에 기록하고 종료 코드로 알린다.

사용법:
    python -m etl.run --port 3307 --password devpass --data ./output
"""

import argparse
import sys
from pathlib import Path
import pandas as pd

from .common import EtlConfig, QualityReport, connect
from .dims import DimLoader
from .facts import FactLoader

CHECKS = [
    ("SCD: is_current=1 중복 품목",
     "SELECT COUNT(*) FROM (SELECT item_code_erp FROM dim_item"
     " WHERE is_current=1 GROUP BY item_code_erp HAVING COUNT(*)>1) t"),
    ("SCD: 유효기간 겹침",
     "SELECT COUNT(*) FROM dim_item a JOIN dim_item b"
     " ON a.item_code_erp=b.item_code_erp AND a.item_key<b.item_key"
     " AND a.valid_from<=b.valid_to AND b.valid_from<=a.valid_to"),
    ("grain: 수주 라인 중복",
     "SELECT COUNT(*) FROM (SELECT order_no,line_no FROM"
     " fact_sales_fulfillment GROUP BY order_no,line_no"
     " HAVING COUNT(*)>1) t"),
    ("grain: 생산실적 (지시,일자) 중복",
     "SELECT COUNT(*) FROM (SELECT work_order_no,date_key FROM"
     " fact_production_performance GROUP BY work_order_no,date_key"
     " HAVING COUNT(*)>1) t"),
    ("고아: 품목 미상(-1) 외 dim 미참조 fact",
     "SELECT COUNT(*) FROM fact_production_performance f"
     " LEFT JOIN dim_item d ON f.item_key=d.item_key"
     " WHERE d.item_key IS NULL"),
]

RECON = [
    ("대사: ERP 출하수량 합 = fact 출하수량 합",
     "SELECT (SELECT COALESCE(SUM(qty_shipped),0) FROM erp_source.shipments)"
     " - (SELECT COALESCE(SUM(qty_shipped),0) FROM mfg_dw.fact_sales_fulfillment"
     "    WHERE ship_date_key<>-1)"),
    ("대사: 소스 재고 행수 = fact 재고 행수",
     "SELECT (SELECT COUNT(*) FROM erp_source.inventory_daily)"
     " - (SELECT COUNT(*) FROM mfg_dw.fact_daily_inventory)"),
]


def validate(cfg: EtlConfig, qr: QualityReport) -> bool:
    """무결성/대사 검증. 전부 0이어야 통과."""
    dw = connect(cfg, cfg.dw_db)
    ok = True
    with dw.cursor() as cur:
        for name, sql in CHECKS + RECON:
            cur.execute(sql)
            v = int(cur.fetchone()[0])
            qr.count(f"[검증] {name}", v)
            if v != 0:
                ok = False
                qr.detail("검증 실패", f"{name}: {v}")
    return ok


def main() -> None:
    """ETL 전체 실행: dim -> fact -> 검증 -> 품질 리포트."""
    parser = argparse.ArgumentParser(description="mfg_synth ETL")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3307)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="devpass")
    parser.add_argument("--data", default="./output")
    args = parser.parse_args()

    cfg = EtlConfig(host=args.host, port=args.port, user=args.user,
                    password=args.password, data_dir=Path(args.data))
    qr = QualityReport()

    print("[1/3] 차원 적재")
    dims = DimLoader(cfg, qr)
    dims.run()

    print("[2/3] 팩트 적재")
    FactLoader(cfg, qr, dims).run()

    print("[3/3] 무결성 검증")
    ok = validate(cfg, qr)

    report_path = Path("etl_quality_report.md")
    report_path.write_text(qr.to_markdown(), encoding="utf-8")
    print(f"품질 리포트: {report_path.resolve()}")
    print("검증", "전체 통과" if ok else "실패 항목 존재 - 리포트 확인")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
