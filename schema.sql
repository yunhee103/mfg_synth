-- =============================================================================
-- mfg_synth 웨어하우스 스키마 (schema.sql)
-- 설계 근거: docs/schema_design.md (grain 선언, 판단 3.1~3.10, STM)
-- 실행 순서: dimension -> item_code_map -> fact (FK 의존성)
-- 재현성: 이 파일 단독 실행으로 빈 웨어하우스가 생성되어야 한다
-- =============================================================================

SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS mfg_dw DEFAULT CHARACTER SET utf8mb4;
USE mfg_dw;

DROP TABLE IF EXISTS
    fact_sales_fulfillment, fact_work_order, fact_production_performance,
    fact_quality_inspection, fact_daily_inventory, fact_procurement_fulfillment,
    item_code_map, dim_item, dim_customer, dim_supplier, dim_equipment, dim_date;

-- =============================================================================
-- 1. Dimension Tables
-- =============================================================================

-- 1.1 날짜 차원
-- 판단 3.10: quarter/day/day_of_week 추가, is_workday 유지 (주중 공휴일 존재)
CREATE TABLE dim_date (
    date_key     INT PRIMARY KEY,          -- YYYYMMDD 정수, -1 = 미상/미발생
    full_date    DATE NULL,                -- -1 행은 NULL
    year         SMALLINT NULL,
    quarter      TINYINT NULL,
    month        TINYINT NULL,
    day          TINYINT NULL,
    day_of_week  TINYINT NULL,             -- 1(월) ~ 7(일)
    is_workday   TINYINT(1) NULL           -- 1: 영업일, 0: 주말/공휴일
) COMMENT '공유 일자 차원 (-1: 미상 행 포함)';

-- 판단 3.3 전제: 미상 행은 fact 적재 이전에 존재해야 한다
INSERT INTO dim_date (date_key, full_date, year, quarter, month, day,
                      day_of_week, is_workday)
VALUES (-1, NULL, NULL, NULL, NULL, NULL, NULL, NULL);

-- 1.2 품목 차원 (SCD Type 2)
-- 판단 3.5: item_group 이력 관리 / 3.8: item_code_mes 제거 / 3.9: unit_price 수록
CREATE TABLE dim_item (
    item_key      INT PRIMARY KEY AUTO_INCREMENT,  -- surrogate key
    item_code_erp VARCHAR(20) NOT NULL,            -- 대표 업무 키 (F-0012 / M-0008)
    item_name     VARCHAR(100),
    item_type     VARCHAR(10) NOT NULL,            -- FG(완제품) / RM(자재), 소스 그대로
    item_group    VARCHAR(50),                     -- SCD Type 2 이력 대상
    unit_price    INT,                             -- 정적 단가 (이력 없음, 판단 3.9)
    valid_from    DATE NOT NULL,                   -- 최초 행 1900-01-01
    valid_to      DATE NOT NULL,                   -- 현재 행 9999-12-31
    is_current    TINYINT(1) NOT NULL DEFAULT 1,
    INDEX idx_item_code_erp (item_code_erp)
    -- SCD 무결성 주의: item_code_erp는 이력 행 때문에 UNIQUE 불가.
    -- "품목당 is_current=1은 1행" / "유효기간 겹침 금지" 규칙은
    -- MariaDB 제약으로 강제할 수 없으므로 ETL 무결성 쿼리로 검증한다
    -- (설계 문서 7절 7번 항목).
) COMMENT '품목 마스터 - 품목군 개편 이력 보존 (원료: items + item_change_log)';

-- 1.3 고객 차원 (판단 3.9: region 수록)
CREATE TABLE dim_customer (
    customer_key  INT PRIMARY KEY AUTO_INCREMENT,
    customer_code VARCHAR(20) UNIQUE NOT NULL,
    customer_name VARCHAR(100),
    region        VARCHAR(20)                      -- 납기 지연의 지리적 단면 (Q7)
) COMMENT '거래처 마스터';

-- 1.4 공급사 차원
CREATE TABLE dim_supplier (
    supplier_key  INT PRIMARY KEY AUTO_INCREMENT,
    supplier_code VARCHAR(20) UNIQUE NOT NULL,
    supplier_name VARCHAR(100)
) COMMENT '공급사 마스터';

