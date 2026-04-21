"""
需求：SmartVoyage核心对话服务，供CLI和API共同调用

架构说明：
    本模块实现了智能旅游助手的核心对话流程，采用 Planning + ReAct 架构：
    1. 意图识别（intent_agent）：分析用户输入，识别出用户想做什么（查天气/查机票/查景点等）
    2. 任务规划（planning_agent）：判断任务复杂度
       - 简单任务（如只查天气）：直接执行，快速响应
       - 复杂任务（如同时查机票+天气+景点）：生成执行计划
    3. ReAct循环（react_loop）：针对复杂任务，逐步执行每个步骤
       - Thought（思考）：分析当前情况，决定下一步行动
       - Action（行动）：调用对应的 agent 执行查询
       - Observation（观察）：记录执行结果，供后续步骤参考
    4. 结果汇总：所有步骤完成后，生成一条连贯的最终回复

整体流程：
    用户输入 → 意图识别 → 任务规划 → [简单任务直接执行 / 复杂任务进入ReAct循环] → 返回回复

A2A架构说明：
    Agent2Agent (A2A) 是一种代理间通信协议。本系统通过 AgentNetwork 管理多个子代理：
    - WeatherQueryAssistant（天气查询代理）：运行在 localhost:5005
    - TicketAssistant（票务代理）：运行在 localhost:5006
    主助手通过 A2A 协议向子代理发送任务请求，获取查询结果后整合回复用户。
"""

# ==================== 导入依赖 ====================
import asyncio  # 异步IO库，用于并发调用多个 agent（异步网络请求）
import json  # JSON处理，用于解析大模型的JSON输出和序列化数据
import uuid  # 唯一标识符生成，用于给每个任务分配唯一ID
from datetime import datetime  # 时间处理，用于获取当前日期和时间戳
import pytz  # 时区库，用于将时间转换到 Asia/Shanghai 时区
import re  # 正则表达式，用于清理大模型返回的JSON（去掉代码块标记）

# python_a2a 是 A2A 协议的 Python 客户端库
from python_a2a import AgentNetwork, TextContent, Message, MessageRole, Task
# AgentNetwork: 代理网络管理器，负责注册和获取子代理
# TextContent: 消息文本内容包装
# Message: A2A 消息对象，包含角色和内容
# MessageRole: 消息角色枚举（USER/ASSISTANT）
# Task: A2A 任务对象，用于向子代理发送请求

from langchain_openai import ChatOpenAI  # LangChain 的大模型接口，兼容 OpenAI API 格式

# 导入项目内部模块
from SmartVoyage.config import Config  # 配置模块（模型地址、API Key、意图映射等）
from SmartVoyage.create_logger import logger  # 日志模块，用于记录运行日志
from SmartVoyage.main_prompts import SmartVoyagePrompts  # Prompt 模板管理
from SmartVoyage.memory import ConversationMemory  # 记忆管理（短期记忆、用户偏好、任务上下文）

conf = Config()  # 全局配置实例，包含模型参数、意图映射等


