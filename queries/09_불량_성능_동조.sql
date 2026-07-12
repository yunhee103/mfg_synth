-- Q9. 불량률은 라인/시기별로 어떻게 다르고, 사이클타임 악화와 겹치는가?
-- 보는 법: 9a 결과를 Q8a 옆에 놓고 같은 라인·같은 시기에 둘 다
--   움직이는지 본다. 같이 움직이면 공통 원인(설비 상태) 가설 강화.
SELECT de.equip_code, dd.quarter,
       SUM(f.qty_defect) / SUM(f.qty_produced) * 100 AS defect_pct,
       AVG(f.cycle_time_s) AS avg_ct
FROM fact_production_performance f
JOIN dim_equipment de ON f.equip_key = de.equip_key
JOIN dim_date dd ON f.date_key = dd.date_key
WHERE dd.year = 2025
GROUP BY de.equip_code, dd.quarter
ORDER BY de.equip_code, dd.quarter;

-- 9b. 검사 불합격률의 라인별 분포 (검사 fact와 교차 검증)
SELECT de.equip_code,
       COUNT(*) AS insp_cnt,
       SUM(CASE WHEN q.decision = '불합격' THEN 1 ELSE 0 END)
           / COUNT(*) * 100 AS fail_pct
FROM fact_quality_inspection q
JOIN fact_work_order w ON q.work_order_no = w.work_order_no
JOIN dim_equipment de ON w.equip_key = de.equip_key
GROUP BY de.equip_code
ORDER BY fail_pct DESC;
