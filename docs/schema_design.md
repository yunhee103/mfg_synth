# 웨어하우스 스키마 설계 문서

프로젝트: 제조 운영 데이터 통합 파이프라인 (mfg_synth)
단계: 2단계 (스키마 설계) 산출물
최종 갱신: 2026-07-12 (판단 3.8~3.10 확정 반영)

---

## 1. 설계 개요

소스 3계(ERP MariaDB, MES CSV, 품질검사 Excel)를 스타 스키마 웨어하우스로
통합한다. 스키마는 분석 질문 10개(탐색 워크시트 섹션 4)에서 역산하여
설계했고, 질문 리허설로 검증한 뒤 확정했다.

구성: dimension 5개 + 코드 매핑 테이블 1개 + fact 6개

---

## 2. Grain 선언

모든 fact는 DDL 첫 주석에 아래 선언문을 그대로 포함한다.

| fact | grain (1행 = ?) | 유일 키 | 유형 |
|---|---|---|---|
| fact_sales_fulfillment | 수주 상세 라인 1개 | order_no + line_no | Accumulating Snapshot |
| fact_work_order | 작업지시 1건 | work_order_no | Accumulating Snapshot |
| fact_production_performance | 작업지시 1건 x 생산일 1일 | work_order_no + date_key | Transaction |
| fact_quality_inspection | 작업지시(LOT) 1건에 대한 검사 1회 | work_order_no | Transaction |
| fact_daily_inventory | 자재 1개 x 1일(마감 시점) | item_key + date_key | Periodic Snapshot |
| fact_procurement_fulfillment | 구매 발주 1건 | po_no | Accumulating Snapshot |

패턴 메모: "약속과 진행이 있는 프로세스마다 accumulating snapshot 하나"
— 수주 이행 / 작업지시 이행 / 구매 이행 세 개가 같은 꼴로 나란히 선다.

grain 작성 규율 (이 프로젝트에서 배운 것):
- 문장에 들어가는 모든 단어는 실재하는 컬럼/키여야 한다. 추측으로
  선언하지 않는다 (실패 사례: "검사 일련번호", "발주 상세 라인").
- 키가 아닌 것(설비, 품목 등 자동으로 따라오는 속성)은 grain에 넣지 않는다.

---

## 3. 설계 판단 기록

### 3.1 발주/입고 fact 통합
purchase_orders와 material_receipts는 po_no 1:1 관계(고아 0건 확인)이므로
별도 fact 2개가 아닌 fact_procurement_fulfillment 1개로 통합.
근거: Q6("약속 대비 실제 입고")이 한 테이블 내 날짜 뺄셈으로 풀린다.

### 3.2 fact_work_order 신설
질문 리허설에서 Q1(작업지시 대기시간)이 기존 설계로 답 불가 판정.
일별 실적(transaction)과 지시 생애주기(accumulating)는 grain이 달라
한 테이블에 담을 수 없으므로 fact를 추가하는 것으로 해결.
원료: mes_work_orders (REG_DT / REL_DT / CMPL_DT).
부수 효과: REF_ORD_NO + ITEM_CD로 수주 라인을 도출해 담으면
Q2(양품-출하 대사)가 라인 단위로 정확해진다.

### 3.3 미발생 날짜 처리: -1 (미상 행) 방식
미출하/미착수/미완료/미입고 등 아직 발생하지 않은 날짜 키는
NULL 대신 -1로 통일하고, dim_date에 date_key = -1 미상 행을 둔다.
적용 대상: ship_date_key, rel_date_key, cmpl_date_key,
actual_receipt_date_key — 전부 NOT NULL DEFAULT -1 + FK.
전제: dim_date의 -1 행 INSERT가 fact 적재보다 먼저 실행되어야 한다.
장점: 모든 날짜 키에 FK가 일관되게 걸리고, 조인 시 행 소실이 없다.

