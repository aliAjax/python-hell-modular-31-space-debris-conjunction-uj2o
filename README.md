# 太空碎片接近预警与规避协调

模块化纯 Python 3.9.6+ 标准库项目，默认端口 `8331`。

## 模块

- `app.py`：参数解析、依赖组装和 HTTP 生命周期。
- `src/domain.py`：领域类型、校验和错误定义。
- `src/rules.py`：风险评估、意见冲突和状态机。
- `src/repository.py`：SQLite、事务、乐观版本和审计链。
- `src/service.py`：身份、权限、用例编排。
- `src/http_api.py`：JSON API 和静态首页。
- `src/audit.py`：哈希审计事件。

## 运行

```bash
python3 app.py --init --db ./data.db
python3 app.py --db ./data.db --port 8331
```

服务提供 `GET /health`、`GET /api/state`、`GET /api/items`、`POST /api/items`、`POST /api/items/<id>/sources` 和 `POST /api/items/<id>/actions`。身份使用 `X-User-Id`、`X-Role` 请求头。浏览器访问根路径即为协调台页面（创建事件、评估、轨道修订、运营方意见、批准/执行/解决、来源台账与审计链）。

## 协调规则

- **轨道修订按观测时间推进**：`report_revision` 只有在 `observed_at` 严格晚于当前最新观测时才会更新最近距离、协方差和风险评估；晚到的旧数据（含同时刻数据）仍完整保留在修订/来源台账中，但标记为 `applied=false`，不覆盖当前值。创建事件时的初始轨道数据在首次修订前一直有效。
- **批准失效**：事件处于 `coordinating`（已批准）时收到更晚的修订，原 `approved_maneuver` 被移除、本轮运营方意见清空，状态退回 `assessed`（待复核）；`payload.approval_invalidated` 记录失效原因，重新批准需要运营方再次全员同意。
- **意见以最新为准**：同一运营方多次表态时只保留其最新意见（完整历史仍可在审计链中追溯）；冲突状态按各运营方的最新意见实时计算。
- **全员一致才可批准**：协调员只有在 `operating_organizations` 中所有运营方最新意见均为 `approve` 且无冲突、燃料不超预算时才能批准。运营方只能对自己参与的事件表态。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖评估、批准、执行、解决、重复告警、权限、版本冲突、过期轨道、运营方意见冲突、全员一致批准、同运营方意见覆盖、晚到旧修订留档和批准后修订失效退回待复核。数据使用 SQLite 持久化；规则是可运行的演示模型，不替代真实轨道力学、碰撞概率和空间交通协调服务。
