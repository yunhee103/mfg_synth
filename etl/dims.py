"""차원 적재: dim_date, dim_item(SCD Type 2), 기타 dim, item_code_map.

핵심 구현 판단 (설계 문서에 반영할 것):
1. 영업일(is_workday) 추론: 소스에 공휴일 달력이 없으므로, 데이터 기간 내
   "수주도 생산도 없는 평일"을 휴무일로 추론한다. 데이터 기반 도출이며
   한계(달력 소스 부재)를 품질 리포트에 기록한다.
2. 구명칭 복원: 마스터에 없는 MES 코드(CONN-39 등)는 같은 작업지시의
   mes_work_orders.ITEM_CD(하이픈 제거형)와 대조하여 정식 코드로
   복원한다. 복원 근거가 데이터에 있으므로 ground truth 참조가 아니다.
3. SCD 키 배정: item_code_map은 현재 행(is_current=1)의 item_key를
   가리키고, fact 적재 시 거래일 기준 유효 행을 dim_item에서 재조회한다.
"""

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pymysql

from .common import EtlConfig, QualityReport, connect, to_date_key

FAR_PAST = date(1900, 1, 1)
FAR_FUTURE = date(9999, 12, 31)


def _fetch_df(conn: pymysql.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, conn)


class DimLoader:
    """차원 테이블 적재기."""

    def __init__(self, cfg: EtlConfig, qr: QualityReport) -> None:
        self.cfg = cfg
        self.qr = qr
        self.src = connect(cfg, cfg.src_db)
        self.dw = connect(cfg, cfg.dw_db)
        # fact 적재가 쓸 조회 캐시
        self.item_versions: Dict[str, List[Tuple[date, date, int]]] = {}
        self.code_to_erp: Dict[Tuple[str, str], str] = {}
        self.customer_keys: Dict[str, int] = {}
        self.supplier_keys: Dict[str, int] = {}
        self.equip_keys: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def load_dim_date(self) -> None:
        """dim_date 적재. 영업일은 활동 기반으로 추론한다."""
        orders = _fetch_df(self.src, "SELECT DISTINCT order_date d FROM sales_order_h")
        active = {r for r in orders["d"]}
        mes_dir = self.cfg.data_dir / "mes"
        for f in sorted(mes_dir.glob("mes_prod_result_*.csv")):
            wd = pd.read_csv(f, usecols=["WORK_DT"])["WORK_DT"]
            active.update(pd.to_datetime(wd).dt.date)

        d0, d1 = min(active), max(active)
        rows = []
        inferred_holidays = 0
        d = d0 - timedelta(days=31)
        end = d1 + timedelta(days=31)
        while d <= end:
            weekday = d.isoweekday()
            if weekday >= 6:
                workday = 0
            elif d0 <= d <= d1 and d not in active:
                workday = 0
                inferred_holidays += 1
            else:
                workday = 1
            rows.append((to_date_key(d), d, d.year, (d.month - 1) // 3 + 1,
                         d.month, d.day, weekday, workday))
            d += timedelta(days=1)

        with self.dw.cursor() as cur:
            cur.executemany(
                "INSERT INTO dim_date (date_key, full_date, year, quarter,"
                " month, day, day_of_week, is_workday)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        self.dw.commit()
        self.qr.count("dim_date 적재 행", len(rows))
        self.qr.count("영업일 추론: 평일 휴무 추정", inferred_holidays)
        self.qr.detail("한계", "공휴일 달력 소스 부재 - 무활동 평일을 휴무로 추론 (기간 밖 평일은 영업일 가정)")

    # ------------------------------------------------------------------
    def load_dim_item_scd(self) -> None:
        """dim_item SCD Type 2 적재 (STM 6.1, 판단 3.5)."""
        items = _fetch_df(self.src, "SELECT item_code, item_name, item_type,"
                                    " item_group, unit_price FROM items")
        log = _fetch_df(self.src, "SELECT change_date, item_code, old_value,"
                                  " new_value FROM item_change_log"
                                  " WHERE field_name='item_group'"
                                  " ORDER BY change_date")
        changes = defaultdict(list)
        for _, r in log.iterrows():
            changes[r.item_code].append((r.change_date, r.old_value, r.new_value))

        rows = []  # (code, name, type, group, price, vf, vt, cur)
        for _, it in items.iterrows():
            if it.item_code not in changes:
                rows.append((it.item_code, it.item_name, it.item_type,
                             it.item_group, it.unit_price,
                             FAR_PAST, FAR_FUTURE, 1))
                continue
            # 변경 1회 전제(현 데이터), 다회 변경도 순차 전개 가능 구조
            segs = []
            start = FAR_PAST
            for chg_d, old_v, _new_v in changes[it.item_code]:
                segs.append((old_v, start, chg_d - timedelta(days=1), 0))
                start = chg_d
            segs.append((it.item_group, start, FAR_FUTURE, 1))
            for grp, vf, vt, cur in segs:
                rows.append((it.item_code, it.item_name, it.item_type,
                             grp, it.unit_price, vf, vt, cur))
            self.qr.count("SCD Type 2 이력 전개 품목")

        with self.dw.cursor() as cur:
            # 미상 품목 행: 매핑 불가 fact의 도피처 (판단 3.3의 품목 버전)
            cur.execute("INSERT INTO dim_item (item_key, item_code_erp,"
                        " item_name, item_type, item_group, unit_price,"
                        " valid_from, valid_to, is_current)"
                        " VALUES (-1,'UNKNOWN','미상','NA',NULL,NULL,"
                        " %s,%s,1)", (FAR_PAST, FAR_FUTURE))
            cur.executemany(
                "INSERT INTO dim_item (item_code_erp, item_name, item_type,"
                " item_group, unit_price, valid_from, valid_to, is_current)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        self.dw.commit()

        ver = _fetch_df(self.dw, "SELECT item_key, item_code_erp, valid_from,"
                                 " valid_to FROM dim_item WHERE item_key<>-1")
        for _, r in ver.iterrows():
            self.item_versions.setdefault(r.item_code_erp, []).append(
                (r.valid_from, r.valid_to, int(r.item_key)))
        self.qr.count("dim_item 적재 행", len(ver) + 1)

    def item_key_for(self, erp_code: str, on: Optional[date]) -> int:
        """거래일 기준 SCD 유효 행의 item_key를 반환한다. 미상은 -1."""
        versions = self.item_versions.get(erp_code)
        if not versions:
            return -1
        if on is None:
            on = FAR_FUTURE
        for vf, vt, key in versions:
            if vf <= on <= vt:
                return key
        return versions[-1][2]

    # ------------------------------------------------------------------
    def load_small_dims(self) -> None:
        """dim_customer / dim_supplier / dim_equipment 적재."""
        cust = _fetch_df(self.src, "SELECT customer_code, customer_name,"
                                   " region FROM customers")
        supp = _fetch_df(self.src, "SELECT supplier_code, supplier_name"
                                   " FROM suppliers")
        with self.dw.cursor() as cur:
            cur.executemany("INSERT INTO dim_customer (customer_code,"
                            " customer_name, region) VALUES (%s,%s,%s)",
                            list(cust.itertuples(index=False, name=None)))
            cur.executemany("INSERT INTO dim_supplier (supplier_code,"
                            " supplier_name) VALUES (%s,%s)",
                            list(supp.itertuples(index=False, name=None)))

            # 설비 마스터 소스 부재(판단 7절 6번): MES 코드 목록에서 도출
            wo = pd.read_csv(self.cfg.data_dir / "mes" / "mes_work_orders.csv",
                             usecols=["EQP_CD"])
            codes = sorted({f"EQ-{c[2:]}" for c in wo["EQP_CD"].astype(str)})
            cur.executemany("INSERT INTO dim_equipment (equip_code,"
                            " equip_name, install_year) VALUES (%s,%s,NULL)",
                            [(c, f"조립라인 {int(c[3:])}호기") for c in codes])
        self.dw.commit()
        self.qr.detail("한계", "설비 마스터 테이블 소스 부재 - MES 코드에서 도출, install_year NULL")

        for tbl, keycol, codecol, cache in [
                ("dim_customer", "customer_key", "customer_code", self.customer_keys),
                ("dim_supplier", "supplier_key", "supplier_code", self.supplier_keys),
                ("dim_equipment", "equip_key", "equip_code", self.equip_keys)]:
            df = _fetch_df(self.dw, f"SELECT {keycol}, {codecol} FROM {tbl}")
            cache.update({r[1]: int(r[0]) for r in df.itertuples(index=False)})

    # ------------------------------------------------------------------
    def load_item_code_map(self) -> None:
        """item_code_map 적재 (판단 3.4) + 구명칭 데이터 기반 복원."""
        current = _fetch_df(self.dw, "SELECT item_key, item_code_erp"
                                     " FROM dim_item WHERE is_current=1"
                                     " AND item_key<>-1")
        rows = []
        for _, r in current.iterrows():
            rows.append(("ERP", r.item_code_erp, int(r.item_key)))
            rows.append(("MES", r.item_code_erp.replace("-", ""), int(r.item_key)))
            self.code_to_erp[("ERP", r.item_code_erp)] = r.item_code_erp
            self.code_to_erp[("MES", r.item_code_erp.replace("-", ""))] = r.item_code_erp

        # 구명칭 복원: 작업지시 공출현 대조 (본 모듈 docstring 판단 2)
        wo = pd.read_csv(self.cfg.data_dir / "mes" / "mes_work_orders.csv",
                         usecols=["WORK_ORD_NO", "ITEM_CD"])
        wo_item = dict(zip(wo.WORK_ORD_NO, wo.ITEM_CD.astype(str)))
        prods = pd.concat([
            pd.read_csv(f, usecols=["WORK_ORD_NO", "ITEM_CD"])
            for f in sorted((self.cfg.data_dir / "mes").glob("mes_prod_result_*.csv"))])
        known_mes = {c for (sys, c) in self.code_to_erp if sys == "MES"}
        unknown = prods[~prods.ITEM_CD.astype(str).isin(known_mes)]

        resolved: Dict[str, str] = {}
        unresolved: Dict[str, int] = defaultdict(int)
        for _, r in unknown.iterrows():
            legacy = str(r.ITEM_CD)
            canonical_mes = wo_item.get(r.WORK_ORD_NO)
            if canonical_mes in known_mes:
                resolved.setdefault(legacy, self.code_to_erp[("MES", canonical_mes)])
            else:
                unresolved[legacy] += 1

        for legacy, erp_code in sorted(resolved.items()):
            key = self.item_key_for(erp_code, None)
            rows.append(("MES", legacy, key))
            self.code_to_erp[("MES", legacy)] = erp_code
            self.qr.detail("구명칭 복원 결과", f"{legacy} -> {erp_code} (작업지시 공출현 대조)")
        self.qr.count("구명칭 코드 복원 종수", len(resolved))
        self.qr.count("구명칭 미복원 종수", len(unresolved))
        for code, n in unresolved.items():
            self.qr.detail("구명칭 미복원", f"{code} ({n}행) -> item_key=-1 적재")

        with self.dw.cursor() as cur:
            cur.executemany("INSERT INTO item_code_map (source_system,"
                            " source_code, item_key) VALUES (%s,%s,%s)", rows)
        self.dw.commit()
        self.qr.count("item_code_map 적재 행", len(rows))

    def erp_code_of(self, system: str, source_code: str) -> Optional[str]:
        """소스 코드 -> 정식 ERP 코드. 미상은 None."""
        return self.code_to_erp.get((system, str(source_code)))

    def run(self) -> None:
        """차원 적재 전체 실행."""
        self.load_dim_date()
        self.load_dim_item_scd()
        self.load_small_dims()
        self.load_item_code_map()
