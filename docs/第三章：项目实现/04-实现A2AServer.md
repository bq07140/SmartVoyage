## 学习目标

* 理解 MCP 客户端连接与工具加载机制
* 理解 A2A 服务器的任务处理与状态管理
* 理解 Agent 执行循环：分析输入 → 选择工具 → 获取结果 → 格式化回复
* 掌握 AgentCard 的验证方法：检查名称、URL、技能数量和类型
* 掌握集成测试方法：直接调用查询函数验证 Agent + MCP 完整链路



## 一、天气 Agent 服务器

weather\_server.py：天气代理服务器，使用 **LangChain Agent + MCP Tools** 模式处理用户自然语言查询，返回用户友好的文本结果。

**作用**：处理用户自然语言查询，通过 Agent 自主选择调用 MCP 工具，提升智能性，支持追问。

**项目中的定位**：执行层，接收主助手路由的任务，调用 MCP 工具，返回格式化结果。

**核心功能**：

- 初始化 LLM 和 MCP 客户端
- 使用 LangChain Agent 自动选择调用 MCP 工具
- AgentExecutor 执行循环：分析输入 → 选择工具 → 获取结果 → 格式化回复

### 1 导包与配置

**位置**：SmartVoyage/a2a_server/weather_server.py

在编写天气 Agent 之前，需要先导入所需的库并初始化 LLM 和 MCP 客户端。这里涉及三组依赖：

- **MCP 客户端**（`ClientSession`、`streamablehttp_client`）：用于连接后端的天气 MCP 服务器（端口 8002）
- **A2A 协议库**（`python_a2a`）：用于定义 AgentCard、处理任务状态
- **LangChain Agent**（`ChatOpenAI`、`AgentExecutor`、`load_mcp_tools`）：构建智能 Agent，让它能自主选择调用 MCP 工具

```python
import json
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import A2AServer, run_server, AgentCard, AgentSkill, TaskStatus, TaskState

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_tool_calling_agent, AgentExecutor

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from datetime import datetime
import pytz

conf = Config()

# 初始化LLM
llm = ChatOpenAI(
    model=conf.model_name,
    base_url=conf.base_url,
    api_key=conf.api_key,
    temperature=conf.temperature
)

# MCP 服务器运行在 8002 端口
MCP_URL = "http://127.0.0.1:8002/mcp"
```

> **说明**：每个 Agent 服务器的导包结构基本相同，区别仅在于 `MCP_URL` 指向的端口号不同。天气 Agent 连接 8002，票务 Agent 连接 8001，行程 Agent 连接 8003。

### 2 查询函数

这是天气 Agent 的核心逻辑。整个流程分为四步：

1. **连接 MCP 服务器**：通过 `streamablehttp_client` 建立 HTTP 通信通道，创建 `ClientSession` 并初始化
2. **加载 MCP 工具**：`load_mcp_tools(session)` 会自动从 MCP 服务器获取所有已注册的工具（此处为 `query_weather`）
3. **创建 Agent**：通过 LangChain 的 `create_tool_calling_agent` 将 LLM、工具和系统 Prompt 组合成一个 Agent，它具备分析用户输入、选择合适工具、获取结果并格式化回复的能力
4. **执行 Agent**：调用 `agent_executor.ainvoke()` 传入用户对话，Agent 会自动完成"分析 → 选工具 → 调用 → 获取结果 → 回复"的循环

