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

服务提供 `GET /health`、`GET /api/state`、`GET /api/items`、`POST /api/items`、`POST /api/items/<id>/sources` 和 `POST /api/items/<id>/actions`。身份使用 `X-User-Id`、`X-Role` 请求头。首页 `/` 是协调台界面，可创建事件、提交轨道修订、登记来源、记录运营方意见以及批准/执行/解决动作。

## 协调规则

- 轨道修订（`report_revision`）只按**观测时间**推进：`observed_at` 晚于当前最新观测才覆盖当前距离、协方差并重算风险；晚到的旧观测仍写入修订记录（`applied=false`、`note=late_observation`）和来源表，但不覆盖当前距离与风险；同一观测时间拒绝重复修订。
- 已批准（`coordinating`/`executing`）后到达**更晚**的修订会使原机动批准失效，事件退回 `assessed`（待复核），运营方意见清空，必须重新协调。
- 运营方意见（`record_opinion`）按运营方归并，同一运营方再次表态以**最新意见**为准（历史保留，新条目记录 `supersedes`）；只有全部参与运营方最新意见均为 `approve` 且无冲突时，协调员才能批准。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖评估、批准、执行、解决、重复告警、权限、版本冲突、过期轨道、运营方意见冲突、晚到修订留痕和批准失效重核。数据使用 SQLite 持久化；规则是可运行的演示模型，不替代真实轨道力学、碰撞概率和空间交通协调服务。
