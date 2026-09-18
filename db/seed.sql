-- Non-overwriting synthetic usage seed for the current API schema.
-- Run from the repository root after db/schema.sql. Safe to repeat: existing
-- sim_usage rows (including purchases and changed usage) are never updated.
USE iot_sim_ops;

INSERT IGNORE INTO sim_usage (iccid, month, used_mb, package_mb)
VALUES
  ('8986001200000000001', '2025-08', 200, 1024),
  ('8986001200000000002', '2025-08', 50, 512),
  ('8986001200000000003', '2025-08', 300, 2048);