```python
async def query_weather(conversation: str) -> dict:
    """通过 LangChain Agent + MCP Tools 执行天气查询"""
    try:
        # 连接 MCP Server（端口 8002），建立通信通道
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # 从 MCP 会话中加载所有可用工具
                tools = await load_mcp_tools(session)

                # 定义 Agent 的系统 Prompt
                prompt = ChatPromptTemplate.from_messages([
                    ("system", """你是一个天气查询助手，能够调用天气查询工具。
你需要仔细分析用户的问题，从中提取城市和时间信息，然后调用天气查询工具。
如果用户提供的信息不足以查询天气（如缺少城市或日期），则向用户追问。不能自己编造参数。
当前日期是{current_date}。"""),
                    ("human", "{input}"),
                    ("placeholder", "{agent_scratchpad}"),
                ])

                # 创建基于工具调用的 Agent
                agent = create_tool_calling_agent(llm, tools, prompt)

                # 创建 Agent 执行器
                agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

                # 获取当前日期，注入到 Prompt 中
                current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')

                # 执行 Agent
                response = await agent_executor.ainvoke({
                    "input": conversation,
                    "current_date": current_date
                })

                return {"status": "success", "message": response["output"]}

    except ExceptionGroup as eg:
        first_exc = eg.exceptions[0] if eg.exceptions else eg
        logger.error(f"天气 MCP 查询出错：{first_exc}")
        return {"status": "error", "message": f"天气 MCP 查询出错：{first_exc}"}
    except BaseException as e:
        logger.error(f"天气 MCP 查询出错：{str(e)}")
        return {"status": "error", "message": f"天气 MCP 查询出错：{str(e)}"}
```

> **说明**：系统 Prompt 中的 `current_date` 变量会在运行时注入当天的日期，这样 Agent 知道"今天"是哪一天，可以正确理解"明天"、"后天"等相对日期表达。异常处理同时覆盖了 `ExceptionGroup`（LangChain 可能抛出）和 `BaseException`（包括取消等），确保任何错误都能返回友好的错误信息。

### 3 AgentCard 定义

`AgentCard` 是 A2A 协议中用于描述 Agent 身份和能力的名片。当主助手（ChatService）需要与子 Agent 通信时，会先读取 AgentCard 了解该 Agent 能提供哪些服务。每个 AgentCard 包含名称、描述、服务 URL、技能列表等信息。

```python
agent_card = AgentCard(
    name="WeatherQueryAssistant",
    description="基于LangChain提供天气查询服务的助手",
    url="http://localhost:5005",
    version="1.0.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="query weather",
            description="查询天气数据，支持自然语言输入",
            examples=["北京 2025-07-30 天气", "上海未来3天天气如何", "明天天气怎么样"]
        )
    ]
)
```

> **说明**：`url` 是本 Agent 服务器的监听地址（端口 5005），`examples` 用于演示用户可能的输入方式。

### 4 WeatherQueryServer 类

`WeatherQueryServer` 是 A2A 协议的服务器实现，继承自 `A2AServer`。它的工作是接收来自主助手的任务请求，调用 `query_weather` 函数获取结果，然后根据结果设置不同的任务状态：

- **COMPLETED**：查询成功且返回了完整答案，将结果写入 `task.artifacts`
- **INPUT_REQUIRED**：Agent 需要更多信息（如缺少城市或日期），将追问消息返回给主助手
- **FAILED**：查询出错，返回错误信息

其中 `handle_task` 方法是 A2A 协议的核心入口，每次收到任务时自动调用。

```python
class WeatherQueryServer(A2AServer):
    """天气查询 A2A 服务器"""

    def __init__(self):
        super().__init__(agent_card=agent_card)

    def handle_task(self, task):
        # 提取输入
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"用户查询: {conversation}")

        try:
            # 调用 MCP 查询
            weather_result = asyncio.run(query_weather(conversation))
            logger.info(f"MCP 查询返回: {weather_result}")

            # 根据结果设置任务状态
            if weather_result.get("status") == "success":
                result_text = weather_result.get("message", "")

                # 检查是否是追问消息
                if "请提供" in result_text or "请确认" in result_text:
                    task.status = TaskStatus(
                        state=TaskState.INPUT_REQUIRED,
                        message={"role": "agent", "content": {"text": result_text}}
                    )
                else:
                    task.artifacts = [{"parts": [{"type": "text", "text": result_text}]}]
                    task.status = TaskStatus(state=TaskState.COMPLETED)

            elif weather_result.get("status") == "error":
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": weather_result.get("message", "查询失败，请重试。")}}
                )
            else:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": "查询失败，请重试或提供更多细节。"}}
                )

            return task

        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"查询失败: {str(e)} 请重试。"}}
            )
            return task
```