### 3.4 품목코드 매핑: 별도 매핑 테이블 (item_code_map)
문제: MES 코드 불일치가 두 유형 — (1) 하이픈 차이(F0012),
(2) 마스터 미등록 구명칭(CONN-39 등). dim_item의 코드 컬럼 2개로는
품목당 코드 3개 이상을 수용할 수 없다.

선택: (a) 별도 매핑 테이블. (대안 (b): dim_item에 legacy 컬럼 추가)

근거: 현 데이터는 품목당 구명칭이 최대 1개라 (b)로도 동작하지만,
(b)는 새 소스 시스템이 추가될 때마다 스키마 변경(컬럼 추가)이 필요하고
소스 수만큼 컬럼이 옆으로 늘어난다. (a)는 소스가 늘어도 행 추가만으로
흡수되며, 코드의 출처(source_system)가 데이터로 남아 Q4(매핑 유형 분석)를
매핑 테이블 단독 집계로 답할 수 있다. 대가는 코드 조회 시 조인 1회
추가이며, 조회 빈도 대비 수용 가능하다고 판단.

```sql
CREATE TABLE item_code_map (
    source_system VARCHAR(10) NOT NULL,   -- 'ERP' / 'MES'
    source_code   VARCHAR(30) NOT NULL,   -- F-0012 / F0012 / CONN-39
    item_key      INT NOT NULL,
    PRIMARY KEY (source_system, source_code),
    FOREIGN KEY (item_key) REFERENCES dim_item(item_key)
) COMMENT '멀티 소스 품목코드 통합 매핑';
```

파급: 모든 STM의 item_key 조회 규칙이 이 테이블 경유로 변경된다.

### 3.5 SCD Type 2 (dim_item.item_group)
원료: ERP items(최종 상태) + item_change_log(감사 이력, 8건).
2025-07-01 품목군 개편 대상 품목은 dim_item에 2행이 된다
(과거 행 valid_to = 2025-06-30 / 현재 행 is_current = 1).
6월 생산 fact는 과거 행의 item_key를 참조해야 한다 — ETL 적재 시
거래 일자와 valid_from/valid_to를 대조해 키를 배정한다.
효과: 품목군 시계열 분석을 "당시 기준/현재 기준" 양쪽으로 수행 가능.

### 3.6 Q5의 ETL 이관
"Excel 날짜 형식 복원 가능성"은 웨어하우스 질문이 아니라 staging/ETL
검증 항목으로 이관. ETL이 산출하는 데이터 품질 리포트(파싱 실패 건수,
복원 불가 건수)가 Q5의 답을 대신한다.
대비: Q4(코드 매핑)는 매핑 결과가 창고(item_code_map)에 남으므로
웨어하우스에서 답해진다.

### 3.7 소스 불변 원칙
원본(ERP 덤프, MES CSV, Excel)은 읽기 전용. 변환은 ETL 흐름 중에만
발생하며 원본 파일은 절대 수정하지 않는다 (재현성 보장).

### 3.8 dim_item.item_code_mes 제거
MES 코드의 진실은 item_code_map에 있고, dim_item에 중복 보관하면
두 곳이 어긋날 수 있으므로 단일 진실 원칙(single source of truth)에
따라 제거. dim_item에는 대표 업무 키인 item_code_erp만 남긴다.

### 3.9 소스 속성 컬럼 3건 수록 결정
- items.unit_price -> dim_item에 수록. 지연·불량의 금액 환산
  (영향도 정량화)용 정적 단가. 단, 소스에 단가 이력이 없는
  한 시점 값이므로 가격 변동 분석에는 사용 불가함을 명시.
- customers.region -> dim_customer에 수록. 납기 지연 분포(Q7)
  분석 시 지리적 단면 제공.
- equipment.install_year -> dim_equipment에 수록. 설비 저하 분석
  (Q8·Q9)에서 설치 연식이 노후화 가설의 보조 변수가 됨.

수록 판단 기준: "어느 질문/분석에 쓰이는가"에 답할 수 있는 컬럼만
싣는다. "추후 쓸지도 모른다"는 수록 근거로 인정하지 않는다.

