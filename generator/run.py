"""합성 데이터 생성 실행 진입점.

사용법:
    python -m generator.run --out ./output

산출물:
    output/erp/erp_dump.sql          ERP 소스 DB 덤프 (MariaDB)
    output/mes/*.csv                 MES 작업지시/생산실적
    output/inspection/*.xlsx         품질검사 대장 (월별)
    output/ground_truth.json         심어둔 이상/오염 명세 (검증용)
"""

import argparse
from pathlib import Path

import numpy as np

from .config import CONFIG
from .corrupt import (corrupt_erp_shipments, corrupt_mes_production,
                      corrupt_mes_work_orders)
from .master import build_master
from .simulate import Simulator
from .writers import (write_erp_sql, write_ground_truth,
                      write_inspection_excel, write_mes_csv)


def main() -> None:
    """마스터 생성 -> 시뮬레이션 -> 오염 -> 시스템별 출력."""
    parser = argparse.ArgumentParser(description="제조 운영 합성 데이터 생성기")
    parser.add_argument("--out", default="./output", help="출력 디렉토리")
    args = parser.parse_args()

    out = Path(args.out)
    rng = np.random.default_rng(CONFIG.seed)

    print("[1/4] 마스터 데이터 생성")
    md = build_master(CONFIG, rng)

    print("[2/4] 12개월 운영 시뮬레이션")
    res = Simulator(CONFIG, md, rng).run()
    print(f"      수주 {len(res.sales_order_h):,}건 / "
          f"작업지시 {len(res.work_orders):,}건 / "
          f"생산실적 {len(res.production_results):,}행 / "
          f"출하 {len(res.shipments):,}건")

    print("[3/4] 시스템별 오염 주입")
    wo_rows = [{
        "wo_no": w.wo_no, "order_no": w.order_no, "item_code": w.item_code,
        "equip_code": w.equip_code, "qty": w.qty,
        "create_date": w.create_date, "release_date": w.release_date,
        "complete_date": w.complete_date,
    } for w in res.work_orders]
    mes_wo = corrupt_mes_work_orders(wo_rows, CONFIG.corruption)
    mes_prod = corrupt_mes_production(res.production_results,
                                      CONFIG.corruption, rng)
    erp_ship = corrupt_erp_shipments(res.shipments, CONFIG.corruption, rng)

    print("[4/4] 파일 출력")
    (out / "erp").mkdir(parents=True, exist_ok=True)
    write_erp_sql(out / "erp" / "erp_dump.sql", md, res, erp_ship, CONFIG)
    write_mes_csv(out / "mes", mes_wo, mes_prod)
    write_inspection_excel(out / "inspection", res.inspections, CONFIG, rng)
    write_ground_truth(out / "ground_truth.json", CONFIG, md)
    print(f"완료: {out.resolve()}")


if __name__ == "__main__":
    main()
