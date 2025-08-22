-- V008__add_imsi_to_sim_card.sql
-- 目的：为 sim_card 增加 imsi 列及唯一索引（若已存在则跳过）
USE iot_sim_ops;

DELIMITER $$
DROP PROCEDURE IF EXISTS add_imsi_to_sim_card $$
CREATE PROCEDURE add_imsi_to_sim_card()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'sim_card'
       AND COLUMN_NAME = 'imsi'
  ) THEN
    ALTER TABLE sim_card
      ADD COLUMN imsi VARCHAR(20) NULL AFTER msisdn;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'sim_card'
       AND INDEX_NAME = 'uk_sim_imsi'
  ) THEN
    ALTER TABLE sim_card
      ADD UNIQUE INDEX uk_sim_imsi (imsi);
  END IF;
END $$
CALL add_imsi_to_sim_card() $$
DROP PROCEDURE IF EXISTS add_imsi_to_sim_card $$
DELIMITER ;
