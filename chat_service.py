"""
需求：SmartVoyage核心对话服务，供CLI和API共同调用
"""
import asyncio
import json
import uuid
from datetime import datetime
import pytz
import re
from python_a2a import AgentNetwork, TextContent, Message, MessageRole, Task
from langchain_openai import ChatOpenAI


def _run_async(coro):
    """兼容同步和异步环境：如果已有运行中的事件循环，则创建新线程运行协程"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from SmartVoyage.main_prompts import SmartVoyagePrompts
from SmartVoyage.memory import ConversationMemory

conf = Config()


class ChatService:
    """SmartVoyage对话服务，封装意图识别、路由和代理调用"""

    def __init__(self):
        self.agent_urls = {
            "WeatherQueryAssistant": "http://localhost:5005",
            "TicketQueryAssistant": "http://localhost:5006",
            "TicketAssistant": "http://localhost:5006"
        }
        self.agent_network = AgentNetwork(name="旅行助手网络")
        self.agent_network.add("WeatherQueryAssistant", "http://localhost:5005")
        self.agent_network.add("TicketQueryAssistant", "http://localhost:5006")
        self.agent_network.add("TicketAssistant", "http://localhost:5006")

        self.llm = ChatOpenAI(
            model=conf.model_name,
            api_key=conf.api_key,
            base_url=conf.base_url,
            temperature=0.1
        )

        self.memory = ConversationMemory(short_term_limit=10)
        self.messages = []
        self.conversation_history = ""

    def intent_agent(self, user_input: str):
        """意图识别"""
        chain = SmartVoyagePrompts.intent_prompt() | self.llm
        current_date = datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d')
        intent_response = chain.invoke({
            "conversation_history": self.memory.get_short_term_text(),
            "query": user_input,
            "current_date": current_date,
            "user_profile": self.memory.get_profile_text(),
            "task_context": json.dumps(self.memory.current_task, ensure_ascii=False)
        }).content.strip()
        logger.info(f"意图识别原始响应: {intent_response}")

        intent_response = re.sub(r'^```json\s*|\s*```$', '', intent_response).strip()
        logger.info(f"清理后响应: {intent_response}")
        intent_output = json.loads(intent_response)
        intents = intent_output.get("intents", [])
        user_queries = intent_output.get("user_queries", {})
        follow_up_message = intent_output.get("follow_up_message", "")
        logger.info(f"intents: {intents}||user_queries: {user_queries}||follow_up_message: {follow_up_message} ")

        return intents, user_queries, follow_up_message

    def chat(self, user_input: str) -> str:
        """处理用户输入，返回助手响应"""
        self.memory.add_message("user", user_input)
        self.messages.append({"role": "user", "content": user_input})
        self.conversation_history += f"\nUser: {user_input}"

        try:
            intents, user_queries, follow_up_message = self.intent_agent(user_input)

            if "out_of_scope" in intents:
                response = follow_up_message
            elif follow_up_message != "":
                response = follow_up_message
            else:
                responses = []
                routed_agents = []
                for intent in intents:
                    logger.info(f"处理意图：{intent}")
                    agent_name = conf.intent.get(intent)

                    if intent == "attraction":
                        chain = SmartVoyagePrompts.attraction_prompt() | self.llm
                        rec_response = chain.invoke({"query": user_input}).content.strip()
                        responses.append(rec_response)
                    elif agent_name:
                        query_str = user_queries.get(intent, {})
                        logger.info(f"{agent_name} 查询：{query_str}")

                        if intent in ["flight", "train", "concert"]:
                            self.memory.extract_entities(intent, query_str)
                            self.memory.update_task_context({"type": intent, "query": query_str})

                        agent = self.agent_network.get_agent(agent_name)
                        chat_history = self.memory.get_short_term_text() + f'\nUser: {query_str}'
                        msg = Message(content=TextContent(text=chat_history), role=MessageRole.USER)
                        task = Task(id="task-" + str(uuid.uuid4()), message=msg.to_dict())
                        raw_response = _run_async(agent.send_task_async(task))
                        logger.info(f"{agent_name} 原始响应: {raw_response}")

                        if raw_response.status.state == 'completed':
                            agent_result = raw_response.artifacts[0]['parts'][0]['text']
                        else:
                            agent_result = raw_response.status.message['content']['text']

                        if agent_name == "WeatherQueryAssistant":
                            chain = SmartVoyagePrompts.summarize_weather_prompt() | self.llm
                            final_response = chain.invoke({"query": query_str, "raw_response": agent_result}).content.strip()
                        elif agent_name == "TicketQueryAssistant":
                            chain = SmartVoyagePrompts.summarize_ticket_prompt() | self.llm
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

            self.memory.add_message("assistant", response)
            self.conversation_history += f"\nAssistant: {response}"
            self.messages.append({"role": "assistant", "content": response})
            return response

        except json.JSONDecodeError as e:
            logger.error(f"意图识别JSON解析失败")
            error_message = f"意图识别JSON解析失败：{str(e)}。请重试。"
            self.memory.add_message("assistant", error_message)
            self.messages.append({"role": "assistant", "content": error_message})
            return error_message
        except Exception as e:
            logger.error(f"处理异常: {str(e)}")
            error_message = f"处理失败：{str(e)}。请重试。"
            self.memory.add_message("assistant", error_message)
            self.messages.append({"role": "assistant", "content": error_message})
            return error_message

    def get_agent_cards(self) -> list:
        """获取代理卡片信息"""
        cards = []
        for agent_name in self.agent_network.agents.keys():
            agent_card = self.agent_network.get_agent_card(agent_name)
            agent_url = self.agent_urls.get(agent_name, "未知地址")
            cards.append({
                "name": agent_name,
                "skills": [s.name + ": " + s.description for s in agent_card.skills],
                "description": agent_card.description,
                "url": agent_url,
                "status": "在线"
            })
        return cards

    def get_memory_state(self) -> dict:
        """获取记忆状态"""
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
        """清空记忆"""
        self.memory.clear()
        self.messages.clear()
        self.conversation_history = ""
