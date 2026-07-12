"""팩트 적재: STM 6.2~6.6의 구현.

모든 item_key 배정은 거래일 기준 SCD 유효 행 조회(dims.item_key_for)를
거친다. 미발생 날짜는 -1 (판단 3.3).
"""

import re
from collections import defaultdict
from typing import Dict, Optional, Tuple

import pandas as pd

from .common import (EtlConfig, QualityReport, connect, normalize_equip_code,
                     normalize_inspector, parse_int, parse_messy_date,
                     to_date_key)
from .dims import DimLoader


def _d(v):
    """pandas 값 -> date 또는 None."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    return pd.to_datetime(v).date()


class FactLoader:
    """팩트 테이블 적재기."""

    def __init__(self, cfg: EtlConfig, qr: QualityReport,
                 dims: DimLoader) -> None:
        self.cfg = cfg
        self.qr = qr
        self.dims = dims
        self.src = connect(cfg, cfg.src_db)
        self.dw = connect(cfg, cfg.dw_db)

    # ------------------------------------------------------------------
    def load_sales_fulfillment(self) -> None:
        """fact_sales_fulfillment (STM 6.4)."""
        lines = pd.read_sql(
            "SELECT d.order_no, d.line_no, d.item_code, d.qty, d.due_date,"
            " h.customer_code, h.order_date"
            " FROM sales_order_d d JOIN sales_order_h h"
            " ON d.order_no = h.order_no", self.src)
        ships = pd.read_sql(
            "SELECT order_no, line_no, qty_shipped, ship_date"
            " FROM shipments", self.src)
        m = lines.merge(ships, on=["order_no", "line_no"], how="left")

        rows = []
        for r in m.itertuples(index=False):
            order_d, due_d, ship_d = _d(r.order_date), _d(r.due_date), _d(r.ship_date)
            item_key = self.dims.item_key_for(r.item_code, order_d)
            lead = (ship_d - order_d).days if ship_d else None
            delay = (ship_d - due_d).days if ship_d else None
            rows.append((r.order_no, int(r.line_no),
                         self.dims.customer_keys[r.customer_code], item_key,
                         to_date_key(order_d), to_date_key(due_d),
                         to_date_key(ship_d),
                         int(r.qty),
                         int(r.qty_shipped) if pd.notna(r.qty_shipped) else 0,
                         lead, delay))
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_sales_fulfillment (order_no, line_no,"
                " customer_key, item_key, order_date_key, due_date_key,"
                " ship_date_key, qty_ordered, qty_shipped, lead_time_days,"
                " delay_days) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        self.dw.commit()
        self.qr.count("fact_sales_fulfillment 적재 행", len(rows))
        self.qr.count("미출하 라인 (-1 적재)",
                      sum(1 for r in rows if r[6] == -1))

    # ------------------------------------------------------------------
    def _load_mes_frames(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """MES 소스 적재 + 완전 중복행 제거 (STM 6.6)."""
        wo = pd.read_csv(self.cfg.data_dir / "mes" / "mes_work_orders.csv")
        prods = pd.concat([
            pd.read_csv(f) for f in sorted(
                (self.cfg.data_dir / "mes").glob("mes_prod_result_*.csv"))])
        before = len(prods)
        prods = prods.drop_duplicates()
        self.qr.count("MES 생산실적 완전 중복행 제거", before - len(prods))
        return wo, prods

    def load_work_order_and_production(self) -> None:
        """fact_work_order (STM 6.5) + fact_production_performance (6.6)."""
        wo, prods = self._load_mes_frames()

        # 수주 라인 도출 맵: (order_no, erp_code) -> line_no
        lines = pd.read_sql(
            "SELECT order_no, line_no, item_code FROM sales_order_d", self.src)
        line_map = {(r.order_no, r.item_code): int(r.line_no)
                    for r in lines.itertuples(index=False)}
        dup_check = defaultdict(int)
        for r in lines.itertuples(index=False):
            dup_check[(r.order_no, r.item_code)] += 1
        ambiguous = {k for k, n in dup_check.items() if n > 1}
        if ambiguous:
            self.qr.count("수주 라인 도출 모호(주문 내 품목 중복)", len(ambiguous))

        # 작업지시별 생산 집계
        agg = prods.groupby("WORK_ORD_NO").agg(
            produced=("PROD_QTY", "sum"), defect=("DEF_QTY", "sum"))

        wo_rows = []
        for r in wo.itertuples(index=False):
            erp_code = self.dims.erp_code_of("MES", r.ITEM_CD)
            reg_d = _d(r.REG_DT)
            if erp_code is None:
                self.qr.count("작업지시 품목 매핑 실패 (-1)")
                item_key = -1
            else:
                item_key = self.dims.item_key_for(erp_code, reg_d)
            equip_key = self.dims.equip_keys[normalize_equip_code(r.EQP_CD)]
            ref_line = None
            if erp_code is not None and (r.REF_ORD_NO, erp_code) in line_map \
                    and (r.REF_ORD_NO, erp_code) not in ambiguous:
                ref_line = line_map[(r.REF_ORD_NO, erp_code)]
            a = agg.loc[r.WORK_ORD_NO] if r.WORK_ORD_NO in agg.index else None
            wo_rows.append((r.WORK_ORD_NO, item_key, equip_key,
                            r.REF_ORD_NO, ref_line,
                            to_date_key(reg_d), to_date_key(_d(r.REL_DT)),
                            to_date_key(_d(r.CMPL_DT)), int(r.ORD_QTY),
                            int(a.produced) if a is not None else 0,
                            int(a.defect) if a is not None else 0))
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_work_order (work_order_no, item_key,"
                " equip_key, ref_order_no, ref_line_no, reg_date_key,"
                " rel_date_key, cmpl_date_key, qty_planned, qty_produced,"
                " qty_defect) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                wo_rows)
        self.dw.commit()
        self.qr.count("fact_work_order 적재 행", len(wo_rows))

        # 생산 실적: (지시, 일자) 단위 합산
        wo_reg = {r.WORK_ORD_NO: _d(r.REG_DT) for r in wo.itertuples(index=False)}
        prods["work_date"] = pd.to_datetime(prods["WORK_DT"]).dt.date
        grouped = prods.groupby(["WORK_ORD_NO", "work_date"]).agg(
            item_cd=("ITEM_CD", "first"), eqp=("EQP_CD", "first"),
            produced=("PROD_QTY", "sum"), defect=("DEF_QTY", "sum"),
            cycle=("CYCLE_TM", "mean"), n=("PROD_QTY", "size"))
        merged_rows = int((grouped["n"] > 1).sum())
        if merged_rows:
            self.qr.count("생산실적 (지시,일자) 병합 발생", merged_rows)

        missing_ct = 0
        pf_rows = []
        for (wo_no, wdate), r in grouped.iterrows():
            erp_code = self.dims.erp_code_of("MES", r.item_cd)
            item_key = (self.dims.item_key_for(erp_code, wdate)
                        if erp_code else -1)
            if erp_code is None:
                self.qr.count("생산실적 품목 매핑 실패 (-1)")
            cycle = None if pd.isna(r.cycle) else round(float(r.cycle), 2)
            if cycle is None:
                missing_ct += 1
            pf_rows.append((wo_no, to_date_key(wdate), item_key,
                            self.dims.equip_keys[normalize_equip_code(r.eqp)],
                            int(r.produced), int(r.defect), cycle))
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_production_performance (work_order_no,"
                " date_key, item_key, equip_key, qty_produced, qty_defect,"
                " cycle_time_s) VALUES (%s,%s,%s,%s,%s,%s,%s)", pf_rows)
        self.dw.commit()
        self.qr.count("fact_production_performance 적재 행", len(pf_rows))
        self.qr.count("사이클타임 결측 행", missing_ct)

    # ------------------------------------------------------------------
    def load_quality_inspection(self) -> None:
        """fact_quality_inspection (STM 6.3)."""
        files = sorted((self.cfg.data_dir / "inspection").glob("quality_insp_*.xlsx"))
        roster: Dict[str, int] = defaultdict(int)
        frames = []
        for f in files:
            ym = re.search(r"(\d{4})(\d{2})", f.name)
            df = pd.read_excel(f, header=2)
            df["fyear"], df["fmonth"] = int(ym.group(1)), int(ym.group(2))
            frames.append(df)
            for v in df["검사자"].dropna():
                roster[str(v).replace(" ", "").strip()] += 1
        all_df = pd.concat(frames)
        # 표준 명단: 빈도 상위 이름 (오타는 저빈도)
        names = sorted(roster.items(), key=lambda x: -x[1])
        canonical = [n for n, c in names if c >= max(3, names[0][1] * 0.05)]
        self.qr.detail("검사자 표준 명단(빈도 도출)", ", ".join(canonical))

        rows = []
        for r in all_df.itertuples(index=False):
            insp_d = parse_messy_date(r.검사일, r.fyear, r.fmonth, self.qr)
            item_key = -1
            erp_code = str(r.품목코드)
            if erp_code in self.dims.item_versions:
                item_key = self.dims.item_key_for(erp_code, insp_d)
            else:
                self.qr.count("검사 품목 매핑 실패 (-1)")
            rows.append((r.작업지시번호, item_key, to_date_key(insp_d),
                         parse_int(r.LOT수량, self.qr, "LOT수량"),
                         int(r.샘플수량), int(r.불량수), str(r.판정),
                         normalize_inspector(r.검사자, canonical, self.qr)))
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_quality_inspection (work_order_no,"
                " item_key, date_key, qty_lot, qty_sample, qty_defect,"
                " decision, inspector_nm) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                rows)
        self.dw.commit()
        self.qr.count("fact_quality_inspection 적재 행", len(rows))

    # ------------------------------------------------------------------
    def load_daily_inventory(self) -> None:
        """fact_daily_inventory (STM 6.2)."""
        inv = pd.read_sql("SELECT snap_date, material_code, qty_on_hand,"
                          " qty_on_order FROM inventory_daily", self.src)
        rows = [(to_date_key(_d(r.snap_date)),
                 self.dims.item_key_for(r.material_code, _d(r.snap_date)),
                 int(r.qty_on_hand), int(r.qty_on_order))
                for r in inv.itertuples(index=False)]
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_daily_inventory (date_key, item_key,"
                " qty_on_hand, qty_on_order) VALUES (%s,%s,%s,%s)", rows)
        self.dw.commit()
        self.qr.count("fact_daily_inventory 적재 행", len(rows))

    # ------------------------------------------------------------------
    def load_procurement(self) -> None:
        """fact_procurement_fulfillment (STM 병합: 발주 + 입고 집계)."""
        po = pd.read_sql("SELECT po_no, supplier_code, material_code, qty,"
                         " order_date, promised_date FROM purchase_orders",
                         self.src)
        rc = pd.read_sql("SELECT po_no, MAX(receipt_date) AS receipt_date,"
                         " SUM(qty) AS qty_received FROM material_receipts"
                         " GROUP BY po_no", self.src)
        m = po.merge(rc, on="po_no", how="left")
        rows = []
        for r in m.itertuples(index=False):
            od = _d(r.order_date)
            rows.append((r.po_no, self.dims.supplier_keys[r.supplier_code],
                         self.dims.item_key_for(r.material_code, od),
                         to_date_key(od), to_date_key(_d(r.promised_date)),
                         to_date_key(_d(r.receipt_date)), int(r.qty),
                         int(r.qty_received) if pd.notna(r.qty_received) else 0))
        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO fact_procurement_fulfillment (po_no,"
                " supplier_key, item_key, order_date_key, promised_date_key,"
                " receipt_date_key, qty_ordered, qty_received)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        self.dw.commit()
        self.qr.count("fact_procurement_fulfillment 적재 행", len(rows))
        self.qr.count("미입고 발주 (-1 적재)",
                      sum(1 for r in rows if r[5] == -1))

    def run(self) -> None:
        """팩트 적재 전체 실행."""
        self.load_sales_fulfillment()
        self.load_work_order_and_production()
        self.load_quality_inspection()
        self.load_daily_inventory()
        self.load_procurement()
