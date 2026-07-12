-- Q1. 작업지시 생성일-착수일 간격이 큰 건들은 무엇 때문에 대기했나?
-- 보는 법: (1) 대기일 분포에서 "정상 범위"를 먼저 정하고 (2) 그 밖의
--   건들이 어느 시기/품목에 몰리는지 본다. 시기가 몰리면 그 시기에
--   무슨 일이 있었는지가 다음 질문(Q6, Q10과 연결).
-- 주의: rel_date_key = -1 (미착수)은 대기가 끝나지 않은 건이다.

-- 1a. 월별 평균 대기일수와 미착수 잔량
SELECT dd.year, dd.month,
       COUNT(*)                                   AS wo_cnt,
       AVG(DATEDIFF(dr.full_date, dd.full_date))  AS avg_wait_days,
       MAX(DATEDIFF(dr.full_date, dd.full_date))  AS max_wait_days,
       SUM(CASE WHEN f.rel_date_key = -1 THEN 1 ELSE 0 END) AS not_released
FROM fact_work_order f
JOIN dim_date dd ON f.reg_date_key = dd.date_key
LEFT JOIN dim_date dr ON f.rel_date_key = dr.date_key AND f.rel_date_key <> -1
GROUP BY dd.year, dd.month
ORDER BY dd.year, dd.month;

-- 1b. 대기 상위 30건: 어떤 품목이, 언제
SELECT f.work_order_no, di.item_code_erp, di.item_group,
       dd.full_date AS reg_date,
       DATEDIFF(dr.full_date, dd.full_date) AS wait_days
FROM fact_work_order f
JOIN dim_item di ON f.item_key = di.item_key
JOIN dim_date dd ON f.reg_date_key = dd.date_key
JOIN dim_date dr ON f.rel_date_key = dr.date_key
WHERE f.rel_date_key <> -1
ORDER BY wait_days DESC
LIMIT 30;

-- 1c. 대기 3일 이상 작업지시의 품목이 쓰는 자재 (BOM은 창고 미수록 -> 소스 참조)
--   보는 법: 특정 자재가 반복해서 나오면 Q10(재고 바닥)과 대조할 후보다.
SELECT b.child_item, COUNT(DISTINCT f.work_order_no) AS blocked_wo_cnt
FROM fact_work_order f
JOIN dim_item di ON f.item_key = di.item_key
JOIN dim_date dd ON f.reg_date_key = dd.date_key
JOIN dim_date dr ON f.rel_date_key = dr.date_key
JOIN erp_source.bom b ON b.parent_item = di.item_code_erp
WHERE f.rel_date_key <> -1 AND DATEDIFF(dr.full_date, dd.full_date) >= 3
GROUP BY b.child_item
ORDER BY blocked_wo_cnt DESC
LIMIT 20;
