-- V002__seed_demo.sql
-- Seed demo data for iot_sim_ops (non-destructive inserts).

USE iot_sim_ops;

-- 1) App credential (demo account). In production, store bcrypt hash instead of SHA2.
INSERT INTO app_credential(appid, password_hash, is_active)
VALUES ('demo-app', SHA2('demo-password', 256), 1)
ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), is_active=VALUES(is_active);

-- 2) SIM cards
INSERT INTO sim_card(iccid, msisdn, status, activated_at)
VALUES
  ('8986001200000000001', '13900000001', 'ACTIVE',      '2025-07-15 10:00:00'),
  ('8986001200000000002', '13900000002', 'SUSPENDED',   '2025-06-01 09:00:00'),
  ('8986001200000000003', '13900000003', 'DEACTIVATED', '2024-12-20 11:30:00')
ON DUPLICATE KEY UPDATE msisdn=VALUES(msisdn), status=VALUES(status), activated_at=VALUES(activated_at);

-- 3) Monthly usage for 2025-08
INSERT INTO usage_monthly(iccid, month, plan_total_mb, used_mb, updated_at)
VALUES
  ('8986001200000000001', '2025-08', 10240, 3842.60, NOW()),
  ('8986001200000000002', '2025-08',  2048,  512.00, NOW())
ON DUPLICATE KEY UPDATE plan_total_mb=VALUES(plan_total_mb), used_mb=VALUES(used_mb), updated_at=VALUES(updated_at);

-- 4) Purchases
INSERT INTO purchase_order(order_id, iccid, month, package_mb, price_cent, status, product_id, transid, created_at)
VALUES
  ('PO202508210001', '8986001200000000001', '2025-08', 1024, 1200, 'SUCCESS', 'pkg_1g', '20250821T093012-0001', NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status);

INSERT INTO purchase_order(order_id, iccid, month, package_mb, price_cent, status, product_id, transid, created_at)
VALUES
  ('PO202508210002', '8986001200000000002', '2025-08',  512,  700, 'PENDING', 'pkg_512m','20250821T093512-0002', NOW())
ON DUPLICATE KEY UPDATE status=VALUES(status);

-- 5) Issue a demo opaque token (valid for 1 hour) for app 'demo-app'.
SET @token := SUBSTRING(SHA2(CONCAT(UUID(), RAND()), 256), 1, 64);
INSERT INTO auth_token(token, appid, expires_at)
VALUES (@token, 'demo-app', DATE_ADD(NOW(), INTERVAL 1 HOUR))
ON DUPLICATE KEY UPDATE expires_at=VALUES(expires_at);

-- 6) Show token and sanity checks (optional)
SELECT @token AS issued_demo_token, DATE_ADD(NOW(), INTERVAL 1 HOUR) AS expires_at;
SELECT * FROM v_usage_effective WHERE iccid='8986001200000000001' AND month='2025-08';
SELECT iccid, status FROM sim_card ORDER BY iccid;
SELECT order_id, iccid, month, package_mb, status, transid FROM purchase_order ORDER BY created_at DESC;