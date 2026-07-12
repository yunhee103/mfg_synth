-- Q2. 양품수량과 출하수량이 어긋나는 건은 몰려 있는가, 흩어져 있는가?
-- 보는 법: 불일치 건의 (1) 월별 분포 (2) 방향(+/-) (3) 차이 크기.
--   무작위 흩어짐 + 작은 차이 = 입력 오류 가설, 몰림 = 시스템/공정 가설.

-- 2a. 라인 단위 대사: 불일치 건 목록
SELECT w.ref_order_no, w.ref_line_no,
       (w.qty_produced - w.qty_defect) AS good_qty,
       s.qty_shipped,
       s.qty_shipped - (w.qty_produced - w.qty_defect) AS diff,
       dd.year, dd.month
FROM fact_work_order w
JOIN fact_sales_fulfillment s
  ON w.ref_order_no = s.order_no AND w.ref_line_no = s.line_no
JOIN dim_date dd ON s.ship_date_key = dd.date_key
WHERE s.ship_date_key <> -1
  AND s.qty_shipped <> (w.qty_produced - w.qty_defect)
ORDER BY dd.year, dd.month;

-- 2b. 월별 불일치 건수/전체 건수 (몰림 판정)
SELECT dd.year, dd.month,
       COUNT(*) AS shipped_cnt,
       SUM(CASE WHEN s.qty_shipped <> (w.qty_produced - w.qty_defect)
                THEN 1 ELSE 0 END) AS mismatch_cnt
FROM fact_work_order w
JOIN fact_sales_fulfillment s
  ON w.ref_order_no = s.order_no AND w.ref_line_no = s.line_no
JOIN dim_date dd ON s.ship_date_key = dd.date_key
WHERE s.ship_date_key <> -1 AND w.ref_line_no IS NOT NULL
GROUP BY dd.year, dd.month
ORDER BY dd.year, dd.month;
