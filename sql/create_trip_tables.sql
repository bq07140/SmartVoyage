-- 行程管家相关表：租车表 + 保险表
-- 注意：旅游团数据不走数据库，而是通过 Milvus 向量检索（RAG 模式）

USE travel_rag;

-- ==================== 租车表 ====================
DROP TABLE IF EXISTS car_rentals;
CREATE TABLE car_rentals (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增',
    company VARCHAR(50) NOT NULL COMMENT '租车公司名称',
    pickup_city VARCHAR(50) NOT NULL COMMENT '取车城市',
    return_city VARCHAR(50) NOT NULL COMMENT '还车城市',
    pickup_date DATE NOT NULL COMMENT '取车日期',
    car_type VARCHAR(20) NOT NULL COMMENT '车型分类：经济型/SUV/豪华型/MPV',
    car_model VARCHAR(50) NOT NULL COMMENT '具体车型',
    price_per_day DECIMAL(8,2) NOT NULL COMMENT '每日租金（元）',
    total_available INT NOT NULL DEFAULT 0 COMMENT '可租车辆数',
    transmission VARCHAR(20) NOT NULL COMMENT '变速箱：自动挡/手动挡',
    seats INT NOT NULL COMMENT '座位数',
    deposit DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '押金（元）',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY unique_car (company, car_model, pickup_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='租车信息表';

-- ==================== 保险表 ====================
DROP TABLE IF EXISTS insurances;
CREATE TABLE insurances (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增',
    insurance_type VARCHAR(20) NOT NULL COMMENT '保险类型：综合型/意外型/医疗型/境外型',
    name VARCHAR(100) NOT NULL COMMENT '保险产品名称',
    company VARCHAR(50) NOT NULL COMMENT '保险公司',
    coverage TEXT NOT NULL COMMENT '保障范围说明',
    price DECIMAL(8,2) NOT NULL COMMENT '价格（元/份）',
    duration_days INT NOT NULL COMMENT '保障天数',
    max_coverage DECIMAL(10,2) NOT NULL COMMENT '最高赔付金额（元）',
    medical_coverage DECIMAL(10,2) DEFAULT 0 COMMENT '医疗保障额度（元）',
    baggage_coverage DECIMAL(10,2) DEFAULT 0 COMMENT '行李保障额度（元）',
    flight_delay BOOLEAN DEFAULT FALSE COMMENT '是否包含航班延误',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY unique_insurance (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='旅行保险信息表';