> **说明**：`asyncio.run(query_weather(conversation))` 在同步方法中调用异步函数，这是因为 A2A 协议的 `handle_task` 是同步接口。实际执行时，这里会先连接 MCP 服务器、加载工具、运行 Agent，最后返回结果。

### 5 运行天气 Agent 服务器

在代码块末尾，通过 `run_server()` 启动 A2A 服务器，监听端口 5005。启动前会打印 AgentCard 信息，方便确认服务器状态。

```python
if __name__ == "__main__":
    weather_server = WeatherQueryServer()
    print("\n=== 服务器信息 ===")
    print(f"名称: {weather_server.agent_card.name}")
    print(f"描述: {weather_server.agent_card.description}")
    print("\n技能:")
    for skill in weather_server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    run_server(weather_server, host="127.0.0.1", port=5005)
```



## 二、票务 Agent 服务器

票务 Agent 的结构与天气 Agent 基本一致，区别在于：

- 连接 **8001 端口**的票务 MCP 服务器
- 系统 Prompt 改为票务相关（火车票、机票、演唱会票的查询和预定）
- AgentCard 定义了 **6 个技能**（3 个查询 + 3 个预定）

以下只标注与天气 Agent 的不同之处，相同的导包和异常处理不再重复讲解。

ticket\_server.py：统一的票务代理服务器，使用 **LangChain Agent + MCP Tools** 模式处理用户的票务查询和预定请求。

**作用**：处理用户自然语言查询，通过 Agent 自主选择调用 MCP 工具（查询或预定），返回用户友好的文本结果。

**项目中的定位**：执行层，接收主助手路由的任务，调用 MCP 工具，返回格式化结果。

**核心功能**：

- 初始化 LLM 和 MCP 客户端（端口 8001）
- 使用 LangChain Agent 自动选择调用 MCP 工具
- 同时支持查询和预定功能

### 1 导包与配置

**位置**：SmartVoyage/a2a_server/ticket_server.py

与天气 Agent 相同的导包结构，唯一区别是 `MCP_URL` 指向 8001 端口（票务 MCP 服务器）。

```python
import json
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import A2AServer, run_server, AgentCard, AgentSkill, TaskStatus, TaskState

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_tool_calling_agent, AgentExecutor

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from datetime import datetime
import pytz

conf = Config()

# 初始化LLM
llm = ChatOpenAI(
    model=conf.model_name,
    base_url=conf.base_url,
    api_key=conf.api_key,
    temperature=conf.temperature
)

# MCP 服务器运行在 8001 端口
MCP_URL = "http://127.0.0.1:8001/mcp"
```

### 2 查询函数

票务查询函数与天气的结构完全相同，区别仅在于系统 Prompt。这里需要 Agent 能处理更多场景：火车票、机票、演唱会票的查询和预定，每种类型需要的参数也不同（出发城市、到达城市、日期、座位类型/舱位等）。MCP 服务器共注册了 6 个工具（3 查询 + 3 预定），Agent 会根据用户输入自动选择合适的工具。

```python
async def query_tickets(conversation: str) -> dict:
    """通过 LangChain Agent + MCP Tools 执行票务查询或预定"""
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)

                prompt = ChatPromptTemplate.from_messages([
                    ("system", """你是一个票务查询和预定助手，能够调用工具来完成火车票、机票、演唱会票的查询和预定。
你需要仔细分析用户的问题，从中提取必要的参数（出发城市、到达城市、日期、座位类型等），然后调用对应的工具。
如果用户提供的信息不足以提取到调用工具所有必要参数，则向用户追问。不能自己编造参数。
当前日期是{current_date}。"""),
                    ("human", "{input}"),
                    ("placeholder", "{agent_scratchpad}"),
                ])

                agent = create_tool_calling_agent(llm, tools, prompt)
                agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

                current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')

                response = await agent_executor.ainvoke({
                    "input": conversation,
                    "current_date": current_date
                })

                return {"status": "success", "message": response["output"]}

    except ExceptionGroup as eg:
        first_exc = eg.exceptions[0] if eg.exceptions else eg
        logger.error(f"票务 MCP 查询出错：{first_exc}")
        return {"status": "error", "message": f"票务 MCP 查询出错：{first_exc}"}
    except BaseException as e:
        logger.error(f"票务 MCP 查询出错：{str(e)}")
        return {"status": "error", "message": f"票务 MCP 查询出错：{str(e)}"}
```

