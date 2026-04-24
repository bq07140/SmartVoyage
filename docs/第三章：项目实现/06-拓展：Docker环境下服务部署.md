# 服务部署：使用 Docker 部署 SmartVoyage 智能旅游系统

## 学习目标

1. 理解 Docker 容器化部署的基本概念与优势，能够独立编写 Dockerfile 将 Python 项目打包为镜像
2. 掌握 SmartVoyage 项目的代码改造要点，使其适配 Docker 容器化运行环境
3. 学会使用 docker-compose 编排并启动多个 MCP 服务与 A2A Agent 服务，理解"同一镜像 + 不同环境变量 = 不同服务"的部署模式
4. 掌握 Docker 网络配置，实现容器间通信以及与外部 MySQL、Milvus 等中间件的连接

---

## 一、项目架构与端口总览

在开始部署之前，先理清 SmartVoyage 项目的整体架构和各服务监听的端口：

```
┌─────────────────────────────────────────────────────────────────┐
│                        SmartVoyage 架构                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   用户浏览器 / 客户端                                            │
│        │                                                        │
│        ▼                                                        │
│   ┌──────────────┐                                              │
│   │  API Server  │  端口 8080（FastAPI，对外提供 REST/SSE 接口） │
│   └──────┬───────┘                                              │
│          │ A2A 协议                                              │
│          ▼                                                       │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│   │ Weather A2A  │  │ Ticket A2A   │  │  Trip A2A    │          │
│   │   :5005      │  │   :5006      │  │   :5007      │          │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│          │ MCP             │ MCP             │ MCP              │
│          ▼                 ▼                 ▼                  │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│   │ Weather MCP  │  │ Ticket MCP   │  │  Trip MCP    │          │
│   │   :8002      │  │   :8001      │  │   :8003      │          │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│          │                 │                 │                  │
│          ▼                 ▼                 ▼                  │
│   ┌─────────────────────────────────────────────────┐           │
│   │            外部依赖服务                          │           │
│   │  MySQL(:3306) │ Milvus(:19530) │ LLM API(HTTPS) │           │
│   └─────────────────────────────────────────────────┘           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

| 服务类型 | 模块文件 | 默认端口 | 说明 |
|---------|---------|---------|------|
| API Server | `api_server.py` | 8080 | FastAPI 主入口，面向前端/客户端 |
| A2A - Weather | `a2a_server/weather_server.py` | 5005 | 天气查询 Agent |
| A2A - Ticket | `a2a_server/ticket_server.py` | 5006 | 票务查询 Agent |
| A2A - Trip | `a2a_server/trip_server.py` | 5007 | 行程管家 Agent |
| MCP - Ticket | `mcp_server/mcp_ticket_server.py` | 8001 | 票务 MCP 工具服务 |
| MCP - Weather | `mcp_server/mcp_weather_server.py` | 8002 | 天气 MCP 工具服务 |
| MCP - Trip | `mcp_server/mcp_trip_server.py` | 8003 | 行程 MCP 工具服务 |

---

## 二、代码改造要点

原始代码是为本地开发环境编写的，部署到 Docker 容器前需要做以下改造。这些改造不涉及业务逻辑变更，只是让服务适配容器化运行环境。

### 2.1 绑定地址：从 `127.0.0.1` 改为 `0.0.0.0`

**问题**：容器内如果服务只绑定 `127.0.0.1`（本地回环），容器外和其他容器都无法访问该服务。

**改造方案**：所有服务的 `host` 参数需要改为 `0.0.0.0`（监听所有网络接口）。

以 `api_server.py` 为例，原始代码：
```python
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
```

改造后：
```python
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info")
```

同样的原则应用到所有服务启动文件（MCP 和 A2A 服务）：
- MCP 服务的 `FastMCP(..., host="127.0.0.1", port=...)` 改为从环境变量读取
- A2A 服务的 `run_server(server, host="127.0.0.1", port=...)` 改为从环境变量读取

### 2.2 配置外部化：环境变量驱动

**问题**：当前 `config.py` 中数据库连接、API Key 等都是硬编码的，容器化后这些值因部署环境而异。

**改造方案**：所有敏感信息和环境相关配置通过环境变量注入。

改造后的 `config.py` 核心变更：
```python
import os

