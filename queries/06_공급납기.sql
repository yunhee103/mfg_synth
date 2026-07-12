-- Q6. 자재 입고는 약속 대비 얼마나 지켜지나? 상습 지연 공급사는? 언제부터?
-- 보는 법: (1) 공급사별 평균/최대 지연 -> 튀는 공급사 특정
--   (2) 그 공급사의 "월별" 추이 -> 언제부터인지 특정. 시작 시점이
--   보이면 그 날짜를 기록해두고 Q10, Q1과 겹치는지 대조한다.

-- 6a. 공급사별 지연 요약 (미입고 제외)
SELECT ds.supplier_code, ds.supplier_name,
       COUNT(*) AS po_cnt,
       AVG(DATEDIFF(dr.full_date, dp.full_date)) AS avg_delay_days,
       MAX(DATEDIFF(dr.full_date, dp.full_date)) AS max_delay_days,
       SUM(CASE WHEN dr.full_date > dp.full_date THEN 1 ELSE 0 END)
           / COUNT(*) * 100 AS late_rate_pct
FROM fact_procurement_fulfillment f
JOIN dim_supplier ds ON f.supplier_key = ds.supplier_key
JOIN dim_date dp ON f.promised_date_key = dp.date_key
JOIN dim_date dr ON f.receipt_date_key = dr.date_key
WHERE f.receipt_date_key <> -1
GROUP BY ds.supplier_code, ds.supplier_name
ORDER BY avg_delay_days DESC;

-- 6b. 지연 상위 공급사의 월별 추이 (6a에서 특정한 코드를 넣어라)
SELECT dd.year, dd.month,
       AVG(DATEDIFF(dr.full_date, dp.full_date)) AS avg_delay_days,
       COUNT(*) AS po_cnt
FROM fact_procurement_fulfillment f
JOIN dim_supplier ds ON f.supplier_key = ds.supplier_key
JOIN dim_date dd ON f.order_date_key = dd.date_key
JOIN dim_date dp ON f.promised_date_key = dp.date_key
JOIN dim_date dr ON f.receipt_date_key = dr.date_key
WHERE f.receipt_date_key <> -1
  AND ds.supplier_code = '__여기에_공급사코드__'
GROUP BY dd.year, dd.month
ORDER BY dd.year, dd.month;