### 3 AgentCard 定义

票务 AgentCard 定义了 6 个技能，分为查询和预定两类。主助手通过读取这些技能信息，可以了解票务 Agent 的能力范围。

```python
agent_card = AgentCard(
    name="TicketAssistant",
    description="基于 LangChain 提供票务查询和预定服务的统一助手",
    url="http://localhost:5006",
    version="1.0.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="query train tickets",
            description="查询火车票信息",
            examples=["北京到上海的高铁 2025-10-28"]
        ),
        AgentSkill(
            name="query flight tickets",
            description="查询航班机票信息",
            examples=["上海到北京的机票 10月28日"]
        ),
        AgentSkill(
            name="query concert tickets",
            description="查询演唱会门票信息",
            examples=["刀郎北京演唱会门票"]
        ),
        AgentSkill(
            name="order train tickets",
            description="预定火车票",
            examples=["帮我订一张G1234二等座"]
        ),
        AgentSkill(
            name="order flight tickets",
            description="预定机票",
            examples=["订一张经济舱机票"]
        ),
        AgentSkill(
            name="order concert tickets",
            description="预定演唱会票",
            examples=["买两张刀郎演唱会VIP票"]
        )
    ]
)
```

### 4 TicketQueryServer 类

票务服务器的 `handle_task` 实现与天气服务器完全一致，都是提取输入、调用查询函数、根据结果设置任务状态。此处不再重复讲解。

```python
class TicketQueryServer(A2AServer):
    """票务查询和预定 A2A 服务器"""

    def __init__(self):
        super().__init__(agent_card=agent_card)

    def handle_task(self, task):
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"用户查询: {conversation}")

        try:
            ticket_result = asyncio.run(query_tickets(conversation))
            logger.info(f"MCP 查询返回: {ticket_result}")

            if ticket_result.get("status") == "success":
                result_text = ticket_result.get("message", "")
                if "请提供" in result_text or "请确认" in result_text:
                    task.status = TaskStatus(
                        state=TaskState.INPUT_REQUIRED,
                        message={"role": "agent", "content": {"text": result_text}}
                    )
                else:
                    task.artifacts = [{"parts": [{"type": "text", "text": result_text}]}]
                    task.status = TaskStatus(state=TaskState.COMPLETED)

            elif ticket_result.get("status") == "error":
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": ticket_result.get("message", "查询失败，请重试。")}}
                )
            else:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": "查询失败，请重试或提供更多细节。"}}
                )

            return task

        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"查询失败: {str(e)} 请重试。"}}
            )
            return task
```

### 5 运行票务 Agent 服务器

```python
if __name__ == "__main__":
    ticket_server = TicketQueryServer()
    print("\n=== 服务器信息 ===")
    print(f"名称: {ticket_server.agent_card.name}")
    print(f"描述: {ticket_server.agent_card.description}")
    print("\n技能:")
    for skill in ticket_server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    run_server(ticket_server, host="127.0.0.1", port=5006)
```



## 三、行程 Agent 服务器

trip\_server.py：行程管家代理服务器，处理租车、旅游团、保险的查询及预订请求。与前两个 Agent 结构相同，区别在于：

- 连接 **8003 端口**的行程 MCP 服务器
- 系统 Prompt 需要处理更多业务场景（租车需要取车/还车城市和日期，旅游团使用语义搜索，保险可直接查询）
- AgentCard 定义了 6 个技能

