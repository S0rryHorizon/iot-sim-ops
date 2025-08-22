-- V009__purchase_price_and_product.sql
-- 目的：为订单表补充 product_id / price_cent 两个字段，避免“悬空参数”

USE iot_sim_ops;

ALTER TABLE sim_purchase
  ADD COLUMN IF NOT EXISTS product_id VARCHAR(64) NULL AFTER package_mb,
  ADD COLUMN IF NOT EXISTS price_cent INT NULL AFTER product_id;
