-- Q4. 매핑 안 되는 코드는 몇 종, 유형은 몇 가지였나?
-- 보는 법: 매핑 테이블 자체가 답이다. 형식 차이(하이픈)와 구명칭을
--   패턴으로 구분하고, ETL 품질 리포트의 복원/미복원 수치와 대조.
SELECT source_system,
       CASE WHEN source_code REGEXP '^[FM]-[0-9]{4}$' THEN 'ERP 표준형'
            WHEN source_code REGEXP '^[FM][0-9]{4}$'  THEN '하이픈 제거형'
            ELSE '구명칭/기타' END AS code_type,
       COUNT(*) AS cnt
FROM item_code_map
GROUP BY source_system, code_type
ORDER BY source_system, code_type;

-- 4b. 구명칭 목록과 연결된 정식 코드
SELECT m.source_code AS legacy_code, di.item_code_erp, di.item_name
FROM item_code_map m
JOIN dim_item di ON m.item_key = di.item_key AND di.is_current = 1
WHERE m.source_system = 'MES'
  AND m.source_code NOT REGEXP '^[FM][0-9]{4}$';