### 1 导包与配置

**位置**：SmartVoyage/a2a_server/trip_server.py

与前两个 Agent 相同的导包，`MCP_URL` 指向 8003 端口。

```python
import json
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import A2AServer, run_server, AgentCard, AgentSkill, TaskStatus, TaskState

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_tool_calling_agent, AgentExecutor

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from datetime import datetime
import pytz

conf = Config()

llm = ChatOpenAI(
    model=conf.model_name,
    base_url=conf.base_url,
    api_key=conf.api_key,
    temperature=conf.temperature
)

# MCP 服务器运行在 8003 端口
MCP_URL = "http://127.0.0.1:8003/mcp"
```

### 2 查询函数

行程 Agent 的 Prompt 最为复杂，因为涉及三种不同类型的服务。其中旅游团查询使用**语义搜索**（Milvus 向量库），与租车/保险的精确查询方式不同。

```python
async def query_trip(conversation: str) -> dict:
    """通过 LangChain Agent + MCP Tools 执行行程查询或预订"""
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)

                prompt = ChatPromptTemplate.from_messages([
                    ("system", """你是一个行程管家助手，能够调用工具来完成租车查询/预定、旅游团查询/报名、保险查询/购买。
你需要仔细分析用户的问题，从问题中提取工具需要的参数，然后调用对应的工具。
如果用户提供的信息不足以提取到调用工具所有必要参数，则向用户追问。不能自己编造参数。

注意：
- query_tour_group 使用语义搜索，query_text 参数是用户的自然语言查询描述。
- query_car_rental 需要取车城市、还车城市、日期。
- query_insurance 可以直接调用，不传 insurance_type 会返回所有保险。

当前日期是{current_date}。"""),
                    ("human", "{input}"),
                    ("placeholder", "{agent_scratchpad}"),
                ])

                agent = create_tool_calling_agent(llm, tools, prompt)
                agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

                current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')

                response = await agent_executor.ainvoke({
                    "input": conversation,
                    "current_date": current_date
                })

                return {"status": "success", "message": response["output"]}

    except ExceptionGroup as eg:
        first_exc = eg.exceptions[0] if eg.exceptions else eg
        logger.error(f"行程 MCP 查询出错：{first_exc}")
        return {"status": "error", "message": f"行程 MCP 查询出错：{first_exc}"}
    except BaseException as e:
        logger.error(f"行程 MCP 查询出错：{str(e)}")
        return {"status": "error", "message": f"行程 MCP 查询出错：{str(e)}"}
```

### 3 AgentCard 定义

行程 AgentCard 同样定义了 6 个技能（3 查询 + 3 预定），涵盖租车、旅游团、保险三个场景。

```python
agent_card = AgentCard(
    name="TripAssistant",
    description="基于 LangChain 提供行程管家服务的统一助手，支持租车、旅游团、保险的查询与预订",
    url="http://localhost:5007",
    version="1.0.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(name="query car rental", description="查询租车信息", examples=["租车 北京 上海 2025-08-01"]),
        AgentSkill(name="query tour group", description="通过语义搜索查询旅游团信息", examples=["想看雪山的地方"]),
        AgentSkill(name="query insurance", description="查询旅行保险产品", examples=["旅行保险"]),
        AgentSkill(name="order car rental", description="预订租车服务", examples=["预订租车 2025-08-01 SUV 1辆"]),
        AgentSkill(name="order tour group", description="报名旅游团", examples=["报名丽江三日游 2人"]),
        AgentSkill(name="order insurance", description="购买旅行保险", examples=["购买综合型保险"])
    ]
)
```

### 4 TripQueryServer 类

行程服务器的 `handle_task` 实现与前两个完全一致，都是提取输入、调用查询函数、根据结果设置任务状态。

