# Financial Audit Agent 📊

基于 FastAPI 构建的轻量级异步财务对账 Web 服务。本服务将复杂的对账 Pipeline 包装为异步任务队列，提供高效的提交、查询与下载接口。

## 💡 核心设计 (Core Design)

- **异步提交 (Fire-and-forget)**：针对耗时较长的 pipeline 任务，采用异步处理机制，避免同步阻塞客户端请求。
- **轻量级状态管理**：使用基于内存的任务表 (`tasks_db`) 追踪任务状态，无需引入 Redis 或 Celery，保持系统极简（注：生产环境下可平滑迁移至持久化存储）。
- **完全解耦的输出**：结果序列化为标准 JSON 格式，调用方可根据需求自行渲染。

## 🚀 快速启动 (Getting Started)

### 环境要求
- Python 3.10+
- 依赖项（请确保已通过 `pip install -r requirements.txt` 安装）

### 启动服务
在项目根目录下运行以下命令启动 Uvicorn 服务器：

```bash
uvicorn main:app --host 0.0.0.0 --port 6006
