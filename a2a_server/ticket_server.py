"""
需求：实现基于A2A的统一票务服务器，处理用户的火车票、机票和演唱会票查询及预订请求
思路步骤：
1. 导入必要的模块和库
2. 初始化LLM实例
3. 实现query_tickets函数（调用MCP参数化工具执行票务查询）
4. 实现order_tickets函数（调用MCP参数化工具执行票务预订）
5. 定义Agent卡片
6. 创建TicketQueryServer类（继承A2AServer）
7. 实现handle_task方法（处理任务、调用查询/预订、格式化结果）
8. 主函数（创建并运行服务器）
"""
import json
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from python_a2a import A2AServer, run_server, AgentCard, AgentSkill, TaskStatus, TaskState
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_tool_calling_agent, AgentExecutor
from datetime import datetime
import pytz

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger

conf = Config()

# 初始化LLM
llm = ChatOpenAI(
    model=conf.model_name,
    base_url=conf.base_url,
    api_key=conf.api_key,
    temperature=conf.temperature
)

# 统一MCP客户端（端口8001，同时支持查询和预定工具）
MCP_URL = "http://127.0.0.1:8001/mcp"


# 定义查询函数
async def query_tickets(conversation: str) -> dict:
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)

                prompt = ChatPromptTemplate.from_messages([
                    ("system", """你是一个票务查询助手，能够调用工具来完成火车票、飞机票或演唱会门票的查询。
你需要仔细分析用户的问题，从问题中提取工具需要的参数，然后调用对应的查询工具。
如果用户提供的信息不足以提取到调用工具所有必要参数，则向用户追问，以获取该信息。不能自己编撰参数。
查询到结果后，请用清晰的中文格式化输出，格式如下：
- 火车票：出发城市 到 到达城市 出发时间: 车次XX，座位类型XX，票价XX元，剩余XX张
- 机票：出发城市 到 到达城市 出发时间: 航班XX，舱位类型XX，票价XX元，剩余XX张
- 演唱会票：城市 开始时间: 艺人XX演唱会，场地XX，票价XX元，剩余XX张
如果未查到数据，请回复"未找到相关票务数据，请确认或修改查询条件。"
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
    except Exception as e:
        logger.error(f"票务 MCP 查询出错：{str(e)}")
        return {"status": "error", "message": f"票务 MCP 查询出错：{str(e)}"}


# 定义预订函数
async def order_tickets(query: str) -> dict:
    try:
        async with streamablehttp_client(MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await load_mcp_tools(session)

                prompt = ChatPromptTemplate.from_messages([
                    ("system",
                     "你是一个票务预定助手，能够调用工具来完成火车票、飞机票或演出票的预定。你需要仔细分析工具需要的参数，然后从用户提供的信息中提取信息。如果用户提供的信息不足以提取到调用工具所有必要参数，则向用户追问，以获取该信息。不能自己编撰参数。"),
                    ("human", "{input}"),
                    ("placeholder", "{agent_scratchpad}"),
                ])

                agent = create_tool_calling_agent(llm, tools, prompt)
                agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

                response = await agent_executor.ainvoke({"input": query})

                return {"status": "success", "message": f"{response['output']}"}
    except Exception as e:
        logger.error(f"票务 MCP 预订出错：{str(e)}")
        return {"status": "error", "message": f"票务 MCP 预订出错：{str(e)}"}


# Agent 卡片定义
agent_card = AgentCard(
    name="TicketAssistant",
    description="基于 LangChain 提供票务查询和预订服务的统一助手",
    url="http://localhost:5006",
    version="2.0.0",
    capabilities={"streaming": True, "memory": True},
    skills=[
        AgentSkill(
            name="execute ticket query",
            description="根据客户端提供的输入执行票务查询，返回数据库结果，支持自然语言输入",
            examples=["火车票 北京 上海 2025-07-31 硬卧", "机票 北京 上海 2025-07-31 经济舱",
                      "演唱会 北京 刀郎 2025-08-23 看台"]
        ),
        AgentSkill(
            name="execute ticket order",
            description="根据客户端提供的输入执行票务预定，返回执行结果",
            examples=["北京 到 上海 2025-11-15 火车票 二等座 1张",
                      "上海 到 北京 2025-12-11 飞机票 公务舱 2张"]
        )
    ]
)


# 票务查询服务器类
class TicketQueryServer(A2AServer):
    def __init__(self):
        super().__init__(agent_card=agent_card)

    # 处理任务：提取输入，调用查询/预订，格式化结果
    def handle_task(self, task):
        # 1 提取输入
        content = (task.message or {}).get("content", {})
        conversation = content.get("text", "") if isinstance(content, dict) else ""
        logger.info(f"用户查询: {conversation}")

        try:
            # 2 调用MCP查询
            query_result = asyncio.run(query_tickets(conversation))
            logger.info(f"MCP 查询返回: {query_result}")

            # 3 结果输出
            if query_result.get("status") == "success":
                result_text = query_result.get("message", "")
                task.artifacts = [{"parts": [{"type": "text", "text": result_text}]}]
                task.status = TaskStatus(state=TaskState.COMPLETED)
            elif query_result.get("status") == "error":
                task.status = TaskStatus(state=TaskState.FAILED,
                                         message={"role": "agent", "content": {"text": query_result.get("message", "查询失败，请重试。")}})
            else:
                task.status = TaskStatus(state=TaskState.FAILED,
                                         message={"role": "agent", "content": {"text": "查询失败，请重试或提供更多细节。"}})
            return task
        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            task.status = TaskStatus(state=TaskState.FAILED,
                                     message={"role": "agent", "content": {"text": f"查询失败: {str(e)} 请重试或提供更多细节。"}})
            return task


if __name__ == "__main__":
    # 创建并运行服务器
    ticket_server = TicketQueryServer()
    # 打印服务器信息
    print("\n=== 服务器信息 ===")
    print(f"名称: {ticket_server.agent_card.name}")
    print(f"描述: {ticket_server.agent_card.description}")
    print("\n技能:")
    for skill in ticket_server.agent_card.skills:
        print(f"- {skill.name}: {skill.description}")
    # 运行服务器
    run_server(ticket_server, host="127.0.0.1", port=5006)