```python
class TripQueryServer(A2AServer):
    """行程管家 A2A 服务器"""

    def __init__(self):
        super().__init__(agent_card=agent_card)

    def handle_task(self, task):
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"用户查询: {conversation}")

        try:
            trip_result = asyncio.run(query_trip(conversation))
            logger.info(f"MCP 查询返回: {trip_result}")

            if trip_result.get("status") == "success":
                result_text = trip_result.get("message", "")
                if "请提供" in result_text or "请确认" in result_text:
                    task.status = TaskStatus(
                        state=TaskState.INPUT_REQUIRED,
                        message={"role": "agent", "content": {"text": result_text}}
                    )
                else:
                    task.artifacts = [{"parts": [{"type": "text", "text": result_text}]}]
                    task.status = TaskStatus(state=TaskState.COMPLETED)

            elif trip_result.get("status") == "error":
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": trip_result.get("message", "查询失败，请重试。")}}
                )
            else:
                task.status = TaskStatus(
                    state=TaskState.FAILED,
                    message={"role": "agent", "content": {"text": "查询失败，请重试或提供更多细节。"}}
                )

            return task

        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            task.status = TaskStatus(
                state=TaskState.FAILED,
                message={"role": "agent", "content": {"text": f"查询失败: {str(e)} 请重试。"}}
            )
            return task
```

### 5 运行行程 Agent 服务器

```python
if __name__ == "__main__":
    trip_server = TripQueryServer()
    print("\n=== 服务器信息 ===")
    print(f"名称: {trip_server.agent_card.name}")
    print(f"描述: {trip_server.agent_card.description}")
    print("\n技能:")
    for skill in trip_server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    run_server(trip_server, host="127.0.0.1", port=5007)
```



## 四、Agent 服务器测试

### 1 AgentCard 定义测试（纯属性检查，零依赖）

验证每个 Agent 的代理卡片信息是否正确配置。测试只检查属性值，不需要启动服务器或连接 API。

```python
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestWeatherAgentCard(unittest.TestCase):
    """测试天气 Agent 的代理卡片定义"""

    def test_agent_card_basic(self):
        """验证代理卡片基本信息"""
        from SmartVoyage.a2a_server.weather_server import agent_card

        self.assertEqual(agent_card.name, "WeatherQueryAssistant")
        self.assertIn("天气查询", agent_card.description)
        self.assertEqual(agent_card.url, "http://localhost:5005")
        self.assertEqual(len(agent_card.skills), 1)

    def test_weather_skill_examples(self):
        """验证天气技能示例"""
        from SmartVoyage.a2a_server.weather_server import agent_card

        skill = agent_card.skills[0]
        self.assertIn("weather", skill.name.lower())
        self.assertIsNotNone(skill.description)
        self.assertGreater(len(skill.examples), 0)


class TestTicketAgentCard(unittest.TestCase):
    """测试票务 Agent 的代理卡片定义"""

    def test_agent_card_skills(self):
        """验证票务代理拥有 6 个技能"""
        from SmartVoyage.a2a_server.ticket_server import agent_card

        self.assertEqual(agent_card.name, "TicketAssistant")
        self.assertEqual(agent_card.url, "http://localhost:5006")
        self.assertEqual(len(agent_card.skills), 6)

    def test_has_query_and_order_skills(self):
        """验证同时包含查询和预定技能"""
        from SmartVoyage.a2a_server.ticket_server import agent_card

        skill_names = [s.name for s in agent_card.skills]
        query_skills = [n for n in skill_names if 'query' in n.lower()]
        order_skills = [n for n in skill_names if 'order' in n.lower()]
        self.assertEqual(len(query_skills), 3, "应该有3个查询技能")
        self.assertEqual(len(order_skills), 3, "应该有3个预定技能")


class TestTripAgentCard(unittest.TestCase):
    """测试行程 Agent 的代理卡片定义"""

    def test_agent_card_skills(self):
        """验证行程代理拥有 6 个技能"""
        from SmartVoyage.a2a_server.trip_server import agent_card

        self.assertEqual(agent_card.name, "TripAssistant")
        self.assertEqual(agent_card.url, "http://localhost:5007")
        self.assertEqual(len(agent_card.skills), 6)

    def test_has_all_trip_skills(self):
        """验证包含租车、旅游团、保险的查询和预定技能"""
        from SmartVoyage.a2a_server.trip_server import agent_card

        skill_names = [s.name.lower() for s in agent_card.skills]
        self.assertTrue(any('car' in n for n in skill_names), "缺少租车技能")
        self.assertTrue(any('tour' in n for n in skill_names), "缺少旅游团技能")
        self.assertTrue(any('insurance' in n for n in skill_names), "缺少保险技能")
```

