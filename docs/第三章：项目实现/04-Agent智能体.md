## 学习目标

* 理解 LangChain Agent + MCP Tools 的 Agent 执行模式

* 理解 MCP 客户端连接与工具加载机制

* 理解 AgentCard 的定义与作用

* 理解 A2A 服务器的任务处理与状态管理

* 理解 Agent 执行循环：分析输入 → 选择工具 → 获取结果 → 格式化回复

* 掌握 Agent 服务器的测试方法：验证 AgentCard 定义、模拟异步查询返回、测试任务状态转换



## 一、天气 Agent 服务器

weather\_server.py：天气代理服务器，使用 **LangChain Agent + MCP Tools** 模式处理用户自然语言查询，返回用户友好的文本结果。

**作用**：处理用户自然语言查询，通过 Agent 自主选择调用 MCP 工具，提升智能性，支持追问。

**项目中的定位**：执行层，接收主助手路由的任务，调用 MCP 工具，返回格式化结果。

**核心功能**：

- 初始化 LLM 和 MCP 客户端
- 使用 LangChain Agent 自动选择调用 MCP 工具
- AgentExecutor 执行循环：分析输入 → 选择工具 → 获取结果 → 格式化回复

### 1 导包与配置

位置：SmartVoyage/a2a_server/weather_server.py

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

### 2 查询函数

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

### 3 AgentCard 定义

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

### 4 WeatherQueryServer 类

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

### 5 运行天气 Agent 服务器

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

ticket\_server.py：统一的票务代理服务器，使用 **LangChain Agent + MCP Tools** 模式处理用户的票务查询和预定请求。

**作用**：处理用户自然语言查询，通过 Agent 自主选择调用 MCP 工具（查询或预定），返回用户友好的文本结果。

**项目中的定位**：执行层，接收主助手路由的任务，调用 MCP 工具，返回格式化结果。

**核心功能**：

- 初始化 LLM 和 MCP 客户端（端口 8001）
- 使用 LangChain Agent 自动选择调用 MCP 工具
- 同时支持查询和预定功能

### 1 导包与配置

位置：SmartVoyage/a2a_server/ticket_server.py

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

trip\_server.py：行程管家代理服务器，使用 **LangChain Agent + MCP Tools** 模式处理用户的租车、旅游团、保险查询及预订请求。

**作用**：处理用户自然语言查询，通过 Agent 自主选择调用 MCP 工具，返回用户友好的文本结果。

**项目中的定位**：执行层，接收主助手路由的任务，调用 MCP 工具（端口 8003），返回格式化结果。

### 1 导包与配置

位置：SmartVoyage/a2a_server/trip_server.py

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

### 1 测试策略

Agent 服务器涉及 LLM API 调用和 MCP 网络连接，不适合直接运行端到端测试。测试重点是：

| 测试目标 | 方法 | 说明 |
|---------|------|------|
| AgentCard 定义 | 直接导入检查 | 验证名称、URL、技能数量和类型 |
| handle_task 行为 | `unittest.mock` 模拟异步查询函数 | 测试成功/失败/追问三种状态转换 |
| 异常处理 | 模拟抛出异常 | 验证即使出错也返回有效的失败状态 |

### 2 AgentCard 定义测试

验证每个 Agent 的代理卡片信息是否正确配置。

**测试文件**：SmartVoyage/tests/test_agent_servers.py

```python
import unittest
from unittest.mock import MagicMock, patch

from SmartVoyage.a2a_server.weather_server import agent_card as weather_card
from SmartVoyage.a2a_server.ticket_server import agent_card as ticket_card
from SmartVoyage.a2a_server.trip_server import agent_card as trip_card


class TestWeatherAgentCard(unittest.TestCase):
    """测试天气 Agent 的代理卡片定义"""

    def test_agent_card_basic(self):
        """验证代理卡片基本信息"""
        self.assertEqual(weather_card.name, "WeatherQueryAssistant")
        self.assertIn("天气查询", weather_card.description)
        self.assertEqual(weather_card.url, "http://localhost:5005")
        self.assertEqual(len(weather_card.skills), 1)

    def test_weather_skill_examples(self):
        """验证天气技能示例"""
        skill = weather_card.skills[0]
        self.assertIn("weather", skill.name.lower())
        self.assertIsNotNone(skill.description)
        self.assertGreater(len(skill.examples), 0)


class TestTicketAgentCard(unittest.TestCase):
    """测试票务 Agent 的代理卡片定义"""

    def test_agent_card_skills(self):
        """验证票务代理拥有 6 个技能"""
        self.assertEqual(ticket_card.name, "TicketAssistant")
        self.assertEqual(ticket_card.url, "http://localhost:5006")
        self.assertEqual(len(ticket_card.skills), 6)

    def test_has_query_and_order_skills(self):
        """验证同时包含查询和预定技能"""
        skill_names = [s.name for s in ticket_card.skills]
        query_skills = [n for n in skill_names if 'query' in n.lower()]
        order_skills = [n for n in skill_names if 'order' in n.lower()]
        self.assertEqual(len(query_skills), 3, "应该有3个查询技能")
        self.assertEqual(len(order_skills), 3, "应该有3个预定技能")


class TestTripAgentCard(unittest.TestCase):
    """测试行程 Agent 的代理卡片定义"""

    def test_has_all_trip_skills(self):
        """验证包含租车、旅游团、保险的查询和预定技能"""
        skill_names = [s.name.lower() for s in trip_card.skills]
        self.assertTrue(any('car' in n for n in skill_names), "缺少租车技能")
        self.assertTrue(any('tour' in n for n in skill_names), "缺少旅游团技能")
        self.assertTrue(any('insurance' in n for n in skill_names), "缺少保险技能")
```