class Config:
    def __init__(self):
        # 大模型配置 — 从环境变量读取
        self.base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.model_name = os.getenv("LLM_MODEL_NAME", "qwen3.6-plus")

        # 数据库配置 — 从环境变量读取
        self.host = os.getenv("MYSQL_HOST", "mysql")
        self.user = os.getenv("MYSQL_USER", "smart_yoyage")
        self.password = os.getenv("MYSQL_PASSWORD", "123456")
        self.database = os.getenv("MYSQL_DATABASE", "travel_rag")

        # 日志路径
        self.log_file = os.getenv("LOG_FILE", "/app/logs/app.log")

        # 其他配置保持不变...
```

### 2.3 A2A 服务地址：指向容器名而非 localhost

**问题**：`main.py` 和 `chat_service.py` 中的 `agent_urls` 指向 `localhost:5005/5006/5007`，在容器内这些 Agent 运行在不同的容器中，需要通过 Docker 网络的服务名（容器名）来访问。

**改造方案**：使用环境变量配置 Agent 地址。

改造 `chat_service.py` 中的 Agent 地址配置：
```python
# 改造前
self.agent_urls = {
    "WeatherQueryAssistant": "http://localhost:5005",
    "TicketAssistant": "http://localhost:5006",
    "TripAssistant": "http://localhost:5007"
}

# 改造后
self.agent_urls = {
    "WeatherQueryAssistant": os.getenv("WEATHER_A2A_URL", "http://weather-a2a:5005"),
    "TicketAssistant": os.getenv("TICKET_A2A_URL", "http://ticket-a2a:5006"),
    "TripAssistant": os.getenv("TRIP_A2A_URL", "http://trip-a2a:5007")
}
```

同样的，A2A 服务连接 MCP 的 URL 也需要从环境变量读取：
```python
# a2a_server/weather_server.py 中
MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8002/mcp")

# a2a_server/ticket_server.py 中
MCP_URL = os.getenv("TICKET_MCP_URL", "http://ticket-mcp:8001/mcp")

# a2a_server/trip_server.py 中
MCP_URL = os.getenv("TRIP_MCP_URL", "http://trip-mcp:8003/mcp")
```

### 2.4 日志路径：使用容器内标准路径

**问题**：原代码日志路径指向宿主机目录，容器内该路径不存在。

**改造方案**：
```python
# 日志输出到容器内的 /app/logs/ 目录
self.log_file = os.getenv("LOG_FILE", "/app/logs/app.log")
```

容器启动时创建该目录即可。

### 2.5 Milvus 向量数据库地址

**问题**：`mcp_trip_server.py` 中硬编码了 `MILVUS_HOST = "localhost"`。

**改造方案**：
```python
MILVUS_HOST = os.getenv("MILVUS_HOST", "milvus")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))
```

### 2.6 和风天气 API Key

**问题**：`mcp_weather_server.py` 中硬编码了 `HEFENG_API_KEY`。

**改造方案**：
```python
HEFENG_API_KEY = os.getenv("HEFENG_WEATHER_API_KEY", "5ef0a47e161a4ea997227322317eae83")
```

---

## 三、编写 Dockerfile

SmartVoyage 的所有服务共享同一套代码和依赖，因此可以使用**同一个 Dockerfile 构建镜像**，通过启动命令和环境变量来决定运行哪个服务。

```dockerfile
# ==========================================
# SmartVoyage 统一 Dockerfile
# 适用于所有服务：API Server、A2A Agent、MCP Server
# ==========================================

# 1. 基础镜像：使用 Python 3.11  slim 版本，体积小且包含必要依赖
FROM python:3.11-slim

# 2. 设置工作目录
WORKDIR /app

# 3. 设置环境变量
# - 防止 Python 生成 .pyc 字节码文件（容器不需要）
# - 确保日志实时输出到终端（不缓冲）
# - 默认服务监听地址为 0.0.0.0（允许外部访问）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0

# 4. 安装系统级依赖
# - gcc: 编译 mysqlclient 等 C 扩展包所必需
# - pkg-config: mysqlclient 编译时查找 MySQL 头文件
# - default-libmysqlclient-dev: MySQL 开发库
# - curl: 健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    pkg-config \
    default-libmysqlclient-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 5. 复制依赖文件并安装 Python 包