-- 1.5 설비 차원 (판단 3.9: install_year 수록)
CREATE TABLE dim_equipment (
    equip_key     INT PRIMARY KEY AUTO_INCREMENT,
    equip_code    VARCHAR(20) UNIQUE NOT NULL,     -- EQ-01 ~ EQ-08
    equip_name    VARCHAR(50),
    install_year  SMALLINT                          -- 노후화 가설 보조 변수 (Q8·Q9)
) COMMENT '설비 마스터';

-- 1.6 품목코드 매핑 (판단 3.4)
CREATE TABLE item_code_map (
    source_system VARCHAR(10) NOT NULL,             -- 'ERP' / 'MES'
    source_code   VARCHAR(30) NOT NULL,             -- F-0012 / F0012 / CONN-39
    item_key      INT NOT NULL,
    PRIMARY KEY (source_system, source_code),
    FOREIGN KEY (item_key) REFERENCES dim_item(item_key)
) COMMENT '멀티 소스 품목코드 통합 매핑 (Q4의 답이 이 테이블에서 나옴)';

-- =============================================================================
-- 2. Fact Tables
-- =============================================================================

-- 2.1 수주 이행
-- grain: 수주 상세 라인 1개 = 1행 (키: order_no + line_no) [Accumulating Snapshot]
CREATE TABLE fact_sales_fulfillment (
    sales_key        BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_no         VARCHAR(20) NOT NULL,          -- degenerate dimension
    line_no          TINYINT NOT NULL,
    customer_key     INT NOT NULL,
    item_key         INT NOT NULL,
    order_date_key   INT NOT NULL,
    due_date_key     INT NOT NULL,
    ship_date_key    INT NOT NULL DEFAULT -1,       -- 미출하 = -1 (판단 3.3)
    qty_ordered      INT NOT NULL,
    qty_shipped      INT NOT NULL DEFAULT 0,
    lead_time_days   SMALLINT NULL,                 -- ship - order (미출하 NULL)
    delay_days       SMALLINT NULL,                 -- ship - due (양수 = 지연)
    UNIQUE KEY uk_order_line (order_no, line_no),
    FOREIGN KEY (customer_key)   REFERENCES dim_customer(customer_key),
    FOREIGN KEY (item_key)       REFERENCES dim_item(item_key),
    FOREIGN KEY (order_date_key) REFERENCES dim_date(date_key),
    FOREIGN KEY (due_date_key)   REFERENCES dim_date(date_key),
    FOREIGN KEY (ship_date_key)  REFERENCES dim_date(date_key)
) COMMENT '수주 1라인의 생애주기 (Q3, Q7)';

-- 2.2 작업지시 이행 (판단 3.2 신설)
-- grain: 작업지시 1건 = 1행 (키: work_order_no) [Accumulating Snapshot]
CREATE TABLE fact_work_order (
    work_order_key   BIGINT PRIMARY KEY AUTO_INCREMENT,
    work_order_no    VARCHAR(20) UNIQUE NOT NULL,
    item_key         INT NOT NULL,
    equip_key        INT NOT NULL,
    ref_order_no     VARCHAR(20),                   -- 수주 연결 (Q2)
    ref_line_no      TINYINT,                       -- REF_ORD_NO + ITEM_CD로 도출
    reg_date_key     INT NOT NULL,                  -- 지시 생성일
    rel_date_key     INT NOT NULL DEFAULT -1,       -- 착수일, 미착수 = -1
    cmpl_date_key    INT NOT NULL DEFAULT -1,       -- 완료일, 미완료 = -1
    qty_planned      INT NOT NULL,
    qty_produced     INT NOT NULL DEFAULT 0,        -- 총 생산 합계
    qty_defect       INT NOT NULL DEFAULT 0,        -- 불량 합계 (양품 = produced - defect)
    FOREIGN KEY (item_key)      REFERENCES dim_item(item_key),
    FOREIGN KEY (equip_key)     REFERENCES dim_equipment(equip_key),
    FOREIGN KEY (reg_date_key)  REFERENCES dim_date(date_key),
    FOREIGN KEY (rel_date_key)  REFERENCES dim_date(date_key),
    FOREIGN KEY (cmpl_date_key) REFERENCES dim_date(date_key)
) COMMENT '작업지시 생성-착수-완료 생애주기 (Q1, Q2)';

