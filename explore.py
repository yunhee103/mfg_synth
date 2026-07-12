"""탐색 도우미.

기계적인 부분(행 수 집계, 샘플 출력, 무결성 체크)을 자동으로 수행한다.
출력을 읽고 해석하는 것, 위화감을 기록하는 것, 질문을 세우는 것은
사용자의 몫이며 이 스크립트는 그 판단을 대신하지 않는다.

사용법:
    pip install pymysql pandas openpyxl
    python explore.py --port 3307 --password devpass --data ./output
"""

import argparse
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd
import pymysql

DIVIDER = "=" * 68


def connect(host: str, port: int, password: str) -> pymysql.Connection:
    """ERP 소스 DB에 접속한다."""
    return pymysql.connect(host=host, port=port, user="root",
                           password=password, database="erp_source",
                           charset="utf8mb4")


def q(conn: pymysql.Connection, sql: str) -> pd.DataFrame:
    """SQL을 실행하고 DataFrame으로 반환한다."""
    return pd.read_sql(sql, conn)


def part_a_inventory(conn: pymysql.Connection) -> None:
    """패스 1: ERP 테이블 인벤토리 (행 수, 기간, 샘플)."""
    print(DIVIDER)
    print("[A] ERP 테이블 인벤토리 - 표를 옮겨 적고 각 테이블이")
    print("    '무엇에 대한 기록'인지 한 줄씩 네 말로 써라.")
    print(DIVIDER)
    tables = q(conn, "SHOW TABLES").iloc[:, 0].tolist()
    date_cols = {
        "sales_order_h": "order_date", "sales_order_d": "due_date",
        "purchase_orders": "order_date", "material_receipts": "receipt_date",
        "shipments": "ship_date", "inventory_daily": "snap_date",
        "item_change_log": "change_date",
    }
    for t in tables:
        n = q(conn, f"SELECT COUNT(*) c FROM {t}").iloc[0, 0]
        period = ""
        if t in date_cols:
            col = date_cols[t]
            mm = q(conn, f"SELECT MIN({col}) a, MAX({col}) b FROM {t}")
            period = f" | 기간 {mm.iloc[0, 0]} ~ {mm.iloc[0, 1]}"
        print(f"\n--- {t}: {n:,}행{period}")
        print(q(conn, f"SELECT * FROM {t} LIMIT 3").to_string(index=False))


def part_b_relations(conn: pymysql.Connection) -> None:
    """패스 2: 참조 무결성 체크. 0이 아닌 값이 나오는 곳이 단서다."""
    print("\n" + DIVIDER)
    print("[B] 참조 관계 체크 - 각 행의 숫자가 0인지 아닌지 보고,")
    print("    0이 아닌 곳은 위화감 목록에 적어라.")
    print(DIVIDER)
    checks = [
        ("수주상세 -> 수주헤더 고아",
         "SELECT COUNT(*) c FROM sales_order_d d LEFT JOIN sales_order_h h"
         " ON d.order_no=h.order_no WHERE h.order_no IS NULL"),
        ("수주상세 -> 품목마스터 고아",
         "SELECT COUNT(*) c FROM sales_order_d d LEFT JOIN items i"
         " ON d.item_code=i.item_code WHERE i.item_code IS NULL"),
        ("출하 -> 수주상세 고아",
         "SELECT COUNT(*) c FROM shipments s LEFT JOIN sales_order_d d"
         " ON s.order_no=d.order_no AND s.line_no=d.line_no"
         " WHERE d.order_no IS NULL"),
        ("입고 -> 발주 고아",
         "SELECT COUNT(*) c FROM material_receipts r LEFT JOIN"
         " purchase_orders p ON r.po_no=p.po_no WHERE p.po_no IS NULL"),
        ("BOM 자식 -> 품목마스터 고아",
         "SELECT COUNT(*) c FROM bom b LEFT JOIN items i"
         " ON b.child_item=i.item_code WHERE i.item_code IS NULL"),
        ("수주상세 중 미출하 건수",
         "SELECT COUNT(*) c FROM sales_order_d d LEFT JOIN shipments s"
         " ON d.order_no=s.order_no AND d.line_no=s.line_no"
         " WHERE s.ship_no IS NULL"),
    ]
    for name, sql in checks:
        print(f"  {name}: {q(conn, sql).iloc[0, 0]}")


