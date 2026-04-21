"""
需求：实现SmartVoyage的记忆管理模块，包括短期对话记忆、用户偏好记忆和当前任务上下文
"""
from datetime import datetime
import pytz


class ConversationMemory:
    """管理对话记忆的类，包括短期对话、用户偏好和任务上下文"""

    def __init__(self, short_term_limit: int = 10):
        self.short_term_messages = []  # 短期对话消息列表，最多保留short_term_limit条
        self.user_profile = {}  # 用户偏好，如 {"seat_type": "二等座", "cabin_type": "经济舱"}
        self.current_task = {}  # 当前任务上下文，如 {"type": "train", "departure_city": "北京", "arrival_city": "上海"}
        self.short_term_limit = short_term_limit  # 短期记忆最大长度
        self.entity_history = []  # 历史提取的关键实体

    def add_message(self, role: str, content: str):
        """添加消息到短期记忆"""
        self.short_term_messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%H:%M:%S')
        })
        # 超过限制则移除最旧的消息
        if len(self.short_term_messages) > self.short_term_limit:
            self.short_term_messages = self.short_term_messages[-self.short_term_limit:]

    def get_short_term_text(self) -> str:
        """获取短期对话的文本格式，用于意图识别和代理调用"""
        lines = []
        for msg in self.short_term_messages:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role_label}: {msg['content']}")
        return '\n'.join(lines)

    def update_profile(self, profile_update: dict):
        """更新用户偏好"""
        self.user_profile.update(profile_update)

    def update_task_context(self, task_update: dict):
        """更新当前任务上下文"""
        self.current_task.update(task_update)

    def extract_entities(self, intent_type: str, query: str):
        """从查询中提取关键实体到历史"""
        self.entity_history.append({
            "type": intent_type,
            "query": query,
            "timestamp": datetime.now(pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
        })
        # 只保留最近20条实体记录
        if len(self.entity_history) > 20:
            self.entity_history = self.entity_history[-20:]

    def get_profile_text(self) -> str:
        """获取用户偏好的文本描述，用于注入到prompt中"""
        if not self.user_profile:
            return "无已知的用户偏好"
        items = [f"{k}: {v}" for k, v in self.user_profile.items()]
        return "，".join(items)

    def clear(self):
        """清空所有记忆"""
        self.short_term_messages = []
        self.user_profile = {}
        self.current_task = {}
        self.entity_history = []

    def to_dict(self) -> dict:
        """导出记忆为字典，用于序列化"""
        return {
            "short_term_messages": self.short_term_messages,
            "user_profile": self.user_profile,
            "current_task": self.current_task,
            "entity_history": self.entity_history[-5:]  # 只导出最近5条
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ConversationMemory':
        """从字典导入记忆"""
        memory = cls()
        memory.short_term_messages = data.get("short_term_messages", [])
        memory.user_profile = data.get("user_profile", {})
        memory.current_task = data.get("current_task", {})
        memory.entity_history = data.get("entity_history", [])
        return memory
