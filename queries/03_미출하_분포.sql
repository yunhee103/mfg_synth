-- Q3. 미출하 라인은 12월 꼬리인가, 다른 시기에도 흩어져 있는가?
-- 보는 법: 납기월 분포가 데이터 끝(2025-12)에만 몰리면 "자연스러운
--   꼬리"로 판정. 다른 달에도 있으면 그 건들을 개별 추적(왜 안 나갔나).
SELECT dd.year, dd.month, COUNT(*) AS unshipped_cnt
FROM fact_sales_fulfillment f
JOIN dim_date dd ON f.due_date_key = dd.date_key
WHERE f.ship_date_key = -1
GROUP BY dd.year, dd.month
ORDER BY dd.year, dd.month;
