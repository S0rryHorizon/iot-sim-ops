-- V005__user_and_ownership.sql
-- 用户体系 & 卡归属 & token 绑定用户

USE iot_sim_ops;

CREATE TABLE IF NOT EXISTS user_account (
  user_id       BIGINT PRIMARY KEY AUTO_INCREMENT,
  username      VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(100) NOT NULL,
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL
) ENGINE=InnoDB;

-- 卡归属：一张卡属于一个用户（用户可拥有多张卡）
ALTER TABLE sim_card
  ADD COLUMN IF NOT EXISTS owner_user_id BIGINT NULL AFTER status,
  ADD INDEX IF NOT EXISTS idx_sim_owner (owner_user_id);

-- 外键（如果重复执行可能报已存在，首次执行即可）
ALTER TABLE sim_card
  ADD CONSTRAINT fk_sim_owner
  FOREIGN KEY (owner_user_id) REFERENCES user_account(user_id)
  ON UPDATE CASCADE ON DELETE SET NULL;

-- Token 绑定用户；appid 改为可空（兼容旧模式）
ALTER TABLE auth_token
  ADD COLUMN IF NOT EXISTS user_id BIGINT NULL AFTER token,
  MODIFY appid VARCHAR(64) NULL,
  ADD INDEX IF NOT EXISTS idx_token_user (user_id);

ALTER TABLE auth_token
  ADD CONSTRAINT fk_token_user
  FOREIGN KEY (user_id) REFERENCES user_account(user_id)
  ON UPDATE CASCADE ON DELETE CASCADE;