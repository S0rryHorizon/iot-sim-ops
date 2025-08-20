-- db/bulk_seed_10k.sql
-- 一次性插入 10,000 张卡，并为当前月份写入用量；记录操作日志
-- 依赖：已执行 schema.sql（已存在 offering/sim_card/... 表）
-- 注意：本脚本可多次执行；如号码已存在则跳过（依靠 UNIQUE 约束）

USE iot_sim_ops;

SET SESSION cte_max_recursion_depth = 20000;  /* raise recursion depth for 10k */

SET @N := 10000;                 /* 造数数量 */
SET @SEQ_START := 10006;         /* 序号起点（避开 00001~00005）*/
SET @SEQ_END := @SEQ_START + @N - 1;
SET @MONTH := DATE_FORMAT(CURDATE(), '%Y-%m');

/* 记录插入前的最大ID，后面只对本次新增记录写用量/日志 */
SELECT @ID_BEFORE := COALESCE(MAX(id), 0) FROM sim_card;

WITH RECURSIVE seq(n) AS (
  SELECT @SEQ_START
  UNION ALL
  SELECT n + 1 FROM seq WHERE n < @SEQ_END
)
INSERT INTO sim_card (
  iccid, msisdn, imsi, status, throttle_kbps, is_shared_pool,
  offering_ref, owner, activated_at, terminated_at
)
SELECT
  /* 号码规则：确保长度与唯一性 */
  LPAD(CONCAT('898600', n), 20, '0') AS iccid,
  LPAD(CONCAT('14765',  n), 13, '0') AS msisdn,
  LPAD(CONCAT('46007',  n), 15, '0') AS imsi,
  /* 状态分布：ACTIVE(85%) / SUSPENDED(8%) / THROTTLED(5%) / TERMINATED(2%) */
  CASE
    WHEN (n % 100) < 85 THEN 1
    WHEN (n % 100) < 93 THEN 2
    WHEN (n % 100) < 98 THEN 3
    ELSE 4
  END AS status,
  /* 限流值（仅 THROTTLED 生效）*/
  CASE WHEN (n % 100) BETWEEN 93 AND 97 THEN 128 ELSE NULL END AS throttle_kbps,
  /* 共享池约 3% */
  ((n % 33) = 0) AS is_shared_pool,
  /* 套餐分布：8元(70%) / 15元(30%) */
  CASE WHEN (n % 10) < 7
       THEN (SELECT id FROM offering WHERE offering_id='21000032' LIMIT 1)
       ELSE (SELECT id FROM offering WHERE offering_id='21000064' LIMIT 1)
  END AS offering_ref,
  /* owner 简单分配 */
  CASE n % 5 WHEN 0 THEN 'alice' WHEN 1 THEN 'bob' WHEN 2 THEN 'carol' WHEN 3 THEN 'diana' ELSE 'ed' END AS owner,
  /* 激活/注销时间 */
  CASE
    WHEN (CASE WHEN (n % 100) < 85 THEN 1 WHEN (n % 100) < 93 THEN 2 WHEN (n % 100) < 98 THEN 3 ELSE 4 END) IN (1,2,3)
    THEN NOW() ELSE NULL
  END AS activated_at,
  CASE
    WHEN (CASE WHEN (n % 100) < 85 THEN 1 WHEN (n % 100) < 93 THEN 2 WHEN (n % 100) < 98 THEN 3 ELSE 4 END) = 4
    THEN NOW() ELSE NULL
  END AS terminated_at
FROM seq
/* 避免重复插入（UNIQUE 约束已能防重，这里再做一次显式过滤更干净）*/
WHERE NOT EXISTS (
  SELECT 1 FROM sim_card s
  WHERE s.iccid = LPAD(CONCAT('898600', n), 20, '0')
     OR s.msisdn = LPAD(CONCAT('14765',  n), 13, '0')
     OR s.imsi  = LPAD(CONCAT('46007',  n), 15, '0')
);

SELECT @ID_AFTER := COALESCE(MAX(id), 0) FROM sim_card;

/* 为本次新增的卡写当月用量（0~80% 套餐随机使用量）*/
INSERT INTO usage_monthly (sim_id, month, use_kb, updated_at)
SELECT s.id, @MONTH,
       FLOOR(RAND(s.id) * 0.8 * o.total_kb) AS use_kb,
       NOW()
FROM sim_card s
JOIN offering o ON s.offering_ref = o.id
LEFT JOIN usage_monthly um ON um.sim_id = s.id AND um.month = @MONTH
WHERE s.id > @ID_BEFORE AND s.id <= @ID_AFTER
  AND um.id IS NULL;  /* 若已存在当月记录则跳过 */

/* 写操作日志（CREATE）*/
INSERT INTO sim_op_log (sim_id, op_type, op_detail, operator, op_time)
SELECT s.id, 'CREATE',
       JSON_OBJECT('source', 'bulk_seed_10k.sql'),
       'system', NOW()
FROM sim_card s
WHERE s.id > @ID_BEFORE AND s.id <= @ID_AFTER;

/* 便捷验证：本次新增数量 */
SELECT '新增卡数量' AS k, (@ID_AFTER - @ID_BEFORE) AS v;
