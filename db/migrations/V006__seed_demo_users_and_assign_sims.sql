-- V006__seed_demo_users_and_assign_sims.sql
-- 演示用户 + 把三张样例卡归属到用户

USE iot_sim_ops;

INSERT INTO user_account(username, password_hash, is_active)
VALUES
  ('alice', '$2b$12$NlbpTdDhbZ6PwFrFRiy3eu/VxtRUtduZPMGhWFjuzLQ2QZXr.LNbe', 1),
  ('bob',   '$2b$12$bu9TWIA46FTF/cdkZ73N2e6SrITwwF80vX/bdlz2yC21wl4Ll8puS',   1)
ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), is_active=VALUES(is_active);

-- 归属样例卡（按你的三张演示卡）
UPDATE sim_card SET owner_user_id=(SELECT user_id FROM user_account WHERE username='alice')
 WHERE iccid IN ('8986001200000000001','8986001200000000002');

UPDATE sim_card SET owner_user_id=(SELECT user_id FROM user_account WHERE username='bob')
 WHERE iccid IN ('8986001200000000003');