# 先复制 requirements.txt，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. 复制项目代码
# 只需要 SmartVoyage 目录
COPY SmartVoyage/ ./SmartVoyage/

# 7. 创建日志目录
RUN mkdir -p /app/logs

# 8. 暴露常用端口（仅作为文档说明，实际以 docker-compose 映射为准）
EXPOSE 5005 5006 5007 8001 8002 8003 8080

# 9. 默认入口命令
# 通过 SERVICE_NAME 环境变量决定启动哪个服务
# 入口脚本会在容器启动时执行
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api_server"]
```

### 入口脚本 `entrypoint.sh`

```bash
#!/bin/bash
# ==========================================
# SmartVoyage 容器入口脚本
# 根据 SERVICE_NAME 环境变量决定启动哪个服务
# ==========================================

set -e

SERVICE_NAME="${1:-api_server}"

echo "=== 启动 SmartVoyage 服务: ${SERVICE_NAME} ==="

case "${SERVICE_NAME}" in
    api_server)
        echo "启动 API Server (端口 ${PORT:-8080})"
        exec python -m SmartVoyage.api_server
        ;;
    weather_a2a)
        echo "启动 Weather A2A Agent (端口 ${PORT:-5005})"
        exec python -m SmartVoyage.a2a_server.weather_server
        ;;
    ticket_a2a)
        echo "启动 Ticket A2A Agent (端口 ${PORT:-5006})"
        exec python -m SmartVoyage.a2a_server.ticket_server
        ;;
    trip_a2a)
        echo "启动 Trip A2A Agent (端口 ${PORT:-5007})"
        exec python -m SmartVoyage.a2a_server.trip_server
        ;;
    weather_mcp)
        echo "启动 Weather MCP Server (端口 ${PORT:-8002})"
        exec python -m SmartVoyage.mcp_server.mcp_weather_server
        ;;
    ticket_mcp)
        echo "启动 Ticket MCP Server (端口 ${PORT:-8001})"
        exec python -m SmartVoyage.mcp_server.mcp_ticket_server
        ;;
    trip_mcp)
        echo "启动 Trip MCP Server (端口 ${PORT:-8003})"
        exec python -m SmartVoyage.mcp_server.mcp_trip_server
        ;;
    *)
        echo "未知服务: ${SERVICE_NAME}"
        echo "可用服务: api_server, weather_a2a, ticket_a2a, trip_a2a, weather_mcp, ticket_mcp, trip_mcp"
        exit 1
        ;;
esac
```

将 `entrypoint.sh` 放在项目根目录下（与 `SmartVoyage/` 同级）。

---

## 四、构建 Docker 镜像

```bash
# 进入项目根目录（包含 Dockerfile、entrypoint.sh、requirements.txt、SmartVoyage/ 的目录）
cd /path/to/04-代码

# 构建镜像，命名为 smart-voyage
docker build -t smart-voyage:latest .
```

构建完成后，同一个 `smart-voyage:latest` 镜像可以用于启动**所有 7 个服务**。

---

## 五、使用 docker-compose 编排全部服务

单个容器只运行一个服务，多个容器需要协调配合。`docker-compose` 是管理多容器的标准工具。

### 5.1 完整 docker-compose.yml

```yaml
version: "3.8"

