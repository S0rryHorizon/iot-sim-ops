-- V001__init_schema.sql
-- iot_sim_ops (Simplified) - Initialize schema (non-destructive)
-- Creates database (if missing), core tables, and view.
-- Token model: Opaque token persisted in `auth_token` (no Redis).

CREATE DATABASE IF NOT EXISTS iot_sim_ops
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE iot_sim_ops;

CREATE TABLE IF NOT EXISTS app_credential (
  appid           VARCHAR(64) PRIMARY KEY,
  password_hash   VARCHAR(255) NOT NULL,
  is_active       TINYINT(1) NOT NULL DEFAULT 1,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sim_card (
  iccid           VARCHAR(32) PRIMARY KEY,
  msisdn          VARCHAR(20) UNIQUE,
  status          ENUM('ACTIVE','SUSPENDED','DEACTIVATED') NOT NULL DEFAULT 'ACTIVE',
  activated_at    DATETIME NULL,
  deactivated_at  DATETIME NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS usage_monthly (
  iccid           VARCHAR(32) NOT NULL,
  month           CHAR(7)     NOT NULL,              -- 'YYYY-MM'
  plan_total_mb   INT         NOT NULL,              -- Base plan total (MB)
  used_mb         DECIMAL(10,2) NOT NULL DEFAULT 0,  -- Used (MB)
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (iccid, month),
  CONSTRAINT fk_usage_iccid FOREIGN KEY (iccid) REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE CASCADE,
  INDEX idx_usage_month (month)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS purchase_order (
  order_id        VARCHAR(32) PRIMARY KEY,          -- e.g., PO202508210001
  iccid           VARCHAR(32) NOT NULL,
  month           CHAR(7)     NOT NULL,
  package_mb      INT         NOT NULL,             -- Add-on package (MB)
  price_cent      INT         NOT NULL,
  status          ENUM('PENDING','SUCCESS','FAILED') NOT NULL DEFAULT 'PENDING',
  product_id      VARCHAR(64) NULL,
  transid         VARCHAR(64) NOT NULL,             -- Idempotency key from client
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_po_iccid FOREIGN KEY (iccid) REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  UNIQUE KEY uk_po_transid (transid),
  INDEX idx_po_iccid_month (iccid, month),
  INDEX idx_po_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS auth_token (
  token         CHAR(64) PRIMARY KEY,
  appid         VARCHAR(64) NOT NULL,
  expires_at    DATETIME NOT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_token_app FOREIGN KEY (appid) REFERENCES app_credential(appid)
    ON UPDATE CASCADE ON DELETE CASCADE,
  INDEX idx_token_exp (expires_at)
) ENGINE=InnoDB;

CREATE OR REPLACE VIEW v_usage_effective AS
SELECT
  u.iccid,
  u.month,
  u.plan_total_mb
    + COALESCE((SELECT SUM(po.package_mb)
                FROM purchase_order po
                WHERE po.iccid = u.iccid
                  AND po.month = u.month
                  AND po.status = 'SUCCESS'), 0) AS effective_total_mb,
  u.used_mb,
  (u.plan_total_mb
    + COALESCE((SELECT SUM(po.package_mb)
                FROM purchase_order po
                WHERE po.iccid = u.iccid
                  AND po.month = u.month
                  AND po.status = 'SUCCESS'), 0) - u.used_mb) AS remain_mb,
  'MB' AS unit,
  u.updated_at AS last_update
FROM usage_monthly u;