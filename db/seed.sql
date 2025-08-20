-- =========================================================
-- 插入 2 个套餐、5 张示例卡（涵盖 ACTIVE/SUSPENDED/THROTTLED/TERMINATED/共享池）
-- 并写入当月用量与若干操作日志
-- =========================================================


-- db/seed.sql
USE iot_sim_ops;

-- 套餐
INSERT INTO offering (offering_id, name, total_kb) VALUES
('21000032', '全国通用流量8元套餐', 102400),
('21000064', '全国通用流量15元套餐', 204800)
ON DUPLICATE KEY UPDATE name=VALUES(name), total_kb=VALUES(total_kb);

-- 示例卡（5张，覆盖不同状态/场景）
INSERT INTO sim_card (
  iccid, msisdn, imsi, status, throttle_kbps, is_shared_pool,
  offering_ref, owner, activated_at, terminated_at
)
SELECT
  '89860000000000000001' AS iccid,
  '14765004176' AS msisdn,
  '460079650004176' AS imsi,
  1 AS status,
  NULL AS throttle_kbps,
  FALSE AS is_shared_pool,
  (SELECT id FROM offering WHERE offering_id='21000032') AS offering_ref,
  'alice' AS owner,
  NOW() AS activated_at,
  NULL AS terminated_at
UNION ALL
SELECT '89860000000000000002','14765004177','460079650004177', 2, NULL, FALSE,(SELECT id FROM offering WHERE offering_id='21000032'),'bob',   NOW(), NULL
UNION ALL
SELECT '89860000000000000003','14765004178','460079650004178', 3, 128,  FALSE,(SELECT id FROM offering WHERE offering_id='21000064'),'carol', NOW(), NULL
UNION ALL
SELECT '89860000000000000004','14765004179','460079650004179', 4, NULL, FALSE,(SELECT id FROM offering WHERE offering_id='21000032'),'diana', NOW(), NOW()
UNION ALL
SELECT '89860000000000000005','14765004180','460079650004180', 1, NULL, TRUE, (SELECT id FROM offering WHERE offering_id='21000064'),'ed',    NOW(), NULL
ON DUPLICATE KEY UPDATE
  status=VALUES(status),
  throttle_kbps=VALUES(throttle_kbps),
  is_shared_pool=VALUES(is_shared_pool),
  offering_ref=VALUES(offering_ref),
  owner=VALUES(owner),
  activated_at=VALUES(activated_at),
  terminated_at=VALUES(terminated_at);


-- 当月用量（0~70%）
INSERT INTO usage_monthly (sim_id, month, use_kb, updated_at)
SELECT id, DATE_FORMAT(CURDATE(), '%Y-%m'),
       CASE id % 5
         WHEN 1 THEN 15186
         WHEN 2 THEN 512
         WHEN 3 THEN 16384
         WHEN 4 THEN 8192
         ELSE 70000
       END,
       NOW()
FROM sim_card
WHERE iccid IN ('89860000000000000001','89860000000000000002','89860000000000000003','89860000000000000004','89860000000000000005')
ON DUPLICATE KEY UPDATE use_kb=VALUES(use_kb), updated_at=VALUES(updated_at);

-- 操作日志样例
INSERT INTO sim_op_log (sim_id, op_type, op_detail, operator, op_time)
SELECT id, 'CREATE', JSON_OBJECT('source','seed'), 'system', NOW()
FROM sim_card
WHERE iccid IN ('89860000000000000001','89860000000000000002','89860000000000000003','89860000000000000004','89860000000000000005');

-- 示例查询（供开发自测）
-- 依据 iccid 查“本月套餐用量汇总”，与接口返回字段对应
-- SELECT o.offering_id, o.name AS offeringName, o.total_kb AS totalAmount,
--        um.use_kb AS useAmount, (o.total_kb - um.use_kb) AS remainAmount
-- FROM sim_card s
-- JOIN offering o ON s.offering_ref=o.id
-- LEFT JOIN usage_monthly um ON um.sim_id=s.id AND um.month=DATE_FORMAT(CURDATE(), '%Y-%m')
-- WHERE s.iccid='89860000000000000001';
