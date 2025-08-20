-- =========================================================
-- iot-sim-ops / db/schema.sql
-- 目标：创建库 iot_sim_ops 与 4 张表（offering / sim_card / usage_monthly / sim_op_log）
-- 状态枚举：0=INACTIVE,1=ACTIVE,2=SUSPENDED,3=THROTTLED,4=TERMINATED
-- 注意：包含 DROP TABLE 以便重复执行，务必仅在开发库使用
-- =========================================================


-- db/schema.sql
-- 创建数据库与基础表结构（MySQL 8+）
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS=0;

CREATE DATABASE IF NOT EXISTS iot_sim_ops
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE iot_sim_ops;

-- 为便于反复执行，先按依赖顺序安全删除
DROP TABLE IF EXISTS sim_op_log;
DROP TABLE IF EXISTS usage_monthly;
DROP TABLE IF EXISTS sim_card;
DROP TABLE IF EXISTS offering;

-- 资费/套餐
CREATE TABLE offering (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  offering_id VARCHAR(20) NOT NULL UNIQUE,   -- 对外套餐ID（如 21000032）
  name        VARCHAR(64) NOT NULL,
  total_kb    BIGINT NOT NULL                -- 月总量（KB）
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 物联卡
CREATE TABLE sim_card (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  iccid CHAR(20)  NOT NULL UNIQUE,           -- 20位
  msisdn CHAR(13) NOT NULL UNIQUE,           -- 最长13位
  imsi  CHAR(15)  NOT NULL UNIQUE,           -- 最长15位
  status TINYINT NOT NULL,                   -- 0=INACTIVE,1=ACTIVE,2=SUSPENDED,3=THROTTLED,4=TERMINATED
  throttle_kbps INT DEFAULT NULL,            -- 限流值（THROTTLED时生效）
  is_shared_pool BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否流量池/共享卡（本接口不可查）
  offering_ref BIGINT NOT NULL,              -- 关联套餐
  owner VARCHAR(64) NULL,
  activated_at  DATETIME NULL,
  terminated_at DATETIME NULL,
  CONSTRAINT fk_sim_offering FOREIGN KEY (offering_ref) REFERENCES offering(id),
  INDEX idx_sim_status (status),
  INDEX idx_sim_owner (owner)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 月用量（一卡一月一行）
CREATE TABLE usage_monthly (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  sim_id BIGINT NOT NULL,
  month  CHAR(7) NOT NULL,                   -- 'YYYY-MM'
  use_kb BIGINT NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_sim_month (sim_id, month),
  CONSTRAINT fk_usage_sim FOREIGN KEY (sim_id) REFERENCES sim_card(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 操作日志（开/停/限流/复机/注销/新增等）
CREATE TABLE sim_op_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  sim_id BIGINT NOT NULL,
  op_type  VARCHAR(20) NOT NULL,             -- ACTIVATE/SUSPEND/THROTTLE/RESUME/TERMINATE/CREATE
  op_detail JSON NULL,
  operator  VARCHAR(64) NOT NULL,
  op_time   DATETIME NOT NULL,
  CONSTRAINT fk_op_sim FOREIGN KEY (sim_id) REFERENCES sim_card(id),
  INDEX idx_sim_time (sim_id, op_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS=1;