### 3.10 dim_date 확장 (변경 기록)
확정본 대비 변경: quarter, day, day_of_week 컬럼 추가 (분기·요일
단면 분석용). is_weekend로의 대체안은 기각하고 is_workday를 유지 —
주중 공휴일(설날, 추석 등)이 존재하므로 가동률·영업일 기준 리드타임
계산에는 "주말 여부"가 아니라 "영업일 여부"가 필요하다.
운영 규율: 확정된 스키마를 변경할 때는 본 문서에 변경 사실과 이유를
기록한다 (조용한 변경 금지).

---

## 4. 검증된 사실과 미결 항목

### 확인된 사실 (데이터로 검증, 날짜 명기)
- 검사 Excel의 품목코드는 ERP 형식(F-0012)이다. MES 형식이라는 초기
  가정은 오류였음. (2026-07-11 파일 육안 확인)
- purchase_orders는 po_no당 자재 1종 (헤더/상세 구조 없음).
- 소스 참조 무결성: 5개 관계 orphan 0건 (explore.py [B]).

### 완료 기록 (2026-07-12, 2단계 종료)
- [x] STM 9장 전체 작성 완료 (6절)
- [x] schema.sql 통합 완성본 작성 (판단 3.3, 3.8~3.10 반영)
- [x] MariaDB 실행 검증 통과: 12개 테이블 생성, -1 미상 행 확인,
      FK 거부 동작 확인 (ERROR 1452, orphan 차단)

---

## 5. 질문 리허설 결과

| 질문 | 필요 fact | 계산 스케치 | 판정 |
|---|---|---|---|
| Q1 대기시간 | fact_work_order | REL - REG 간격, 분포 | 통과 (3.2로 해결) |
| Q2 양품-출하 대사 | fact_work_order + fact_sales_fulfillment | 수주 라인 단위 수량 비교 | 통과 (보통) |
| Q3 미출하 분포 | fact_sales_fulfillment | ship_date_key = -1 행의 시계열 분포 | 통과 (쉬움) |
| Q4 코드 매핑 유형 | item_code_map | source_system/매칭 유형별 집계 | 통과 (쉬움) |
| Q5 날짜 복원 | - | ETL 데이터 품질 리포트로 이관 | 이관 (3.6) |
| Q6 공급 납기 | fact_procurement_fulfillment | 입고일 - 약속일, 공급사별 | 통과 (쉬움) |
| Q7 납기 준수 | fact_sales_fulfillment | ship - due, 거래처/품목군/월별 | 통과 (쉬움) |
| Q8 사이클타임 추이 | fact_production_performance | 설비/일자별 AVG, 윈도우 함수 | 통과 (보통) |
| Q9 불량-성능 동조 | fact_production_performance + fact_quality_inspection | work_order_no 조인, 구간 비교 | 통과 (보통) |
| Q10 재고 사슬 | fact_daily_inventory + fact_work_order + fact_procurement_fulfillment | 각 fact 단독 집계 후 dim_date 축으로 병렬 비교 | 통과 (어려움) |

Q10 재심 기록: "fact 3개 = 설계 구멍"으로 초기 오판했으나, 실제로는
질문 3개가 포개진 것으로 각각 fact 1개짜리이며, 공유 차원(dim_date)으로
병렬 비교하는 스타 스키마의 표준 사용법에 해당. 별도 '중단 사건' fact나
bridge는 불필요 (소스에 해당 기록이 존재하지 않아 만들 수도 없음).

---

## 6. Source-to-Target Mapping (STM)

규칙: 소스 칸을 채울 수 없는 컬럼은 존재할 수 없다. 도출 컬럼은
변환 규칙 칸에 계산식을 명시해야 생존한다.

