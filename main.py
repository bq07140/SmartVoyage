"""
需求：实现SmartVoyage智能旅游助手的核心功能，包括系统初始化、意图识别、代理网络管理和用户交互
思路步骤：
1. 导入必要的模块和库
2. 初始化全局变量（对话历史、代理网络、LLM实例等）
3. 实现系统初始化函数（创建代理网络、配置LLM）
4. 实现意图识别函数（使用LLM识别用户意图）
5. 实现用户输入处理函数（根据意图路由到相应代理或生成内容）
6. 实现代理卡片显示函数（展示代理信息）
7. 实现主函数（初始化系统并进入交互循环）
8. 处理异常情况（JSON解析错误、其他异常）
"""
import asyncio
import json
import uuid
from datetime import datetime
import pytz
import re
from python_a2a import AgentNetwork, TextContent, Message, MessageRole, Task
from langchain_openai import ChatOpenAI

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from SmartVoyage.main_prompts import SmartVoyagePrompts
from SmartVoyage.memory import ConversationMemory

conf = Config()

# 初始化全局变量，用于模拟会话状态
messages = []  # 存储对话历史消息列表，每个元素为字典{"role": "user/assistant", "content": "消息内容"}
agent_network = None  # 代理网络实例
llm = None  # 大语言模型实例
agent_urls = {}  # 存储代理的URL信息字典
conversation_history = ""  # 存储整个对话历史字符串
memory = None  # 对话记忆管理器实例


# 初始化代理网络和相关组件
def initialize_system():
    """
    初始化系统组件，包括代理网络、路由器、LLM和会话状态
    """
    global agent_network, llm, agent_urls, conversation_history, memory
    agent_urls = {
        "WeatherQueryAssistant": "http://localhost:5005",  # 天气代理URL
        "TicketQueryAssistant": "http://localhost:5006",  # 票务代理URL
        "TicketAssistant": "http://localhost:5006" # 统一票务代理
    }
    network = AgentNetwork(name="旅行助手网络")
    network.add("WeatherQueryAssistant", "http://localhost:5005")
    network.add("TicketQueryAssistant", "http://localhost:5006")
    network.add("TicketAssistant", "http://localhost:5006")
    agent_network = network

    llm = ChatOpenAI(
        model=conf.model_name,
        api_key=conf.api_key,
        base_url=conf.base_url,
        temperature=0.1
    )

    conversation_history = ""
    memory = ConversationMemory(short_term_limit=10)

# 意图识别agent
def intent_agent(user_input):
    global llm, memory

    chain = SmartVoyagePrompts.intent_prompt() | llm

    current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')
    intent_response = chain.invoke(
        {"conversation_history": memory.get_short_term_text(),
         "query": user_input,
         "current_date": current_date,
         "user_profile": memory.get_profile_text(),
         "task_context": json.dumps(memory.current_task, ensure_ascii=False)}
    ).content.strip()
    logger.info(f"意图识别原始响应: {intent_response}")

    intent_response = re.sub(r'^```json\s*|\s*```$', '', intent_response).strip()
    logger.info(f"清理后响应: {intent_response}")
    intent_output = json.loads(intent_response)
    intents = intent_output.get("intents", [])
    user_queries = intent_output.get("user_queries", {})
    follow_up_message = intent_output.get("follow_up_message", "")
    logger.info(f"intents: {intents}||user_queries: {user_queries}||follow_up_message: {follow_up_message} ")

    return intents, user_queries, follow_up_message

