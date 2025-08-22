-- iot_sim_ops_reset.sql
-- 统一到 user_account + sim_card(含 imsi/owner) + sim_usage/sim_purchase + auth_token(user_id) 这一条线
-- 运行方式：mysql -uroot -p < db/iot_sim_ops_reset.sql

-- 1) 建库
CREATE DATABASE IF NOT EXISTS iot_sim_ops
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE iot_sim_ops;

-- 2) 清理旧对象（兼容早期表/视图名）
SET FOREIGN_KEY_CHECKS = 0;
DROP VIEW IF EXISTS v_usage_effective;

DROP TABLE IF EXISTS auth_token;
DROP TABLE IF EXISTS sim_op_log;
DROP TABLE IF EXISTS sim_purchase;
DROP TABLE IF EXISTS sim_usage;
DROP TABLE IF EXISTS sim_card;
DROP TABLE IF EXISTS user_account;

-- 早期遗留（若存在则一并清理）
DROP TABLE IF EXISTS purchase_order;
DROP TABLE IF EXISTS usage_monthly;
DROP TABLE IF EXISTS app_credential;
SET FOREIGN_KEY_CHECKS = 1;

-- 3) 创建新表

-- 3.1 用户
CREATE TABLE IF NOT EXISTS user_account (
  user_id       BIGINT PRIMARY KEY AUTO_INCREMENT,
  username      VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(100) NOT NULL,
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3.2 物联卡（包含 imsi、归属、状态）
CREATE TABLE IF NOT EXISTS sim_card (
  iccid         VARCHAR(32)  NOT NULL,
  msisdn        VARCHAR(16)  NULL,
  imsi          VARCHAR(20)  NULL,
  status        ENUM('ACTIVE','SUSPENDED','TERMINATED') NOT NULL DEFAULT 'ACTIVE',
  owner_user_id BIGINT NULL,
  activated_at  DATETIME NULL,
  deactivated_at DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (iccid),
  UNIQUE KEY uk_sim_msisdn (msisdn),
  UNIQUE KEY uk_sim_imsi (imsi),
  CONSTRAINT fk_sim_owner FOREIGN KEY (owner_user_id)
    REFERENCES user_account(user_id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3.3 当月用量（unique: iccid+month）
CREATE TABLE IF NOT EXISTS sim_usage (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  iccid        VARCHAR(32)     NOT NULL,
  month        CHAR(7)         NOT NULL,                           -- YYYY-MM
  used_mb      INT             NOT NULL DEFAULT 0,
  package_mb   INT             NOT NULL DEFAULT 0,                 -- 基础套餐 + 叠加包汇总
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_iccid_month (iccid, month),
  CONSTRAINT fk_usage_sim FOREIGN KEY (iccid)
    REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3.4 订购记录（含 product_id / price_cent）
CREATE TABLE IF NOT EXISTS sim_purchase (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id     VARCHAR(32)     NOT NULL,
  iccid        VARCHAR(32)     NOT NULL,
  month        CHAR(7)         NOT NULL,
  package_mb   INT             NOT NULL DEFAULT 0,
  product_id   VARCHAR(64)     NULL,
  price_cent   INT             NULL,
  status       ENUM('PENDING','SUCCESS','FAILED','CANCELLED') NOT NULL DEFAULT 'SUCCESS',
  transid      VARCHAR(64)     NOT NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_id (order_id),
  UNIQUE KEY uk_transid (transid),
  KEY idx_iccid_month (iccid, month),
  CONSTRAINT fk_purchase_sim FOREIGN KEY (iccid)
    REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3.5 操作日志（可选）
CREATE TABLE IF NOT EXISTS sim_op_log (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  iccid      VARCHAR(32)     NOT NULL,
  op_type    VARCHAR(32)     NOT NULL,
  op_result  VARCHAR(32)     NOT NULL,
  operator   VARCHAR(64)     NOT NULL,
  created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_iccid_created (iccid, created_at),
  CONSTRAINT fk_oplog_sim FOREIGN KEY (iccid)
    REFERENCES sim_card(iccid)
    ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3.6 Token（绑定用户；appid 保留为兼容但允许 NULL）
CREATE TABLE IF NOT EXISTS auth_token (
  token       CHAR(64)   PRIMARY KEY,
  user_id     BIGINT     NOT NULL,
  appid       VARCHAR(64) NULL,
  expires_at  DATETIME   NOT NULL,
  created_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_token_user (user_id),
  CONSTRAINT fk_token_user FOREIGN KEY (user_id)
    REFERENCES user_account(user_id)
    ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4) 种子数据（演示帐号与样例卡/用量/订单）
INSERT INTO user_account(username, password_hash, is_active)
VALUES
  -- alice: user1pass
  ('alice', '$2b$12$NlbpTdDhbZ6PwFrFRiy3eu/VxtRUtduZPMGhWFjuzLQ2QZXr.LNbe', 1),
  -- bob:   user2pass
  ('bob',   '$2b$12$bu9TWIA46FTF/cdkZ73N2e6SrITwwF80vX/bdlz2yC21wl4Ll8puS', 1)
ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), is_active=VALUES(is_active);

-- 样例卡（三张）
INSERT INTO sim_card(iccid, msisdn, imsi, status, owner_user_id, activated_at, created_at)
VALUES
  ('8986001200000000001', '147650000001', '460000000000001', 'ACTIVE',
     (SELECT user_id FROM user_account WHERE username='alice'), NOW(), NOW()),
  ('8986001200000000002', '147650000002', '460000000000002', 'ACTIVE',
     (SELECT user_id FROM user_account WHERE username='alice'), NOW(), NOW()),
  ('8986001200000000003', '147650000003', '460000000000003', 'SUSPENDED',
     (SELECT user_id FROM user_account WHERE username='bob'),   NOW(), NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status), owner_user_id=VALUES(owner_user_id);

-- 用量（以 2025-08 为例；如需当月，可改成当前月份）
INSERT INTO sim_usage(iccid, month, used_mb, package_mb)
VALUES
  ('8986001200000000001', '2025-08', 200, 1024),
  ('8986001200000000002', '2025-08',  50,  512),
  ('8986001200000000003', '2025-08', 300, 2048)
ON DUPLICATE KEY UPDATE used_mb=VALUES(used_mb), package_mb=VALUES(package_mb);

-- 订单示例（SUCCESS/PENDING）
INSERT INTO sim_purchase(order_id, iccid, month, package_mb, product_id, price_cent, status, transid, created_at)
VALUES
  ('PO202508210001', '8986001200000000001', '2025-08', 1024, 'pkg_1g',   1200, 'SUCCESS', '20250821T093012-0001', NOW()),
  ('PO202508210002', '8986001200000000002', '2025-08',  512, 'pkg_512m',  700, 'PENDING', '20250821T093512-0002', NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status);

-- 5) 快速验收查询
SELECT iccid, imsi, msisdn, status FROM sim_card ORDER BY iccid;
SELECT iccid, month, used_mb, package_mb FROM sim_usage ORDER BY iccid, month;
SELECT order_id, iccid, month, package_mb, product_id, price_cent, status FROM sim_purchase ORDER BY created_at DESC;
