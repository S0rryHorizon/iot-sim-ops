-- iot_sim_ops_reset.sql
-- DROP & RECREATE schema for a clean rebuild, then seed.
CREATE DATABASE IF NOT EXISTS iot_sim_ops
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE iot_sim_ops;

SET FOREIGN_KEY_CHECKS = 0;
DROP VIEW IF EXISTS v_usage_effective;
DROP TABLE IF EXISTS auth_token;
DROP TABLE IF EXISTS purchase_order;
DROP TABLE IF EXISTS usage_monthly;
DROP TABLE IF EXISTS sim_card;
DROP TABLE IF EXISTS app_credential;
SET FOREIGN_KEY_CHECKS = 1;

-- Recreate (same as V001)
CREATE TABLE app_credential (
  appid           VARCHAR(64) PRIMARY KEY,
  password_hash   VARCHAR(255) NOT NULL,
  is_active       TINYINT(1) NOT NULL DEFAULT 1,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE sim_card (
  iccid           VARCHAR(32) PRIMARY KEY,
  msisdn          VARCHAR(20) UNIQUE,
  status          ENUM('ACTIVE','SUSPENDED','DEACTIVATED') NOT NULL DEFAULT 'ACTIVE',
  activated_at    DATETIME NULL,
  deactivated_at  DATETIME NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE usage_monthly (
  iccid           VARCHAR(32) NOT NULL,
  month           CHAR(7)     NOT NULL,
  plan_total_mb   INT         NOT NULL,
  used_mb         DECIMAL(10,2) NOT NULL DEFAULT 0,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (iccid, month),
  CONSTRAINT fk_usage_iccid FOREIGN KEY (iccid) REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE CASCADE,
  INDEX idx_usage_month (month)
) ENGINE=InnoDB;

CREATE TABLE purchase_order (
  order_id        VARCHAR(32) PRIMARY KEY,
  iccid           VARCHAR(32) NOT NULL,
  month           CHAR(7)     NOT NULL,
  package_mb      INT         NOT NULL,
  price_cent      INT         NOT NULL,
  status          ENUM('PENDING','SUCCESS','FAILED') NOT NULL DEFAULT 'PENDING',
  product_id      VARCHAR(64) NULL,
  transid         VARCHAR(64) NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_po_iccid FOREIGN KEY (iccid) REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  UNIQUE KEY uk_po_transid (transid),
  INDEX idx_po_iccid_month (iccid, month),
  INDEX idx_po_status (status)
) ENGINE=InnoDB;

CREATE TABLE auth_token (
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

-- Seed (same as V002)
INSERT INTO app_credential(appid, password_hash, is_active)
VALUES ('demo-app', SHA2('demo-password', 256), 1)
ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), is_active=VALUES(is_active);

INSERT INTO sim_card(iccid, msisdn, status, activated_at)
VALUES
  ('8986001200000000001', '13900000001', 'ACTIVE',      '2025-07-15 10:00:00'),
  ('8986001200000000002', '13900000002', 'SUSPENDED',   '2025-06-01 09:00:00'),
  ('8986001200000000003', '13900000003', 'DEACTIVATED', '2024-12-20 11:30:00')
ON DUPLICATE KEY UPDATE msisdn=VALUES(msisdn), status=VALUES(status), activated_at=VALUES(activated_at);

INSERT INTO usage_monthly(iccid, month, plan_total_mb, used_mb, updated_at)
VALUES
  ('8986001200000000001', '2025-08', 10240, 3842.60, NOW()),
  ('8986001200000000002', '2025-08',  2048,  512.00, NOW())
ON DUPLICATE KEY UPDATE plan_total_mb=VALUES(plan_total_mb), used_mb=VALUES(used_mb), updated_at=VALUES(updated_at);

INSERT INTO purchase_order(order_id, iccid, month, package_mb, price_cent, status, product_id, transid, created_at)
VALUES
  ('PO202508210001', '8986001200000000001', '2025-08', 1024, 1200, 'SUCCESS', 'pkg_1g', '20250821T093012-0001', NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status);

INSERT INTO purchase_order(order_id, iccid, month, package_mb, price_cent, status, product_id, transid, created_at)
VALUES
  ('PO202508210002', '8986001200000000002', '2025-08',  512,  700, 'PENDING', 'pkg_512m','20250821T093512-0002', NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status);

SET @token := SUBSTRING(SHA2(CONCAT(UUID(), RAND()), 256), 1, 64);
INSERT INTO auth_token(token, appid, expires_at)
VALUES (@token, 'demo-app', DATE_ADD(NOW(), INTERVAL 1 HOUR))
ON DUPLICATE KEY UPDATE expires_at=VALUES(expires_at);

SELECT @token AS issued_demo_token, DATE_ADD(NOW(), INTERVAL 1 HOUR) AS expires_at;
SELECT * FROM v_usage_effective WHERE iccid='8986001200000000001' AND month='2025-08';
SELECT iccid, status FROM sim_card ORDER BY iccid;
SELECT order_id, iccid, month, package_mb, status, transid FROM purchase_order ORDER BY created_at DESC;