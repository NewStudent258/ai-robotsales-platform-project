# AI Robot Sales Platform

基于 Python、FastAPI 和 MySQL 的 AI 机器人售卖平台。当前版本包含 NVIDIA 风格的产品首页、产品目录、Mock Agent 选型、报价、报价确认、幂等创建订单和基础测试。

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

3. 复制 `.env.example` 为 `.env`，执行种子数据：

```powershell
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

测试使用 SQLite 内存库，生产和开发环境使用 MySQL。价格、报价和订单服务已经与 Agent 边界分离；后续接入真实 LLM 时，只替换 `app/agents/` 的 Provider 和编排逻辑，不改变确定性价格与订单服务。
