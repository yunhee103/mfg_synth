-- Q8. 라인별 사이클타임은 서로/시간에 따라 어떻게 다른가? 언제부터?
-- 보는 법: 8a에서 튀는 라인을 찾고 -> 8b 주별 추이로 변화 "시작 주"를
--   특정 -> 8c 같은 품목 통제 비교로 "품목 구성 탓" 반론을 제거한다.
--   시작 주를 특정하면 그 날짜가 이 분석의 핵심 발견이다.

-- 8a. 라인별 분기별 평균 사이클타임
SELECT de.equip_code, dd.quarter,
       AVG(f.cycle_time_s) AS avg_ct, COUNT(*) AS rec_cnt
FROM fact_production_performance f
JOIN dim_equipment de ON f.equip_key = de.equip_key
JOIN dim_date dd ON f.date_key = dd.date_key
WHERE dd.year = 2025 AND f.cycle_time_s IS NOT NULL
GROUP BY de.equip_code, dd.quarter
ORDER BY de.equip_code, dd.quarter;

-- 8b. 특정 라인의 주별 추이 + 4주 이동평균 (윈도우 함수)
--   (8a에서 특정한 라인 코드를 넣어라)
SELECT yw, avg_ct,
       AVG(avg_ct) OVER (ORDER BY yw ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
           AS ma_4w
FROM (
    SELECT DATE_FORMAT(dd.full_date, '%x-W%v') AS yw,
           AVG(f.cycle_time_s) AS avg_ct
    FROM fact_production_performance f
    JOIN dim_equipment de ON f.equip_key = de.equip_key
    JOIN dim_date dd ON f.date_key = dd.date_key
    WHERE de.equip_code = 'EQ-03' AND f.cycle_time_s IS NOT NULL
    GROUP BY yw
) t
ORDER BY yw;

-- 8c. 같은 품목 통제 비교: 그 라인에서 가장 많이 만든 품목 1종의
--   월별 사이클타임 (품목 고정 -> 순수 설비 변화만 남음)
SELECT di.item_code_erp, dd.year, dd.month, AVG(f.cycle_time_s) AS avg_ct
FROM fact_production_performance f
JOIN dim_equipment de ON f.equip_key = de.equip_key
JOIN dim_item di ON f.item_key = di.item_key
JOIN dim_date dd ON f.date_key = dd.date_key
WHERE de.equip_code = 'EQ-03' AND f.cycle_time_s IS NOT NULL
GROUP BY di.item_code_erp, dd.year, dd.month
HAVING COUNT(*) >= 3
ORDER BY di.item_code_erp, dd.year, dd.month;
