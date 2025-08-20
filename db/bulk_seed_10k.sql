-- db/bulk_seed_10k.sql

-- 号码规则简单可控，全部唯一。
-- 状态、套餐、共享池做了可复现的伪随机分布（依赖 n 的取模）。
-- 用 @ID_BEFORE/@ID_AFTER 只对“本次新增的卡”写入 usage_monthly 与操作日志。
-- 目标：一次性插入 10,000 张卡，并为当前月份写入用量；记录操作日志
-- 依赖：已执行 schema.sql（已存在 offering/sim_card/... 表）
-- 注意：本脚本可多次执行；如号码已存在则跳过（依靠 UNIQUE 约束）

USE iot_sim_ops;

SET SESSION cte_max_recursion_depth = 20000;          --修改默认递归深度

SET @N := 10000;                 -- 造数数量
SET @SEQ_START := 10006;         -- 序号起点（避开 00001~00005）
SET @SEQ_END := @SEQ_START + @N - 1;
SET @MONTH := DATE_FORMAT(CURDATE(), '%Y-%m');

-- 记录插入前的最大ID，后面只对本次新增记录写用量/日志
SELECT @ID_BEFORE := COALESCE(MAX(id), 0) FROM sim_card;

WITH RECURSIVE seq(n) AS (
  SELECT @SEQ_START
  UNION ALL
  SELECT n + 1 FROM seq WHERE n < @SEQ_END
)
-- 插入 sim_card（若唯一键冲突即跳过）
INSERT INTO sim_card (iccid, msisdn, imsi, status, throttle_kbps, is_shared_pool, offering_ref, owner, activated_at, terminated_at)
SELECT
  -- 号码规则（你可以按需改前缀），确保长度与唯一性
  LPAD(CONCAT('898600', n), 20, '0')                                       AS iccid,         -- 20位
  LPAD(CONCAT('14765', n), 13, '0')                                        AS msisdn,        -- 13位
  LPAD(CONCAT('46007', n), 15, '0')                                        AS imsi,          -- 15位
  -- 状态分布：ACTIVE(85%) / SUSPENDED(8%) / THROTTLED(5%) / TERMINATED(2%)
  CASE
    WHEN (n % 100) < 85 THEN 1
    WHEN (n % 100) < 93 THEN 2
    WHEN (n % 100) < 98 THEN 3
    ELSE 4
  END AS status,
  -- 限流值（仅 THROTTLED 生效）
  CASE WHEN (n % 100) BETWEEN 93 AND 97 THEN 128 ELSE NULL END AS throttle_kbps,
  -- 共享池占比约 3%
  ((n % 33) = 0) AS is_shared_pool,
  -- 套餐分布：8元(70%) / 15元(30%)
  CASE WHEN (n % 10) < 7
       THEN (SELECT id FROM offering WHERE offering_id='21000032' LIMIT 1)
       ELSE (SELECT id FROM offering WHERE offering_id='21000064' LIMIT 1)
  END AS offering_ref,
  -- owner 简单分配
  CASE n % 5 WHEN 0 THEN 'alice' WHEN 1 THEN 'bob' WHEN 2 THEN 'carol' WHEN 3 THEN 'diana' ELSE 'ed' END AS owner,
  -- 激活/注销时间
  CASE
    WHEN (/* ACTIVE / SUSPENDED / THROTTLED */ (CASE WHEN (n % 100) < 85 THEN 1 WHEN (n % 100) < 93 THEN 2 WHEN (n % 100) < 98 THEN 3 ELSE 4 END)) IN (1,2,3)
    THEN NOW()
    ELSE NULL
  END AS activated_at,
  CASE
    WHEN (/* TERMINATED */ (CASE WHEN (n % 100) < 85 THEN 1 WHEN (n % 100) < 93 THEN 2 WHEN (n % 100) < 98 THEN 3 ELSE 4 END)) = 4
    THEN NOW()
    ELSE NULL
  END AS terminated_at
FROM seq
-- 避免重复插入（UNIQUE 约束已能防重，这里再做一次显式过滤更干净）
WHERE NOT EXISTS (
  SELECT 1 FROM sim_card s
  WHERE s.iccid = LPAD(CONCAT('898600', n), 20, '0')
     OR s.msisdn = LPAD(CONCAT('14765', n), 13, '0')
     OR s.imsi  = LPAD(CONCAT('46007', n), 15, '0')
);

-- 记录插入后的最大ID
SELECT @ID_AFTER := COALESCE(MAX(id), 0) FROM sim_card;

-- 为本次新增的卡写当月用量（0~80% 套餐随机使用量）
INSERT INTO usage_monthly (sim_id, month, use_kb, updated_at)
SELECT s.id, @MONTH,
       -- 根据套餐总量随机：0 ~ 0.8 * total_kb
       FLOOR(RAND(s.id) * 0.8 * o.total_kb) AS use_kb,
       NOW()
FROM sim_card s
JOIN offering o ON s.offering_ref = o.id
LEFT JOIN usage_monthly um ON um.sim_id = s.id AND um.month = @MONTH
WHERE s.id > @ID_BEFORE AND s.id <= @ID_AFTER
  AND um.id IS NULL;  -- 若已存在当月记录则跳过

-- 写操作日志（CREATE）
INSERT INTO sim_op_log (sim_id, op_type, op_detail, operator, op_time)
SELECT s.id, 'CREATE',
       JSON_OBJECT('source', 'bulk_seed_10k.sql'),
       'system', NOW()
FROM sim_card s
WHERE s.id > @ID_BEFORE AND s.id <= @ID_AFTER;

-- 便捷验证：本次新增数量、示例查询
SELECT '新增卡数量' AS k, (@ID_AFTER - @ID_BEFORE) AS v;
-- 示例：按 iccid 查本月套餐余量（与接口返回一致）
-- SELECT o.offering_id, o.name AS offeringName, o.total_kb AS totalAmount,
--        um.use_kb AS useAmount, (o.total_kb - um.use_kb) AS remainAmount
-- FROM sim_card s
-- JOIN offering o ON s.offering_ref=o.id
-- LEFT JOIN usage_monthly um ON um.sim_id=s.id AND um.month=@MONTH
-- WHERE s.iccid = '898600000000010006';