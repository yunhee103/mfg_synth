-- Q7. 납기 준수율은? 지연은 어느 단면(거래처/품목군/시기)에 몰리고 언제부터?
-- 보는 법: 7a 월별 추이에서 꺾이는 달을 찾고 -> 7b 품목군 분해로 어느
--   군이 끌어내렸는지 -> 7c에서 그 품목군의 설비를 확인(Q8로 연결).
-- 미출하(-1)는 지연으로 계산한다 (보수적 정의 - 왜 그런지 설명할 수 있어야 함).

-- 7a. 월별 준수율 (납기월 기준, 2025년만)
SELECT dd.year, dd.month,
       COUNT(*) AS line_cnt,
       SUM(CASE WHEN f.ship_date_key <> -1 AND f.delay_days <= 0
                THEN 1 ELSE 0 END) / COUNT(*) * 100 AS ontime_pct
FROM fact_sales_fulfillment f
JOIN dim_date dd ON f.due_date_key = dd.date_key
WHERE dd.year = 2025
GROUP BY dd.year, dd.month
ORDER BY dd.year, dd.month;

-- 7b. 품목군별 x 분기별 준수율 - SCD 활용: "당시 기준" 품목군
--   (개편 품목은 7월 전후로 소속이 달라진다. is_current=1로만 조인하면
--    "현재 기준"이 되는데, 두 결과가 어떻게 다른지 비교해보라 - SCD의 존재 이유)
SELECT di.item_group, dd.quarter,
       COUNT(*) AS line_cnt,
       SUM(CASE WHEN f.ship_date_key <> -1 AND f.delay_days <= 0
                THEN 1 ELSE 0 END) / COUNT(*) * 100 AS ontime_pct
FROM fact_sales_fulfillment f
JOIN dim_item di ON f.item_key = di.item_key
JOIN dim_date dd ON f.due_date_key = dd.date_key
WHERE dd.year = 2025
GROUP BY di.item_group, dd.quarter
ORDER BY di.item_group, dd.quarter;

-- 7c. 지연 라인의 품목이 배정된 설비 분포 (Q8로 넘어가는 다리)
SELECT de.equip_code, COUNT(*) AS late_line_cnt
FROM fact_sales_fulfillment f
JOIN fact_work_order w
  ON f.order_no = w.ref_order_no AND f.line_no = w.ref_line_no
JOIN dim_equipment de ON w.equip_key = de.equip_key
WHERE (f.ship_date_key = -1 OR f.delay_days > 0)
GROUP BY de.equip_code
ORDER BY late_line_cnt DESC;