### 6.1 dim_item

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| item_key | - | surrogate (auto increment) |
| item_code_erp | erp.items.item_code | 그대로 (대표 업무 키) |
| item_name | erp.items.item_name | 그대로 |
| item_type | erp.items.item_type | 그대로 (FG/RM — 도출하지 않음, 소스에 존재) |
| item_group | erp.items.item_group + item_change_log | SCD Type 2 이력 전개 |
| unit_price | erp.items.unit_price | 그대로 (정적 단가, 판단 3.9) |
| valid_from | item_change_log.change_date | 최초 행은 1900-01-01 |
| valid_to | item_change_log.change_date | 다음 변경 전일, 현재 행은 9999-12-31 |
| is_current | - | 최신 행 1, 과거 행 0 |

MES 코드(F0012)와 구명칭(CONN-39)은 item_code_map의 행으로 적재한다
(판단 3.4, 3.8).

### 6.2 fact_daily_inventory

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| date_key | inventory_daily.snap_date | YYYYMMDD 정수 변환 |
| item_key | inventory_daily.material_code | item_code_map (ERP, code) 조회 |
| qty_on_hand | inventory_daily.qty_on_hand | 그대로 |
| qty_on_order | inventory_daily.qty_on_order | 그대로 |

### 6.3 fact_quality_inspection

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| work_order_no | Excel.작업지시번호 | 그대로 (degenerate dimension) |
| date_key | Excel.검사일 | 형식 4종 파싱. 연도 누락 형식(5/2 등)은 파일명의 연월(quality_insp_YYYYMM)로 보정 |
| item_key | Excel.품목코드 | item_code_map (ERP, F-0012) 조회 — 형식 확인 완료 2026-07-11 |
| qty_lot | Excel.LOT수량 | 문자열 숫자(콤마 포함) 정규화 후 정수 변환 |
| qty_sample | Excel.샘플수량 | 그대로 |
| qty_defect | Excel.불량수 | 그대로 |
| decision | Excel.판정 | 그대로 (합격/불합격) |
| inspector_nm | Excel.검사자 | 공백 제거 + 표준 명단 대조로 오타 정규화 |


### 6.4 fact_sales_fulfillment

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| order_no, line_no | erp.sales_order_d | 그대로 (grain 키, UNIQUE 제약) |
| customer_key | erp.sales_order_h.customer_code | order_no 조인 후 dim_customer 조회 |
| item_key | erp.sales_order_d.item_code | item_code_map (ERP) 조회, SCD는 order_date 기준 행 |
| order_date_key | erp.sales_order_h.order_date | YYYYMMDD 변환 |
| due_date_key | erp.sales_order_d.due_date | YYYYMMDD 변환 |
| ship_date_key | erp.shipments.ship_date | (order_no, line_no) 조인. 미출하 = -1 |
| qty_ordered | erp.sales_order_d.qty | 그대로 |
| qty_shipped | erp.shipments.qty_shipped | 미출하 = 0 |
| lead_time_days | - | 도출: ship_date - order_date (미출하 NULL) |
| delay_days | - | 도출: ship_date - due_date (미출하 NULL) |

### 6.5 fact_work_order

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| work_order_no | mes_work_orders.WORK_ORD_NO | 그대로 |
| item_key | mes_work_orders.ITEM_CD | item_code_map (MES) 조회 - 하이픈 없는 코드/구명칭 모두 흡수 |
| equip_key | mes_work_orders.EQP_CD | 하이픈 삽입 정규화(EQ03 -> EQ-03) 후 dim_equipment 조회 |
| ref_order_no | mes_work_orders.REF_ORD_NO | 그대로 |
| ref_line_no | - | 도출: (REF_ORD_NO, item_key)로 sales_order_d에서 line_no 특정. 같은 주문 내 품목 중복이 없음을 전제로 하며, 중복 발견 시 품질 리포트에 기록 |
| reg/rel/cmpl_date_key | REG_DT / REL_DT / CMPL_DT | YYYYMMDD 변환, 미발생 = -1 |
| qty_planned | mes_work_orders.ORD_QTY | 그대로 |
| qty_produced, qty_defect | mes_prod_result 합계 | 작업지시별 SUM(PROD_QTY), SUM(DEF_QTY). 중복행 제거 후 집계 |

