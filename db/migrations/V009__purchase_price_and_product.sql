-- V009__purchase_price_and_product.sql
-- 目的：为 sim_purchase 增加 product_id / price_cent（若已存在则跳过）
USE iot_sim_ops;

DELIMITER $$
DROP PROCEDURE IF EXISTS add_purchase_price_and_product $$
CREATE PROCEDURE add_purchase_price_and_product()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'sim_purchase'
       AND COLUMN_NAME = 'product_id'
  ) THEN
    ALTER TABLE sim_purchase
      ADD COLUMN product_id VARCHAR(64) NULL AFTER package_mb;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'sim_purchase'
       AND COLUMN_NAME = 'price_cent'
  ) THEN
    ALTER TABLE sim_purchase
      ADD COLUMN price_cent INT NULL AFTER product_id;
  END IF;
END $$
CALL add_purchase_price_and_product() $$
DROP PROCEDURE IF EXISTS add_purchase_price_and_product $$
DELIMITER ;

