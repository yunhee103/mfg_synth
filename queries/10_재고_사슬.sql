-- Q10. 재고가 바닥난 자재는? 그 시점이 작업지시 대기(Q1)/공급 지연(Q6)과 겹치나?
-- 보는 법: 10a에서 바닥 자재와 시기를 잡고 -> 10b 월별 3개 지표를
--   나란히 놓는다 (dim_date 축 병렬 비교 - 리허설 Q10 판정의 실행).
--   세 곡선이 같은 달에 움직이면 사슬(공급지연->결품->대기)이 성립한다.

-- 10a. 재고 0 도달 자재: 어떤 자재가, 몇 번, 어느 달에
SELECT di.item_code_erp, dd.year, dd.month,
       COUNT(*) AS zero_days
FROM fact_daily_inventory f
JOIN dim_item di ON f.item_key = di.item_key
JOIN dim_date dd ON f.date_key = dd.date_key
WHERE f.qty_on_hand = 0
GROUP BY di.item_code_erp, dd.year, dd.month
ORDER BY dd.year, dd.month, zero_days DESC;

-- 10b. 월별 병렬 비교: 결품일수 / 평균 대기일 / 평균 공급지연
SELECT m.ym,
       COALESCE(z.zero_days, 0)   AS stockout_days,
       COALESCE(w.avg_wait, 0)    AS avg_wo_wait_days,
       COALESCE(p.avg_delay, 0)   AS avg_supply_delay_days
FROM (SELECT DISTINCT CONCAT(year,'-',LPAD(month,2,'0')) AS ym
      FROM dim_date WHERE year = 2025) m
LEFT JOIN (
    SELECT CONCAT(dd.year,'-',LPAD(dd.month,2,'0')) AS ym, COUNT(*) AS zero_days
    FROM fact_daily_inventory f JOIN dim_date dd ON f.date_key = dd.date_key
    WHERE f.qty_on_hand = 0 GROUP BY ym) z ON z.ym = m.ym
LEFT JOIN (
    SELECT CONCAT(dd.year,'-',LPAD(dd.month,2,'0')) AS ym,
           AVG(DATEDIFF(dr.full_date, dd2.full_date)) AS avg_wait
    FROM fact_work_order f
    JOIN dim_date dd  ON f.reg_date_key = dd.date_key
    JOIN dim_date dd2 ON f.reg_date_key = dd2.date_key
    JOIN dim_date dr  ON f.rel_date_key = dr.date_key
    WHERE f.rel_date_key <> -1 GROUP BY ym) w ON w.ym = m.ym
LEFT JOIN (
    SELECT CONCAT(dd.year,'-',LPAD(dd.month,2,'0')) AS ym,
           AVG(DATEDIFF(dr.full_date, dp.full_date)) AS avg_delay
    FROM fact_procurement_fulfillment f
    JOIN dim_date dd ON f.order_date_key = dd.date_key
    JOIN dim_date dp ON f.promised_date_key = dp.date_key
    JOIN dim_date dr ON f.receipt_date_key = dr.date_key
    WHERE f.receipt_date_key <> -1 GROUP BY ym) p ON p.ym = m.ym
ORDER BY m.ym;