### 6.6 fact_production_performance

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| work_order_no | mes_prod_result.WORK_ORD_NO | 그대로 |
| date_key | mes_prod_result.WORK_DT | YYYYMMDD 변환 |
| item_key | mes_prod_result.ITEM_CD | item_code_map (MES) 조회 |
| equip_key | mes_prod_result.EQP_CD | 하이픈 정규화 후 dim_equipment 조회 |
| qty_produced, qty_defect | PROD_QTY, DEF_QTY | 완전 중복행 제거 후 (지시, 일자) 단위 합산 |
| cycle_time_s | mes_prod_result.CYCLE_TM | 그대로, 결측 허용(NULL) - 소스 결측 약 2% |

(WORKER_NM은 미수록: 작업자별 분석은 배치 로테이션으로 인과 귀속이
불가능하다고 판단 - Q8 단면 선택 논의 참조)

### 6.7 dim_customer / dim_supplier / dim_equipment

| 타깃 컬럼 | 소스 | 변환 규칙 |
|---|---|---|
| customer_code, customer_name, region | erp.customers | 그대로 |
| supplier_code, supplier_name | erp.suppliers | 그대로 |
| equip_code, equip_name, install_year | (설비 마스터 부재) | MES EQP_CD 목록에서 코드 도출, equip_name은 코드 기반 생성, install_year는 소스 부재로 NULL 적재. 소스에 설비 마스터 테이블이 없다는 사실 자체를 데이터 품질 리포트에 기록 |

---


## 7. 조립 검증 기록

schema.sql 조립 시 fact DDL 초안과 확정 사항의 불일치를 발견하여
다음과 같이 처리했다.

1. grain 강제 제약 추가: fact_sales_fulfillment에 UNIQUE(order_no, line_no),
   fact_production_performance에 UNIQUE(work_order_no, date_key).
   grain을 선언만 하고 DB가 강제하지 않으면 ETL 버그로 중복 적재 시
   조용히 grain이 깨진다.
2. fact_quality_inspection 컬럼을 STM 6.3 기준으로 재구성
   (초안의 qty_passed/qty_failed는 소스 부재로 폐기된 버전이었음).
3. fact_work_order.qty_produced 주석을 '양품 합계'에서 '총 생산 합계'로
   교정 (소스 PROD_QTY는 총량, 양품 = produced - defect).
4. cycle_time_s는 NULL 허용 (소스 결측 약 2% 존재).
5. actual_receipt_date_key -> receipt_date_key로 개명 (간결성).
6. 설비 마스터가 소스에 없어 dim_equipment.install_year는 NULL 적재
   (판단 3.9의 수록 결정은 유지하되, 값은 소스 확보 시까지 공란).
7. dim_item SCD 무결성 한계 (조립 후 재검토에서 발견): item_code_erp는
   이력 행 존재로 UNIQUE 제약 불가, "품목당 is_current=1은 1행" 및
   "유효기간 겹침 금지" 규칙을 DB가 강제하지 못한다. 보완: 3단계 ETL의
   필수 무결성 쿼리에 (a) is_current=1 중복 검출, (b) valid_from/valid_to
   기간 겹침 검출 두 항목을 등재한다.


## 8. 구현 현황

1. 3단계 ETL 구현 완료: 본 문서의 STM 9장을 etl/ 패키지로 구현.
   무결성 검증 7항목(SCD 중복/기간겹침, grain 중복, 고아, 소스 대사)
   전체 통과. 산출물: etl_quality_report.md
   - 특기: 구명칭 코드 10종을 작업지시 공출현 대조로 데이터 기반 복원
     (ground truth 대조 결과 10/10 일치 - 방법론 검증 성공)
   - 특기: 공휴일 달력 부재를 무활동 평일 추론으로 보완 (19일 추론,
     실제 심어둔 공휴일 수와 일치)

2. 다음: 4단계 분석 - 리허설 표(5절)의 계산 스케치가 분석 쿼리의 출발점이 된다