-- V008__add_imsi_to_sim_card.sql
-- 目的：为 sim_card 增加 imsi 字段，供 /sims/search 使用

USE iot_sim_ops;

ALTER TABLE sim_card
  ADD COLUMN IF NOT EXISTS imsi VARCHAR(20) NULL AFTER msisdn;

-- 为 imsi 建唯一索引（若已存在则跳过）
ALTER TABLE sim_card
  ADD UNIQUE INDEX IF NOT EXISTS uk_sim_imsi (imsi);
