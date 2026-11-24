# 002 财务日账与锁定批次

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

## 新增字段

- `datasets.kind`：数据批次类型，`checkout`（随机结账台账，默认）或 `finance`（财务退款日账）。
- `runs.finance_dataset_id`：运行发起时锁定的财务日账批次，可空；为空时仅按有单号退款计算，不产生匿名退款区间。
- `schedules.finance_dataset_id`：定时计划锁定的财务日账批次，可空。

## 对已有 PostgreSQL 的显式迁移

```sql
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'checkout';
CREATE INDEX IF NOT EXISTS ix_datasets_kind ON datasets (kind);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS finance_dataset_id INTEGER NULL REFERENCES datasets (id);
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS finance_dataset_id INTEGER NULL REFERENCES datasets (id);
```

启动时 `Base.metadata.create_all` 只补建新表，不会给已存在的表补列；升级旧卷必须先执行上述语句。
新环境直接由模型建表，应用启动流程也会幂等执行同等的补列语句。

## 口径

- 财务日账每个财务日包含一条匿名汇总行（`reader_id` 为空，`refund_count` 为当日退款总笔数）和若干带单号行。
- 运行在发起时把结账台账与财务日账两个不可变批次一并锁定，记录各自 SHA-256 校验指纹。
- 并发补报只生成新批次；原运行（含复盘重试）始终读取原锁定批次，复算须新发起运行。
