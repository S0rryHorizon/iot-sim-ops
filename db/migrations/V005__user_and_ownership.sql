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

-- 卡归属与用户 token 迁移。用 INFORMATION_SCHEMA 检查保证可重复执行。
DELIMITER $$
DROP PROCEDURE IF EXISTS migrate_v005_user_and_ownership $$
CREATE PROCEDURE migrate_v005_user_and_ownership()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sim_card'
      AND COLUMN_NAME = 'owner_user_id'
  ) THEN
    ALTER TABLE sim_card
      ADD COLUMN owner_user_id BIGINT NULL AFTER status;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sim_card'
      AND INDEX_NAME = 'idx_sim_owner'
  ) THEN
    ALTER TABLE sim_card ADD INDEX idx_sim_owner (owner_user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'sim_card'
      AND CONSTRAINT_NAME = 'fk_sim_owner'
  ) THEN
    ALTER TABLE sim_card
      ADD CONSTRAINT fk_sim_owner
      FOREIGN KEY (owner_user_id) REFERENCES user_account(user_id)
      ON UPDATE CASCADE ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'auth_token'
      AND COLUMN_NAME = 'user_id'
  ) THEN
    ALTER TABLE auth_token
      ADD COLUMN user_id BIGINT NULL AFTER token;
  END IF;

  ALTER TABLE auth_token MODIFY appid VARCHAR(64) NULL;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'auth_token'
      AND INDEX_NAME = 'idx_token_user'
  ) THEN
    ALTER TABLE auth_token ADD INDEX idx_token_user (user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'auth_token'
      AND CONSTRAINT_NAME = 'fk_token_user'
  ) THEN
    ALTER TABLE auth_token
      ADD CONSTRAINT fk_token_user
      FOREIGN KEY (user_id) REFERENCES user_account(user_id)
      ON UPDATE CASCADE ON DELETE CASCADE;
  END IF;
END $$
CALL migrate_v005_user_and_ownership() $$
DROP PROCEDURE IF EXISTS migrate_v005_user_and_ownership $$
DELIMITER ;