class ChatService:
    """
    SmartVoyage 对话服务类

    这是整个系统的核心类，负责处理用户的所有输入并生成回复。
    内部流程为：意图识别 → 任务规划 → 执行（直接/ReAct循环）→ 返回回复

    使用示例：
        service = ChatService()          # 创建服务实例
        response = await service.chat("北京明天天气怎么样？")  # 发送用户输入
        print(response)                  # 打印助手回复
    """

    def __init__(self):
        """
        初始化 ChatService 实例

        完成以下初始化工作：
        1. 配置子代理网络（注册天气代理和票务代理）
        2. 初始化大模型连接
        3. 初始化记忆管理器
        4. 初始化对话历史和工具列表缓存
        """
        # ========== 1. 配置 A2A 代理网络 ==========
        # 记录每个子代理的访问地址
        self.agent_urls = {
            "WeatherQueryAssistant": "http://localhost:5005",  # 天气查询代理的地址
            "TicketAssistant": "http://localhost:5006"         # 票务代理的地址
        }
        # 创建代理网络实例，给它起个名字
        self.agent_network = AgentNetwork(name="旅行助手网络")
        # 向网络中注册两个子代理（名字 + 地址）
        self.agent_network.add("WeatherQueryAssistant", "http://localhost:5005")
        self.agent_network.add("TicketAssistant", "http://localhost:5006")

        # ========== 2. 初始化大模型连接 ==========
        # 使用 LangChain 的 ChatOpenAI 接口连接大模型
        # SiliconFlow 兼容 OpenAI API 格式，所以可以用 ChatOpenAI
        self.llm = ChatOpenAI(
            model=conf.model_name,     # 模型名称，如 "Qwen/Qwen2.5-72B-Instruct"
            api_key=conf.api_key,      # API 密钥
            base_url=conf.base_url,    # API 基础地址
            temperature=0.1            # 温度参数：越小输出越确定、越稳定（0.1表示比较严谨）
        )

        # ========== 3. 初始化记忆和对话状态 ==========
        self.memory = ConversationMemory(short_term_limit=10)  # 短期记忆最多保留10条对话
        self.messages = []           # 对话消息列表，格式：[{"role": "user/assistant", "content": "..."}]
        self.conversation_history = ""  # 完整对话历史字符串，用于调试和展示
        self._available_tools_text = "" # 缓存的可用工具列表文本，避免重复查询

    def get_available_tools(self) -> str:
        """
        从 A2A 代理网络中动态获取可用工具列表，格式化为文本

        这是 ReAct 架构的关键：告诉大模型当前有哪些工具/agent 可以调用，
        让大模型在推理时从这些工具中选择合适的。

        优势：当新增或删除 agent 时，无需修改 prompt 模板，系统自动感知变化。

        返回值：
            str: 格式化的工具列表文本，例如：
                - Agent: WeatherQueryAssistant, 查询天气信息
                  Skill: 天气查询: 根据城市查询天气
                - Agent: TicketQueryAssistant, 查询票务信息
                  Skill: 票务查询: 查询机票、火车票等
        """
        try:
            # 调用 get_agent_cards() 获取所有在线 agent 的卡片信息
            cards = self.get_agent_cards()
            lines = []
            for card in cards:
                # 每个 agent 一行，包含名称和描述
                lines.append(f"- Agent: {card['name']}, {card['description']}")
                # 每个 agent 的技能作为子项列出
                for skill in card['skills']:
                    lines.append(f"  Skill: {skill}")
            self._available_tools_text = "\n".join(lines)
        except Exception as e:
            # 如果 agent server 没有启动或网络不通，使用默认的工具列表作为降级方案
            logger.warning(f"获取可用工具列表失败: {e}")
            self._available_tools_text = (
                "- WeatherQueryAssistant: 查询天气信息\n"
                "- TicketQueryAssistant: 查询票务信息（机票/高铁票/演唱会票）\n"
                "- TicketAssistant: 预定票务"
            )
        return self._available_tools_text

    def intent_agent(self, user_input: str):
        """
        意图识别：分析用户输入，判断用户想做什么

        工作流程：
        1. 将用户输入 + 对话历史 + 用户偏好等上下文组装后发送给大模型
        2. 大模型返回 JSON，包含识别到的意图列表、改写后的查询、追问消息

        支持的意图类型：
            - weather: 天气查询
            - flight: 机票查询
            - train: 高铁/火车票查询
            - concert: 演唱会票查询
            - order: 票务预定
            - attraction: 景点推荐
            - out_of_scope: 超出系统能力范围

        参数：
            user_input (str): 用户本次的输入内容

        返回值：
            tuple: (intents, user_queries, follow_up_message)
                - intents (list): 识别到的意图列表，如 ["weather", "flight"]
                - user_queries (dict): 改写后的查询，如 {"weather": "北京明天天气", "flight": "北京到上海机票"}
                - follow_up_message (str): 追问消息，当意图不明确时用于追问用户
        """
        # 组装 Prompt 模板 + 大模型，形成完整的处理链
        chain = SmartVoyagePrompts.intent_prompt() | self.llm

        # 获取当前日期，注入到 prompt 中（用户可能说"明天"、"后天"等相对时间）
        current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')

        # 调用大模型，传入所有上下文信息
        intent_response = chain.invoke({
            "conversation_history": self.memory.get_short_term_text(),  # 短期对话历史
            "query": user_input,          # 用户本次输入
            "current_date": current_date, # 当前日期
            "user_profile": self.memory.get_profile_text(),  # 用户偏好（如"二等座"、"经济舱"）
            "task_context": json.dumps(self.memory.current_task, ensure_ascii=False)  # 当前任务上下文
        }).content.strip()  # .content 获取文本内容，.strip() 去掉首尾空白
        logger.info(f"意图识别原始响应: {intent_response}")

        # 大模型有时会返回 ```json ... ``` 格式的代码块，需要用正则去掉
        intent_response = re.sub(r'^```json\s*|\s*```$', '', intent_response).strip()
        logger.info(f"清理后响应: {intent_response}")

        # 将 JSON 字符串解析为 Python 字典
        intent_output = json.loads(intent_response)
        intents = intent_output.get("intents", [])  # 意图列表，如 ["weather", "flight"]
        user_queries = intent_output.get("user_queries", {})  # 改写后的查询字典
        follow_up_message = intent_output.get("follow_up_message", "")  # 追问消息
        logger.info(f"intents: {intents}||user_queries: {user_queries}||follow_up_message: {follow_up_message} ")

        return intents, user_queries, follow_up_message

    def planning_agent(self, intents: list, user_queries: dict) -> dict:
        """
        任务规划：判断任务复杂度，决定是直接执行还是需要多步计划

        这是 Planning + ReAct 架构的核心——"大脑"：
        - 简单任务（如只查天气）：单意图、无需多步推理，直接执行即可
        - 复杂任务（如"查机票+天气+景点"）：多意图、需要分步处理，生成执行计划

        参数：
            intents (list): 识别到的意图列表，如 ["weather", "flight", "attraction"]
            user_queries (dict): 改写后的查询字典，如 {"weather": "...", "flight": "..."}

        返回值：
            dict: 规划结果，格式为：
                - 简单任务：{"need_plan": false, "reason": "单意图，直接查询即可", "steps": []}
                - 复杂任务：{"need_plan": true, "reason": "多意图需要分步执行",
                            "steps": [{"step": 1, "action": "...", "intent": "weather", "depends_on": 0}, ...]}
        """
        # 组装规划 Prompt + 大模型
        chain = SmartVoyagePrompts.planning_prompt() | self.llm

        # 调用大模型进行规划
        planning_response = chain.invoke({
            "conversation_history": self.memory.get_short_term_text(),  # 对话历史，帮助理解上下文
            "query": self.messages[-1]["content"] if self.messages else "",  # 用户最新的输入
            "intents": json.dumps(intents, ensure_ascii=False),  # 识别到的意图
            "user_queries": json.dumps(user_queries, ensure_ascii=False)  # 改写后的查询
        }).content.strip()
        logger.info(f"规划响应: {planning_response}")

        # 清理可能存在的代码块标记
        planning_response = re.sub(r'^```json\s*|\s*```$', '', planning_response).strip()
        plan = json.loads(planning_response)
        return plan

    async def _call_agent_intent(self, intent: str, query_str: str) -> str:
        """
        统一调用 agent 的底层逻辑

        这是一个私有方法（以 _ 开头），不直接对外暴露，供以下两个场景复用：
        1. 简单任务直接执行时（chat 方法的 else 分支）
        2. ReAct 循环中的步骤执行时（execute_step 方法）

        这样做的好处：避免了代码重复，修改 agent 调用逻辑时只需改一处。

        处理逻辑说明：
        - 如果是景点推荐（attraction），直接让大模型生成，不需要调子代理
        - 如果是其他意图，通过 A2A 协议调用对应的子代理
        - 收到结果后，根据代理类型做适当的总结（天气/票务需要总结，其他直接返回）

        参数：
            intent (str): 意图类型，如 "weather"、"flight"、"attraction"
            query_str (str): 查询内容，如 "北京明天天气"

        返回值：
            str: agent 返回的结果文本
        """
        # 根据意图类型，从配置中查找对应的 agent 名称
        agent_name = conf.intent.get(intent)

        # ========== 情况1：景点推荐 ===========
        # 景点推荐不需要调用子代理，直接让大模型生成推荐内容
        if intent == "attraction":
            chain = SmartVoyagePrompts.attraction_prompt() | self.llm
            return chain.invoke({"query": query_str}).content.strip()

        # ========== 情况2：需要调用子代理 ===========
        elif agent_name:
            # 对于票务查询类意图，提取关键实体（城市、日期等）保存到记忆中
            # 这样后续对话可以引用这些实体（多轮对话场景）
            if intent in ["flight", "train", "concert"]:
                self.memory.extract_entities(intent, query_str)
                self.memory.update_task_context({"type": intent, "query": query_str})

            # 从代理网络中获取对应的 agent 实例
            agent = self.agent_network.get_agent(agent_name)
            if agent is None:
                # agent 未注册或不可用时，返回友好提示
                logger.warning(f"未找到代理：{agent_name}")
                return f"抱歉，{agent_name} 暂时不可用，请稍后重试。"

            # 构建发送给 agent 的消息：包含短期记忆 + 本次查询
            chat_history = self.memory.get_short_term_text() + f'\nUser: {query_str}'
            msg = Message(content=TextContent(text=chat_history), role=MessageRole.USER)
            task = Task(id="task-" + str(uuid.uuid4()), message=msg.to_dict())

            # 通过 A2A 协议异步发送任务给子代理
            # run_in_executor 将异步操作放到线程池中执行（避免阻塞事件循环）
            try:
                raw_response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: asyncio.run(agent.send_task_async(task))
                )
                logger.info(f"{agent_name} 原始响应: {raw_response}")

                # 解析 agent 返回结果
                if raw_response.status.state == 'completed' and raw_response.artifacts:
                    # 任务成功完成，从 artifacts 中提取结果文本
                    agent_result = raw_response.artifacts[0]['parts'][0]['text']
                elif raw_response.status.message:
                    # 有消息但可能失败了，尝试从消息中提取文本
                    agent_result = raw_response.status.message.get('content', {}).get('text', str(raw_response.status.message))
                else:
                    agent_result = f"查询失败：{raw_response.status.message or '未知错误'}"
            except Exception as e:
                # agent 服务不可用（如网络不通、服务崩溃），返回友好错误提示
                logger.error(f"{agent_name} 调用异常: {str(e)}")
                return f"{agent_name} 服务暂时不可用：{str(e)}"

            # 根据代理类型，使用对应的总结 prompt 处理结果
            if agent_name == "WeatherQueryAssistant":
                chain = SmartVoyagePrompts.summarize_weather_prompt() | self.llm
                return chain.invoke({"query": query_str, "raw_response": agent_result}).content.strip()
            elif agent_name == "TicketQueryAssistant":
                chain = SmartVoyagePrompts.summarize_ticket_prompt() | self.llm
                return chain.invoke({"query": query_str, "raw_response": agent_result}).content.strip()
            else:
                # 其他代理直接返回原始结果
                return agent_result

        # ========== 情况3：未识别的意图 ===========
        else:
            return "暂不支持此意图。"

    async def execute_step(self, step: dict, user_queries: dict) -> str:
        """
        执行单个计划步骤（供 ReAct 循环调用）

        这个方法负责将规划中的单个步骤转化为实际的执行：
        1. 从步骤中提取意图和查询内容
        2. 调用 _call_agent_intent 执行实际的查询

        参数：
            step (dict): 计划中的单个步骤，格式如：
                {"step": 1, "action": "查询天气", "intent": "weather", "depends_on": 0}
            user_queries (dict): 改写后的查询字典

        返回值：
            str: 该步骤的执行结果
        """
        intent = step.get("intent", "")
        # 优先从 user_queries 中获取改写后的查询，如果没有则用用户原始输入
        query_str = user_queries.get(intent, self.messages[-1]["content"] if self.messages else "")
        logger.info(f"执行步骤：{step.get('action', '')}，意图：{intent}")

        # 委托给统一的 agent 调用方法
        return await self._call_agent_intent(intent, query_str)

    async def react_loop(self, steps: list, user_queries: dict) -> str:
        """
        ReAct 循环：按规划步骤逐步执行，每步先推理再执行

        ReAct（Reasoning + Acting）是一种智能的逐步推理模式：
        1. Thought（思考）：分析当前情况，决定下一步做什么
        2. Action（行动）：根据思考结果调用对应的工具/agent
        3. Observation（观察）：记录行动结果，作为下一步推理的依据

        这种模式的好处：
        - 让大模型有"思考"的过程，更智能
        - 每步都能看到上一步的结果，可以调整后续策略
        - 当某步失败时，模型可以灵活应对（如跳过或重试）

        参数：
            steps (list): 计划步骤列表，如：
                [{"step": 1, "action": "...", "intent": "flight", "depends_on": 0},
                 {"step": 2, "action": "...", "intent": "weather", "depends_on": 0}]
            user_queries (dict): 改写后的查询字典

        返回值：
            str: 综合所有步骤结果的最终回复
        """
        observations = []     # 存储每个步骤的执行结果（Observation）
        step_results = []     # 存储每个步骤的详细结果（步骤号、描述、结果）
        available_tools = self.get_available_tools()  # 获取当前可用的工具/agent 列表

        # 遍历每个计划步骤，逐步执行
        for step in steps:
            step_num = step.get("step", 0)
            # 优先取 description，没有则取 action 作为步骤描述
            step_desc = step.get("description", step.get("action", ""))

            # ========== Thought（思考）阶段 ==========
            # 调用大模型进行推理：根据当前计划、已有结果、步骤描述，思考应该做什么
            react_chain = SmartVoyagePrompts.react_prompt() | self.llm
            thought_response = react_chain.invoke({
                "available_tools": available_tools,  # 当前可用的工具/agent 列表（动态获取）
                "plan_steps": json.dumps(steps, ensure_ascii=False, indent=2),  # 完整计划
                "observations": "\n".join([f"步骤{i}: {obs}" for i, obs in enumerate(observations, 1)]) if observations else "无",  # 已完成步骤的结果
                "current_step": f"步骤 {step_num}",  # 当前步骤号
                "step_description": step_desc,        # 当前步骤描述
                "query": self.messages[-1]["content"] if self.messages else ""  # 用户原始查询
            }).content.strip()

            logger.info(f"ReAct 步骤 {step_num} 推理: {thought_response}")

            # ========== Action（行动）阶段 ==========
            # 执行对应的 agent 调用
            result = await self.execute_step(step, user_queries)

            # ========== Observation（观察）阶段 ==========
            # 记录执行结果
            observations.append(result)
            step_results.append({"step": step_num, "description": step_desc, "result": result})
            logger.info(f"ReAct 步骤 {step_num} 结果: {result[:100]}...")

        # ========== 所有步骤完成后的最终汇总 ==========
        if len(step_results) > 1:
            # 多个步骤：使用专门的汇总 prompt，将各步骤结果整合成一条连贯回复
            summary_chain = SmartVoyagePrompts.react_summary_prompt() | self.llm
            all_obs = "\n".join([f"步骤{s['step']} ({s['description']}): {s['result']}" for s in step_results])
            final_response = summary_chain.invoke({
                "query": self.messages[-1]["content"] if self.messages else "",
                "all_observations": all_obs
            }).content.strip()
        else:
            # 只有一个步骤：直接使用该步骤的结果
            final_response = observations[0] if observations else "暂无结果"

        return final_response

    async def chat(self, user_input: str) -> str:
        """
        处理用户输入的主方法 —— 整个对话流程的入口

        完整流程：
        1. 记录用户消息到记忆中
        2. 意图识别：判断用户想做什么
        3. 处理特殊情况（超出范围 / 需要追问）
        4. 任务规划：判断是简单任务还是复杂任务
        5. 执行：简单任务直接执行，复杂任务进入 ReAct 循环
        6. 记录回复到记忆中
        7. 返回最终回复

        参数：
            user_input (str): 用户的输入内容

        返回值：
            str: 助手的回复内容

        使用示例：
            service = ChatService()
            response = await service.chat("北京明天天气怎么样？")
        """
        # 将用户消息保存到记忆中（短期记忆、对话历史等）
        self.memory.add_message("user", user_input)
        self.messages.append({"role": "user", "content": user_input})
        self.conversation_history += f"\nUser: {user_input}"

        try:
            # ========== 步骤1：意图识别 ==========
            intents, user_queries, follow_up_message = self.intent_agent(user_input)

            # ========== 步骤2：处理特殊情况 ==========
            if "out_of_scope" in intents:
                # 用户输入超出系统能力范围，返回追问消息
                response = follow_up_message
            elif follow_up_message != "":
                # 意图不明确，需要追问用户
                response = follow_up_message
            else:
                # ========== 步骤3：任务规划 ==========
                # 判断任务复杂度，决定执行策略
                plan = self.planning_agent(intents, user_queries)
                need_plan = plan.get("need_plan", False)
                logger.info(f"规划结果: need_plan={need_plan}, reason={plan.get('reason', '')}")

                if need_plan:
                    # ========== 复杂任务：进入 ReAct 循环 ==========
                    # 获取计划步骤，逐步执行
                    steps = plan.get("steps", [])
                    response = await self.react_loop(steps, user_queries)
                else:
                    # ========== 简单任务：直接执行 ==========
                    # 对每个识别到的意图，直接调用对应的处理逻辑
                    responses = []    # 收集每个意图的回复
                    routed_agents = []  # 记录路由到了哪些 agent

                    for intent in intents:
                        logger.info(f"处理意图：{intent}")
                        # 获取改写后的查询内容
                        query_str = user_queries.get(intent, {})
                        # 统一调用 agent 处理
                        result = await self._call_agent_intent(intent, query_str)
                        responses.append(result)
                        # 记录路由到的 agent（景点推荐不走 agent，不记录）
                        if intent != "attraction" and conf.intent.get(intent):
                            routed_agents.append(conf.intent[intent])

                    # 将多个意图的回复用空行拼接成最终回复
                    response = "\n\n".join(responses)
                    if routed_agents:
                        logger.info(f"路由到代理：{routed_agents}")

            # 将助手回复也保存到记忆中
            self.memory.add_message("assistant", response)
            self.conversation_history += f"\nAssistant: {response}"
            self.messages.append({"role": "assistant", "content": response})
            return response

        except json.JSONDecodeError as e:
            # 大模型返回的不是有效 JSON（虽然概率低，但偶尔会发生）
            logger.error(f"意图识别JSON解析失败")
            error_message = f"意图识别JSON解析失败：{str(e)}。请重试。"
            self.memory.add_message("assistant", error_message)
            self.messages.append({"role": "assistant", "content": error_message})
            return error_message
        except Exception as e:
            # 捕获其他所有异常（网络错误、服务不可用等）
            logger.error(f"处理异常: {str(e)}")
            error_message = f"处理失败：{str(e)}。请重试。"
            self.memory.add_message("assistant", error_message)
            self.messages.append({"role": "assistant", "content": error_message})
            return error_message

    def get_agent_cards(self) -> list:
        """
        获取所有代理的卡片信息

        Agent Card 是 A2A 协议中描述一个 agent 能力的方式，
        包含：名称、技能列表、描述、地址、状态等。

        返回值：
            list: 代理卡片列表，每个卡片格式为：
                {
                    "name": "WeatherQueryAssistant",
                    "skills": ["天气查询: 根据城市查询天气"],
                    "description": "天气查询助手",
                    "url": "http://localhost:5005",
                    "status": "在线"
                }
        """
        cards = []
        for agent_name in self.agent_network.agents.keys():
            agent_card = self.agent_network.get_agent_card(agent_name)
            agent_url = self.agent_urls.get(agent_name, "未知地址")
            cards.append({
                "name": agent_name,
                # 将每个技能对象格式化为 "技能名: 技能描述" 的字符串
                "skills": [s.name + ": " + s.description for s in agent_card.skills],
                "description": agent_card.description,
                "url": agent_url,
                "status": "在线"
            })
        return cards

    def get_memory_state(self) -> dict:
        """
        获取当前记忆状态的摘要信息（用于调试和展示）

        返回值：
            dict: 包含以下信息：
                - short_term_messages: 最近5条对话消息
                - user_profile: 用户偏好
                - current_task: 当前任务上下文
                - entity_history: 最近5条实体记录
        """
        return {
            "short_term_messages": [
                {"role": "用户" if m["role"] == "user" else "助手", "content": m["content"], "timestamp": m["timestamp"]}
                for m in self.memory.short_term_messages[-5:]
            ],
            "user_profile": self.memory.user_profile,
            "current_task": self.memory.current_task,
            "entity_history": self.memory.entity_history[-5:]
        }

    def clear_memory(self):
        """
        清空所有记忆（短期记忆、用户偏好、任务上下文等）

        用户输入 'clear' 命令时调用，用于重置对话状态。
        """
        self.memory.clear()
        self.messages.clear()
        self.conversation_history = ""
