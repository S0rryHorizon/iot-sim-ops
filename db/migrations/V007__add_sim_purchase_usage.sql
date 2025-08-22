-- 订单：sim_purchase
CREATE TABLE IF NOT EXISTS sim_purchase (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id     VARCHAR(32)     NOT NULL,
  iccid        VARCHAR(32)     NOT NULL,
  month        CHAR(7)         NOT NULL,
  package_mb   INT             NOT NULL DEFAULT 0,
  status       ENUM('PENDING','SUCCESS','FAILED','CANCELLED') NOT NULL DEFAULT 'SUCCESS',
  transid      VARCHAR(64)     NOT NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_id(order_id),
  UNIQUE KEY uk_transid(transid),
  KEY idx_iccid_month (iccid, month),
  CONSTRAINT fk_purchase_sim FOREIGN KEY (iccid)
      REFERENCES sim_card(iccid)
      ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用量：sim_usage
CREATE TABLE IF NOT EXISTS sim_usage (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  iccid        VARCHAR(32)     NOT NULL,
  month        CHAR(7)         NOT NULL,
  used_mb      INT             NOT NULL DEFAULT 0,
  package_mb   INT             NOT NULL DEFAULT 0,
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_iccid_month (iccid, month),
  CONSTRAINT fk_usage_sim FOREIGN KEY (iccid)
      REFERENCES sim_card(iccid)
      ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 操作日志：sim_op_log（可选；缺表也不会影响接口）
CREATE TABLE IF NOT EXISTS sim_op_log (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  iccid      VARCHAR(32)     NOT NULL,
  op_type    VARCHAR(32)     NOT NULL,
  op_result  VARCHAR(32)     NOT NULL,
  operator   VARCHAR(64)     NOT NULL,
  created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_iccid_created (iccid, created_at),
  CONSTRAINT fk_oplog_sim FOREIGN KEY (iccid)
      REFERENCES sim_card(iccid)
      ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