### 3 handle_task 行为测试（模拟异步查询）

Agent 服务器的 `handle_task` 方法内部调用 `asyncio.run(query_xxx(conversation))`。通过 `patch('asyncio.run', ...)` 直接模拟异步调用的返回值，测试三种场景：成功、需要追问、失败。

```python
class TestWeatherQueryServerHandleTask(unittest.TestCase):
    """测试天气服务器的任务处理逻辑"""

    def test_handle_task_success(self):
        """测试正常查询成功场景"""
        # 模拟查询成功返回
        mock_result = {
            "status": "success",
            "message": "北京2025-07-30天气：晴，25°C ~ 35°C"
        }
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.weather_server import WeatherQueryServer
            server = WeatherQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京明天天气"}}
            task.artifacts = None

            result = server.handle_task(task)

            # 验证任务状态为完成
            self.assertEqual(result.status.state.value, "completed")
            self.assertIsNotNone(result.artifacts)

    def test_handle_task_needs_input(self):
        """测试需要用户追问场景"""
        mock_result = {
            "status": "success",
            "message": "请提供您要查询的城市名称。"
        }
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.weather_server import WeatherQueryServer
            server = WeatherQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "明天天气怎么样"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "input-required")

    def test_handle_task_error(self):
        """测试查询失败场景"""
        mock_result = {"status": "error", "message": "连接超时"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.weather_server import WeatherQueryServer
            server = WeatherQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京天气"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "failed")

    def test_handle_task_exception(self):
        """测试查询函数抛出异常的场景"""
        with patch('asyncio.run', side_effect=Exception("网络异常")):
            from SmartVoyage.a2a_server.weather_server import WeatherQueryServer
            server = WeatherQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京天气"}}

            result = server.handle_task(task)
            # 即使抛出异常，也应返回有效的失败状态
            self.assertEqual(result.status.state.value, "failed")
```

### 4 票务和行程服务器测试

票务和行程服务器的测试结构与天气服务器完全一致，区别仅在于模拟的返回内容和 Server 类名。

```python
class TestTicketQueryServerHandleTask(unittest.TestCase):
    """测试票务服务器的任务处理逻辑"""

    def test_handle_task_success(self):
        """测试正常票务查询成功"""
        mock_result = {"status": "success", "message": "北京到上海: 车次G1，二等座，553元"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.ticket_server import TicketQueryServer
            server = TicketQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京到上海火车票"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "completed")

    def test_handle_task_unknown_status(self):
        """测试返回未知状态码的情况"""
        mock_result = {"status": "unknown"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.ticket_server import TicketQueryServer
            server = TicketQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "查询"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "failed")


class TestTripQueryServerHandleTask(unittest.TestCase):
    """测试行程服务器的任务处理逻辑"""

    def test_handle_task_needs_input(self):
        """测试需要追问场景"""
        mock_result = {"status": "success", "message": "请提供取车城市和日期。"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.trip_server import TripQueryServer
            server = TripQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "我想租车"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "input-required")
```

### 5 运行测试

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
test_has_all_trip_skills (__main__.TestTripAgentCard.test_has_all_trip_skills) ... ok
test_handle_task_success (__main__.TestWeatherQueryServerHandleTask.test_handle_task_success) ... ok
test_handle_task_needs_input (__main__.TestWeatherQueryServerHandleTask.test_handle_task_needs_input) ... ok
test_handle_task_error (__main__.TestWeatherQueryServerHandleTask.test_handle_task_error) ... ok
test_handle_task_exception (__main__.TestWeatherQueryServerHandleTask.test_handle_task_exception) ... ok
test_handle_task_success (__main__.TestTicketQueryServerHandleTask.test_handle_task_success) ... ok
test_handle_task_unknown_status (__main__.TestTicketQueryServerHandleTask.test_handle_task_unknown_status) ... ok
test_handle_task_success (__main__.TestTripQueryServerHandleTask.test_handle_task_success) ... ok
test_handle_task_needs_input (__main__.TestTripQueryServerHandleTask.test_handle_task_needs_input) ... ok
...
----------------------------------------------------------------------
Ran 16 tests in 1.50s

OK
```