def part_c_boundary(conn: pymysql.Connection, data_dir: Path) -> None:
    """패스 3: 시스템 경계 체크 (ERP vs MES vs Excel)."""
    print("\n" + DIVIDER)
    print("[C] 시스템 경계 체크 - 아래 출력들을 나란히 보고")
    print("    무엇이 어떻게 다른지 네 말로 기록해라.")
    print(DIVIDER)

    erp_codes = set(q(conn, "SELECT item_code FROM items")["item_code"])
    mes_files = sorted((data_dir / "mes").glob("mes_prod_result_*.csv"))
    mes = pd.concat([pd.read_csv(f) for f in mes_files])
    mes_codes = set(mes["ITEM_CD"].dropna().unique())

    print(f"\nC-1. 품목코드 형식 비교")
    print(f"  ERP 코드 샘플 : {sorted(erp_codes)[120:125]}")
    print(f"  MES 코드 샘플 : {sorted(mes_codes)[:5]}")
    exact = len(mes_codes & erp_codes)
    print(f"  ERP와 '정확히' 일치하는 MES 코드: {exact} / {len(mes_codes)}종")
    print("  -> 이 숫자가 의미하는 바를 워크시트에 적어라.")

    print(f"\nC-2. 작업지시 수량 대사 (무작위 15건)")
    print("  MES 양품수량(생산-불량 합계) vs ERP 출하수량")
    mes_good = (mes.groupby("WORK_ORD_NO")
                .agg(prod_qty=("PROD_QTY", "sum"), dfct_qty=("DEF_QTY", "sum")))
    mes_good["mes_good"] = mes_good["prod_qty"] - mes_good["dfct_qty"]
    wo = pd.read_csv(data_dir / "mes" / "mes_work_orders.csv")
    ship = q(conn, "SELECT order_no, qty_shipped FROM shipments")
    # MES 작업지시에는 수주 라인번호가 없어 다중 라인 주문은 모호하게
    # 조인된다. 대사는 단일 라인 주문으로 한정한다. (이 제약 자체가
    # 시스템 간 키 설계의 허점이다 - 위화감 목록에 적을 것.)
    single_ship = ship.groupby("order_no").filter(lambda g: len(g) == 1)
    single_wo = wo.groupby("REF_ORD_NO").filter(lambda g: len(g) == 1)
    merged = (single_wo.merge(mes_good, on="WORK_ORD_NO")
              .merge(single_ship, left_on="REF_ORD_NO", right_on="order_no"))
    merged = merged[merged.CMPL_DT.notna()]
    sample = merged.sample(min(15, len(merged)), random_state=1)
    view = sample[["WORK_ORD_NO", "mes_good", "qty_shipped"]].copy()
    view["차이"] = view.qty_shipped - view.mes_good
    print(view.to_string(index=False))
    n_diff = int((merged.qty_shipped != merged.mes_good).sum())
    print(f"  전체 대사: 불일치 {n_diff}건 / {len(merged)}건")
    print("  -> 불일치가 '몇 건인지'보다 '어느 방향으로 얼마나'인지 보라.")

    print(f"\nC-3. 검사 Excel 날짜 컬럼 (2025년 5월 파일, 처음 12행)")
    xlsx = data_dir / "inspection" / "quality_insp_202505.xlsx"
    insp = pd.read_excel(xlsx, header=2)
    print(insp.iloc[:12, [0, 6, 7]].to_string(index=False))
    print("  -> 날짜 컬럼과 검사자 컬럼에서 눈에 걸리는 것을 전부 적어라.")


def part_d_worksheet(out_path: Path) -> None:
    """빈칸 워크시트를 생성한다."""
    content = """# 탐색 워크시트

작성 규칙: 스크립트 출력을 보고 빈칸을 채운다. 판별이 안 되는 항목은
'판별 불가'라고 쓰고 넘어간다 (나중에 같이 판별한다).

## 1. 테이블 인벤토리 (A 출력 기준)

| 테이블 | 행 수 | 이 테이블은 무엇에 대한 기록인가 (한 줄) |
|---|---|---|
| customers | | |
| suppliers | | |
| items | | |
| bom | | |
| sales_order_h | | |
| sales_order_d | | |
| purchase_orders | | |
| material_receipts | | |
| shipments | | |
| inventory_daily | | |
| item_change_log | | |

## 2. 관계도 (B 출력 기준)

종이에 그린 관계도를 사진으로 남기거나 텍스트로 옮겨라.
B에서 0이 아니었던 체크: ____________________
그것이 이상한가, 자연스러운가: ____________________

## 3. 위화감 목록 (최소 5개)

형식: 현상 / 어디서 봤나 / 왜 이상한가

1.
2.
3.
4.
5.

## 4. 분석 질문 10개

형식 (3줄 엄수):
질문:
의도: (답이 나오면 누가 무엇을 바꾸는가)
필요 데이터: (테이블/컬럼 추정)

Q1.
Q2.
Q3.
Q4.
Q5.
Q6.
Q7.
Q8.
Q9.
Q10.
"""
    out_path.write_text(content, encoding="utf-8")
    print(f"\n워크시트 생성: {out_path}")


def main() -> None:
    """탐색 파트 A-C 실행 및 워크시트 생성."""
    parser = argparse.ArgumentParser(description="탐색 도우미")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3307)
    parser.add_argument("--password", default="devpass")
    parser.add_argument("--data", default="./output",
                        help="생성기 출력 디렉토리 (mes/, inspection/ 포함)")
    args = parser.parse_args()

    conn = connect(args.host, args.port, args.password)
    try:
        part_a_inventory(conn)
        part_b_relations(conn)
        part_c_boundary(conn, Path(args.data))
    finally:
        conn.close()
    part_d_worksheet(Path("탐색_워크시트.md"))



if __name__ == "__main__":
    main()