-- 2.3 생산 실적
-- grain: 작업지시 1건 x 생산일 1일 = 1행 (키: work_order_no + date_key) [Transaction]
CREATE TABLE fact_production_performance (
    prod_perf_key    BIGINT PRIMARY KEY AUTO_INCREMENT,
    work_order_no    VARCHAR(20) NOT NULL,          -- degenerate dimension
    date_key         INT NOT NULL,
    item_key         INT NOT NULL,
    equip_key        INT NOT NULL,
    qty_produced     INT NOT NULL,                  -- 당일 총 생산 (PROD_QTY)
    qty_defect       INT NOT NULL,                  -- 당일 불량 (DEF_QTY)
    cycle_time_s     DECIMAL(8,2) NULL,             -- 실측, 소스 결측 존재
    UNIQUE KEY uk_wo_date (work_order_no, date_key),
    FOREIGN KEY (date_key)  REFERENCES dim_date(date_key),
    FOREIGN KEY (item_key)  REFERENCES dim_item(item_key),
    FOREIGN KEY (equip_key) REFERENCES dim_equipment(equip_key)
) COMMENT '일자별 설비별 생산 기록 (Q8, Q9)';

-- 2.4 품질 검사
-- grain: 작업지시(LOT) 1건에 대한 검사 1회 = 1행 (키: work_order_no) [Transaction]
CREATE TABLE fact_quality_inspection (
    inspection_key   BIGINT PRIMARY KEY AUTO_INCREMENT,
    work_order_no    VARCHAR(20) UNIQUE NOT NULL,   -- degenerate dimension
    item_key         INT NOT NULL,
    date_key         INT NOT NULL,                  -- 검사일
    qty_lot          INT NULL,                      -- 문자열 숫자 정규화 (STM 6.3)
    qty_sample       INT NOT NULL,
    qty_defect       INT NOT NULL,
    decision         VARCHAR(10) NOT NULL,          -- 합격 / 불합격
    inspector_nm     VARCHAR(20),                   -- 오타 정규화 후
    FOREIGN KEY (item_key) REFERENCES dim_item(item_key),
    FOREIGN KEY (date_key) REFERENCES dim_date(date_key)
) COMMENT 'LOT 검사 결과 (Q9) - 불합격은 재작업 지연으로 이어짐';

-- 2.5 일별 재고
-- grain: 자재 1개 x 1일(마감) = 1행 (키: item_key + date_key) [Periodic Snapshot]
-- 주의: semi-additive - 날짜 축으로 SUM 금지 (AVG 또는 시점값만)
CREATE TABLE fact_daily_inventory (
    date_key         INT NOT NULL,
    item_key         INT NOT NULL,
    qty_on_hand      INT NOT NULL,                  -- 마감 보유량
    qty_on_order     INT NOT NULL DEFAULT 0,        -- 발주잔량
    PRIMARY KEY (date_key, item_key),
    FOREIGN KEY (date_key) REFERENCES dim_date(date_key),
    FOREIGN KEY (item_key) REFERENCES dim_item(item_key)
) COMMENT '자재 일일 마감 재고 스냅샷 (Q10)';

-- 2.6 구매 이행 (판단 3.1 통합)
-- grain: 구매 발주 1건 = 1행 (키: po_no) [Accumulating Snapshot]
CREATE TABLE fact_procurement_fulfillment (
    proc_key          BIGINT PRIMARY KEY AUTO_INCREMENT,
    po_no             VARCHAR(20) UNIQUE NOT NULL,  -- degenerate dimension
    supplier_key      INT NOT NULL,
    item_key          INT NOT NULL,
    order_date_key    INT NOT NULL,
    promised_date_key INT NOT NULL,
    receipt_date_key  INT NOT NULL DEFAULT -1,      -- 미입고 = -1 (판단 3.3)
    qty_ordered       INT NOT NULL,
    qty_received      INT NOT NULL DEFAULT 0,
    FOREIGN KEY (supplier_key)      REFERENCES dim_supplier(supplier_key),
    FOREIGN KEY (item_key)          REFERENCES dim_item(item_key),
    FOREIGN KEY (order_date_key)    REFERENCES dim_date(date_key),
    FOREIGN KEY (promised_date_key) REFERENCES dim_date(date_key),
    FOREIGN KEY (receipt_date_key)  REFERENCES dim_date(date_key)
) COMMENT '발주-입고 생애주기 (Q6)';