services:
  # ==========================================
  # 基础设施服务
  # ==========================================

  # MySQL 数据库
  mysql:
    image: mysql:8.0
    container_name: sv-mysql
    restart: always
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: travel_rag
      MYSQL_USER: smart_yoyage
      MYSQL_PASSWORD: 123456
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
      - ./SmartVoyage/sql:/docker-entrypoint-initdb.d  # 初始化 SQL 脚本
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - smart-voyage-net

  # Milvus 向量数据库（轻量单机版）
  milvus:
    image: milvusdb/milvus:v2.4.0
    container_name: sv-milvus
    restart: always
    environment:
      ETCD_USE_EMBED: "true"
      ETCD_DATA_DIR: /var/lib/milvus/etcd
    ports:
      - "19530:19530"
    volumes:
      - milvus_data:/var/lib/milvus
    command: ["milvus", "run", "standalone"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - smart-voyage-net

  # ==========================================
  # MCP 服务层（工具提供者）
  # ==========================================

  ticket-mcp:
    image: smart-voyage:latest
    container_name: sv-ticket-mcp
    restart: always
    command: ["ticket_mcp"]
    environment:
      - SERVICE_NAME=ticket_mcp
      - PORT=8001
      - HOST=0.0.0.0
      - MYSQL_HOST=mysql
      - MYSQL_USER=smart_yoyage
      - MYSQL_PASSWORD=123456
      - MYSQL_DATABASE=travel_rag
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
    ports:
      - "8001:8001"
    depends_on:
      mysql:
        condition: service_healthy
    networks:
      - smart-voyage-net

  weather-mcp:
    image: smart-voyage:latest
    container_name: sv-weather-mcp
    restart: always
    command: ["weather_mcp"]
    environment:
      - SERVICE_NAME=weather_mcp
      - PORT=8002
      - HOST=0.0.0.0
      - MYSQL_HOST=mysql
      - MYSQL_USER=smart_yoyage
      - MYSQL_PASSWORD=123456
      - MYSQL_DATABASE=travel_rag
      - HEFENG_WEATHER_API_KEY=${HEFENG_WEATHER_API_KEY}
    ports:
      - "8002:8002"
    depends_on:
      mysql:
        condition: service_healthy
    networks:
      - smart-voyage-net

  trip-mcp:
    image: smart-voyage:latest
    container_name: sv-trip-mcp
    restart: always
    command: ["trip_mcp"]
    environment:
      - SERVICE_NAME=trip_mcp
      - PORT=8003
      - HOST=0.0.0.0
      - MYSQL_HOST=mysql
      - MYSQL_USER=smart_yoyage
      - MYSQL_PASSWORD=123456
      - MYSQL_DATABASE=travel_rag
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - MILVUS_HOST=milvus
      - MILVUS_PORT=19530
    ports:
      - "8003:8003"
    depends_on:
      mysql:
        condition: service_healthy
      milvus:
        condition: service_healthy
    networks:
      - smart-voyage-net

  # ==========================================
  # A2A Agent 层（智能决策者）
  # ==========================================

  weather-a2a:
    image: smart-voyage:latest
    container_name: sv-weather-a2a
    restart: always
    command: ["weather_a2a"]
    environment:
      - SERVICE_NAME=weather_a2a
      - PORT=5005
      - HOST=0.0.0.0
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
      - LLM_MODEL_NAME=qwen3.6-plus
      - WEATHER_MCP_URL=http://weather-mcp:8002/mcp
    ports:
      - "5005:5005"
    depends_on:
      - weather-mcp
    networks:
      - smart-voyage-net

  ticket-a2a:
    image: smart-voyage:latest
    container_name: sv-ticket-a2a
    restart: always
    command: ["ticket_a2a"]
    environment:
      - SERVICE_NAME=ticket_a2a
      - PORT=5006
      - HOST=0.0.0.0
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
      - LLM_MODEL_NAME=qwen3.6-plus
      - TICKET_MCP_URL=http://ticket-mcp:8001/mcp
    ports:
      - "5006:5006"
    depends_on:
      - ticket-mcp
    networks:
      - smart-voyage-net

  trip-a2a:
    image: smart-voyage:latest
    container_name: sv-trip-a2a
    restart: always
    command: ["trip_a2a"]
    environment:
      - SERVICE_NAME=trip_a2a
      - PORT=5007
      - HOST=0.0.0.0
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
      - LLM_MODEL_NAME=qwen3.6-plus
      - TRIP_MCP_URL=http://trip-mcp:8003/mcp
    ports:
      - "5007:5007"
    depends_on:
      - trip-mcp
    networks:
      - smart-voyage-net

  # ==========================================
  # API 服务层（前端入口）
  # ==========================================

  api-server:
    image: smart-voyage:latest
    container_name: sv-api-server
    restart: always
    command: ["api_server"]
    environment:
      - SERVICE_NAME=api_server
      - PORT=8080
      - HOST=0.0.0.0
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
      - LLM_MODEL_NAME=qwen3.6-plus
      - MYSQL_HOST=mysql
      - MYSQL_USER=smart_yoyage
      - MYSQL_PASSWORD=123456
      - MYSQL_DATABASE=travel_rag
      - WEATHER_A2A_URL=http://weather-a2a:5005
      - TICKET_A2A_URL=http://ticket-a2a:5006
      - TRIP_A2A_URL=http://trip-a2a:5007
    ports:
      - "8080:8080"
    depends_on:
      - mysql
      - weather-a2a
      - ticket-a2a
      - trip-a2a
    networks:
      - smart-voyage-net

volumes:
  mysql_data:
  milvus_data:

networks:
  smart-voyage-net:
    driver: bridge
```

### 5.2 环境变量文件 `.env`

在项目根目录创建 `.env` 文件，存放敏感配置：

```bash
# .env 文件 — 不要提交到版本控制
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
HEFENG_WEATHER_API_KEY=5ef0a47e161a4ea997227322317eae83
```

### 5.3 一键启动

```bash
# 启动所有服务（后台运行）
docker compose up -d

# 查看各服务状态
docker compose ps

# 查看日志
docker compose logs -f api-server

# 停止所有服务
docker compose down

# 停止并清理数据卷（会删除数据库数据！）
docker compose down -v
```

### 5.4 启动顺序说明

`docker-compose.yml` 中通过 `depends_on` 控制了启动顺序：

```
MySQL / Milvus → MCP 服务 → A2A Agent → API Server
```

这个顺序确保了每个服务启动时，它依赖的下层服务已经就绪。例如：
- MCP 服务需要 MySQL 连接，所以 `depends_on mysql: condition: service_healthy`
- A2A Agent 需要连接 MCP 服务，所以 `depends_on: weather-mcp`
- API Server 需要所有 A2A Agent 就绪，所以 `depends_on` 三个 A2A 服务

---

## 六、同一镜像启动多个服务的原理

这是 Docker 部署的核心概念之一。

### 6.1 核心思想

> **一个镜像 + 不同的启动命令/环境变量 = 多个不同的服务**

```
                    ┌─────────────────────┐
                    │  smart-voyage:latest │  ← 同一个镜像
                    └──────────┬──────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
   command: ["api_server"]  command: ["ticket_mcp"]  command: ["weather_a2a"]
   PORT=8080                PORT=8001                PORT=5005
   MYSQL_HOST=mysql         MYSQL_HOST=mysql         MCP_URL=...
           │                   │                      │
           ▼                   ▼                      ▼
    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
    │  API Server  │   │ Ticket MCP   │   │ Weather A2A  │
    │   :8080      │   │   :8001      │   │   :5005      │
    └──────────────┘   └──────────────┘   └──────────────┘
```

### 6.2 入口脚本的工作原理

Dockerfile 中定义了 `ENTRYPOINT ["/entrypoint.sh"]`，容器启动时执行该脚本。脚本通过 `case` 语句判断 `$1`（传入的第一个参数，即 `command`）来决定执行哪个 Python 模块。

在 `docker-compose.yml` 中，每个服务通过 `command: ["service_name"]` 传入不同的参数：
```yaml
# 启动 API Server
api-server:
  command: ["api_server"]   # 传入 entrypoint.sh 的第一个参数

# 启动 Ticket MCP
ticket-mcp:
  command: ["ticket_mcp"]   # 传入 entrypoint.sh 的第一个参数
```

### 6.3 这种方式的优势

1. **镜像体积小**：只需构建一个镜像，不需要为每个服务单独构建
2. **版本一致性**：所有服务使用完全相同的代码版本和依赖版本
3. **部署简单**：新增服务只需要在 docker-compose.yml 中添加一个 service 定义
4. **资源复用**：Docker 层缓存共享，拉取/构建都更快

---

## 七、Docker 网络配置详解

### 7.1 自定义 bridge 网络

`docker-compose` 会自动创建一个名为 `smart-voyage-net` 的 bridge 网络，所有服务容器都连接到这个网络。

```
┌────────────────────────────────────────────────────┐
│              smart-voyage-net (bridge)              │
│                                                    │
│  sv-api-server ←→ sv-weather-a2a                   │
│  sv-api-server ←→ sv-ticket-a2a                    │
│  sv-api-server ←→ sv-trip-a2a                      │
│  sv-weather-a2a ←→ sv-weather-mcp                  │
│  sv-ticket-a2a ←→ sv-ticket-mcp                    │
│  sv-trip-a2a ←→ sv-trip-mcp                        │
│  sv-*-mcp ←→ sv-mysql                              │
│  sv-trip-mcp ←→ sv-milvus                          │
└────────────────────────────────────────────────────┘
```

### 7.2 容器间通信：使用服务名

在同一个 Docker 网络中，容器可以通过**服务名**互相访问，不需要知道具体 IP：

```python
# 在容器内访问 MySQL
MYSQL_HOST = "mysql"          # 不是 "localhost"，是 docker-compose 中定义的服务名

# 在容器内访问 MCP 服务
MCP_URL = "http://ticket-mcp:8001/mcp"  # 服务名:端口

# 在容器内访问 A2A Agent
AGENT_URL = "http://weather-a2a:5005"
```

### 7.3 端口映射：容器内外

`ports` 配置控制宿主机到容器的端口映射：
```yaml
ports:
  - "8080:8080"   # 宿主机端口:容器端口
```

- 左边 `8080`：宿主机端口，外部访问用 `http://localhost:8080`
- 右边 `8080`：容器内服务监听的端口

**注意**：容器间通信不需要端口映射，直接使用服务名和容器内端口即可。

---

## 八、生产环境部署建议

### 8.1 水平扩展：多个 MCP 实例

当单个 MCP 服务无法承载请求量时，可以启动多个实例。以 Ticket MCP 为例：

```yaml
ticket-mcp-1:
  image: smart-voyage:latest
  container_name: sv-ticket-mcp-1
  command: ["ticket_mcp"]
  environment:
    - SERVICE_NAME=ticket_mcp
    - PORT=8001
    - MYSQL_HOST=mysql
    # ... 其他环境变量

ticket-mcp-2:
  image: smart-voyage:latest
  container_name: sv-ticket-mcp-2
  command: ["ticket_mcp"]
  environment:
    - SERVICE_NAME=ticket_mcp
    - PORT=8004  # 注意：使用不同端口
    - MYSQL_HOST=mysql
    # ... 其他环境变量
```

配合 Nginx 或 Traefik 做负载均衡，将请求分发到多个 MCP 实例。

### 8.2 健康检查

为每个服务添加健康检查，确保服务异常时自动重启：
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

### 8.3 资源限制

限制每个容器的 CPU 和内存使用，防止某个服务耗尽宿主机资源：
```yaml
deploy:
  resources:
    limits:
      cpus: "1.0"
      memory: 512M
    reservations:
      cpus: "0.5"
      memory: 256M
```

### 8.4 日志管理

使用 Docker 日志驱动将日志集中管理：
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

---

## 九、常见问题排查

### 9.1 服务无法连接 MySQL

```bash
# 检查 MySQL 是否就绪
docker compose exec mysql mysqladmin ping -h localhost

# 查看 MySQL 日志
docker compose logs mysql
```

**原因**：MySQL 启动较慢，其他服务可能先启动。确保使用了 `condition: service_healthy`。

### 9.2 容器间无法互相访问

```bash
# 进入容器测试网络连通性
docker compose exec api-server curl -v http://weather-a2a:5005/

# 检查容器是否在同一个网络
docker network inspect 04-code_smart-voyage-net
```

**原因**：检查服务名是否正确、是否在同一个 Docker 网络。

### 9.3 MCP 服务连接失败

检查 A2A Agent 中 `MCP_URL` 环境变量是否指向正确的容器服务名和端口。

### 9.4 查看特定服务日志

```bash
# 实时查看 API Server 日志
docker compose logs -f api-server

# 查看最近 100 行日志
docker compose logs --tail=100 ticket-a2a
```

---

## 十、部署流程总结

```
┌─────────────┐
│ 1. 代码改造 │  ← 环境变量驱动、绑定 0.0.0.0、容器名代替 localhost
└──────┬──────┘
       ▼
┌─────────────┐
│ 2. 编写     │  ← Dockerfile + entrypoint.sh
│  Dockerfile │
└──────┬──────┘
       ▼
┌─────────────┐
│ 3. 构建镜像 │  ← docker build -t smart-voyage:latest .
└──────┬──────┘
       ▼
┌─────────────┐
│ 4. 编写     │  ← docker-compose.yml + .env
│ docker-     │
│ compose.yml │
└──────┬──────┘
       ▼
┌─────────────┐
│ 5. 一键启动 │  ← docker compose up -d
└──────┬──────┘
       ▼
┌─────────────┐
│ 6. 验证服务 │  ← curl http://localhost:8080/
│             │    docker compose ps
└─────────────┘
```