### 2 Agent 服务器集成测试（需要 MCP 服务器 + LLM API）

直接调用 `query_weather()`、`query_tickets()`、`query_trip()` 异步函数，验证 LangChain Agent + MCP Tools 的完整业务流程。

```python
class TestWeatherAgentIntegration(unittest.TestCase):
    """集成测试：天气 Agent 服务器"""

    def test_handle_task_weather_query(self):
        """测试天气 Agent 完整查询流程"""
        import asyncio
        from SmartVoyage.a2a_server.weather_server import WeatherQueryServer, query_weather

        result = asyncio.run(query_weather("北京 2025-07-30 天气"))
        self.assertEqual(result["status"], "success")
        self.assertIn("北京", result["message"])

    def test_weather_server_instance(self):
        """验证天气服务器实例化"""
        from SmartVoyage.a2a_server.weather_server import WeatherQueryServer
        server = WeatherQueryServer()
        self.assertEqual(server.agent_card.name, "WeatherQueryAssistant")


class TestTicketAgentIntegration(unittest.TestCase):
    """集成测试：票务 Agent 服务器"""

    def test_handle_task_ticket_query(self):
        """测试票务 Agent 完整查询流程"""
        import asyncio
        from SmartVoyage.a2a_server.ticket_server import query_tickets

        result = asyncio.run(query_tickets("北京 到 上海 火车票 2025-07-30"))
        self.assertEqual(result["status"], "success")
        self.assertIn("北京", result["message"])
        self.assertIn("上海", result["message"])


class TestTripAgentIntegration(unittest.TestCase):
    """集成测试：行程 Agent 服务器"""

    def test_handle_task_trip_query(self):
        """测试行程 Agent 完整查询流程"""
        import asyncio
        from SmartVoyage.a2a_server.trip_server import query_trip

        result = asyncio.run(query_trip("北京租车 2025-08-01"))
        self.assertEqual(result["status"], "success")
        self.assertIn("北京", result["message"])
```

### 3 运行测试

```bash
cd SmartVoyage
python -m tests.test_agent_servers
```

预期输出：

```
test_agent_card_basic (__main__.TestWeatherAgentCard.test_agent_card_basic) ... ok
test_weather_skill_examples (__main__.TestWeatherAgentCard.test_weather_skill_examples) ... ok
test_agent_card_skills (__main__.TestTicketAgentCard.test_agent_card_skills) ... ok
test_has_query_and_order_skills (__main__.TestTicketAgentCard.test_has_query_and_order_skills) ... ok
test_agent_card_skills (__main__.TestTripAgentCard.test_agent_card_skills) ... ok
test_has_all_trip_skills (__main__.TestTripAgentCard.test_has_all_trip_skills) ... ok
test_handle_task_weather_query (__main__.TestWeatherAgentIntegration) ... ok
test_weather_server_instance (__main__.TestWeatherAgentIntegration) ... ok
test_handle_task_ticket_query (__main__.TestTicketAgentIntegration) ... ok
test_handle_task_trip_query (__main__.TestTripAgentIntegration) ... ok
----------------------------------------------------------------------
Ran 10 tests in 15.23s

OK
```

> **注意**：AgentCard 定义测试无需任何依赖即可运行。集成测试部分需要对应的 MCP 服务器已启动（天气 8002、票务 8001、行程 8003），且配置了有效的 LLM API（DASHSCOPE_API_KEY）。