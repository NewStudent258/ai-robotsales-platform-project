# AI Robot Sales Platform

基于 Python、FastAPI 和 MySQL 的 AI 机器人售卖平台。当前版本包含产品首页、产品目录、样例选型助手，以及从产品配置、报价确认到创建订单的客户流程。

## 本地启动

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

2. 启动 MySQL 和 Redis：

```powershell
docker compose up -d mysql redis
```

3. 复制 `.env.example` 为 `.env`，按本机 MySQL 账号修改 `DATABASE_URL`（示例凭据仅供 Docker 开发环境使用），执行数据库迁移和种子数据：

```powershell
alembic upgrade head
python scripts/seed_products.py
```

4. 启动 API：

```powershell
uvicorn app.main:app --reload
```

接口文档地址：`http://127.0.0.1:8000/docs`。
产品首页地址：`http://127.0.0.1:8000/`。

## 测试

```powershell
pytest
```

测试使用 SQLite 内存库，生产和开发环境使用 MySQL。金额由服务端按 `base-price-v1` 计算，当前税额固定为 0；正式税费、折扣和支付尚未接入。报价返回的 `access_token` 是访问该报价与订单的凭证，不应放进 URL 或日志。产品写入 API 仅在 `DEBUG=true` 的开发模式开放，正式运营后台鉴权仍待实现。选型助手目前是 Mock Provider，推荐仅用于演示，不应当作正式采购依据。
