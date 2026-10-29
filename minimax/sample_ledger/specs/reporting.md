# 账本月报规格

本规格描述 `ledger report` 的月度汇总行为。金额使用十进制定点数，月份格式为 `YYYY-MM`。

## REQ-REPORT-001 按分类汇总正数支出

月报 MUST 按 `category` 对当月支出分组，`categories` 的值为该分类正数支出之和。`total` 为所有入账支出之和。

## REQ-REPORT-002 只统计指定月份

月报 MUST 只统计 `occurred_on` 的 `YYYY-MM` 等于请求月份的流水，其他月份不得进入任何合计。

## REQ-REPORT-003 分类合计稳定排序

`categories` MUST 按分类名升序输出，保证 JSON 结果稳定，便于自动化比对。

## REQ-REPORT-004 退款与支出分离

金额为负数的流水是退款。退款 MUST NOT 进入 `categories` 或 `total`；月报 MUST 输出 `refund_total`（当月退款绝对值之和）和 `net_total`（`total - refund_total`）。金额为零的流水忽略。

## REQ-REPORT-005 按最低金额过滤支出

当传入 `min_amount` 时，只有金额大于等于 `min_amount` 的正数支出进入 `categories` 和 `total`；该阈值包含边界。退款不受 `min_amount` 影响，仍计入 `refund_total`。

## 兼容性约束

现有 CSV 解析、月份过滤、正数支出分类汇总和分类排序行为 MUST 保持不变。不得修改 `tests/` 中的断言或预期值。