# 处理用户输入的核心函数
def process_user_input(prompt):
    """
    处理用户输入：识别意图、调用代理、生成响应
    核心逻辑：使用LLM进行意图识别，根据意图路由到相应代理或直接生成内容
    """
    global messages, conversation_history, llm, memory
    # 添加用户消息到记忆
    memory.add_message("user", prompt)
    # 同步到完整历史（用于调试和展示）
    messages.append({"role": "user", "content": prompt})
    conversation_history += f"\nUser: {prompt}"

    print("正在分析您的意图...")
    try:
        # 意图识别过程
        intents, user_queries, follow_up_message = intent_agent(prompt)

        # 根据意图输出生成响应
        if "out_of_scope" in intents:
            response = follow_up_message
        elif follow_up_message != "":
            response = follow_up_message
        else:
            responses = []
            routed_agents = []
            for intent in intents:
                logger.info(f"处理意图：{intent}")
                agent_name = conf.intent[intent]

                if intent == "attraction":
                    chain = SmartVoyagePrompts.attraction_prompt() | llm
                    rec_response = chain.invoke({"query": prompt}).content.strip()
                    responses.append(rec_response)
                elif agent_name:
                    query_str = user_queries.get(intent, {})
                    logger.info(f"{agent_name} 查询：{query_str}")

                    # 提取关键实体到记忆
                    if intent in ["flight", "train", "concert"]:
                        memory.extract_entities(intent, query_str)
                        # 更新任务上下文
                        memory.update_task_context({"type": intent, "query": query_str})

                    agent = agent_network.get_agent(agent_name)
                    # 构建上下文：短期记忆 + 新查询
                    chat_history = memory.get_short_term_text() + f'\nUser: {query_str}'
                    message = Message(content=TextContent(text=chat_history), role=MessageRole.USER)
                    task = Task(id="task-" + str(uuid.uuid4()), message=message.to_dict())
                    raw_response = asyncio.run(agent.send_task_async(task))
                    logger.info(f"{agent_name} 原始响应: {raw_response}")

                    if raw_response.status.state == 'completed':
                        agent_result = raw_response.artifacts[0]['parts'][0]['text']
                    else:
                        agent_result = raw_response.status.message['content']['text']

                    # 根据代理类型总结响应
                    if agent_name == "WeatherQueryAssistant":
                        chain = SmartVoyagePrompts.summarize_weather_prompt() | llm
                        final_response = chain.invoke({"query": query_str, "raw_response": agent_result}).content.strip()
                    elif agent_name == "TicketQueryAssistant":
                        chain = SmartVoyagePrompts.summarize_ticket_prompt() | llm
                        final_response = chain.invoke({"query": query_str, "raw_response": agent_result}).content.strip()
                    else:
                        final_response = agent_result

                    responses.append(final_response)
                    routed_agents.append(agent_name)
                else:
                    responses.append("暂不支持此意图。")

            response = "\n\n".join(responses)
            if routed_agents:
                logger.info(f"路由到代理：{routed_agents}")

        # 添加响应到记忆
        memory.add_message("assistant", response)
        conversation_history += f"\nAssistant: {response}"
        messages.append({"role": "assistant", "content": response})

        # 输出助手响应
        print(f"\n助手回复：\n{response}\n")

    except json.JSONDecodeError as json_err:
        logger.error(f"意图识别JSON解析失败")
        error_message = f"意图识别JSON解析失败：{str(json_err)}。请重试。"
        print(f"\n助手回复：\n{error_message}\n")
        memory.add_message("assistant", error_message)
        messages.append({"role": "assistant", "content": error_message})
    except Exception as e:
        logger.error(f"处理异常: {str(e)}")
        error_message = f"处理失败：{str(e)}。请重试。"
        print(f"\n助手回复：\n{error_message}\n")
        memory.add_message("assistant", error_message)
        messages.append({"role": "assistant", "content": error_message})


# 显示代理卡片信息
def display_agent_cards():
    """
    显示所有代理的卡片信息，包括技能、描述、地址和状态
    """
    print("\n🛠️ Agent Cards:")
    for agent_name in agent_network.agents.keys():
        agent_card = agent_network.get_agent_card(agent_name)
        agent_url = agent_urls.get(agent_name, "未知地址")
        print(f"\n--- Agent: {agent_name} ---")
        print(f"技能: {agent_card.skills}")
        print(f"描述: {agent_card.description}")
        print(f"地址: {agent_url}")
        print(f"状态: 在线")


def display_memory():
    """
    显示当前记忆状态，包括短期对话、用户偏好和任务上下文
    """
    print("\n🧠 Memory State:")
    print(f"--- 短期对话 ({len(memory.short_term_messages)}/{memory.short_term_limit}) ---")
    for msg in memory.short_term_messages[-5:]:
        role_label = "用户" if msg["role"] == "user" else "助手"
        print(f"  [{msg['timestamp']}] {role_label}: {msg['content']}")
    print(f"\n--- 用户偏好 ---")
    if memory.user_profile:
        for k, v in memory.user_profile.items():
            print(f"  {k}: {v}")
    else:
        print("  无")
    print(f"\n--- 当前任务上下文 ---")
    if memory.current_task:
        for k, v in memory.current_task.items():
            print(f"  {k}: {v}")
    else:
        print("  无")
    print(f"\n--- 查询历史 (最近5条) ---")
    for entity in memory.entity_history[-5:]:
        print(f"  [{entity['timestamp']}] {entity['type']}: {entity['query']}")
    if not memory.entity_history:
        print("  无")

# 主函数：脚本入口
# 初始化系统并进入交互循环
if __name__ == "__main__":
    # 初始化系统
    initialize_system()
    print("🤖 基于A2A的SmartVoyage旅行智能助手")
    print("欢迎体验智能对话！输入问题，按回车提交；")
    print("输入'quit'退出；输入'cards'查看代理卡片；输入'memory'查看记忆状态；输入'clear'清空记忆。")

    # 显示初始代理卡片
    display_agent_cards()

    # 交互循环
    while True:
        user_input = input("\n请输入您的问题: ").strip()
        if user_input.lower() == 'quit':
            print("感谢使用SmartVoyage！再见！")
            break
        elif user_input.lower() == 'cards':
            display_agent_cards()
            continue
        elif user_input.lower() == 'memory':
            display_memory()
            continue
        elif user_input.lower() == 'clear':
            memory.clear()
            conversation_history = ""
            messages.clear()
            print("记忆已清空。")
            continue
        elif not user_input:
            continue
        else:
            process_user_input(user_input)

    # 脚本结束时打印页脚信息
    print("\n---")
    print("Powered by 黑马程序员 | 基于Agent2Agent的旅行助手系统 v2.0")