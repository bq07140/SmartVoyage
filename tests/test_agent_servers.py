"""
Agent 服务器测试模块

测试内容：
1. AgentCard 定义验证（名称、技能、URL）
2. Server handle_task 方法的行为测试（成功/失败/追问场景）
3. 任务状态转换逻辑验证

运行方式：
    cd SmartVoyage
    python -m tests.test_agent_servers
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# 确保能导入 SmartVoyage 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ==================== 1. AgentCard 定义测试 ====================

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
        self.assertIsNotNone(skill.examples)
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


# ==================== 2. handle_task 状态转换测试 ====================
# 思路：模拟 asyncio.run 的返回值（即异步函数的查询结果）
# 验证 handle_task 如何根据返回的 dict 设置不同的任务状态


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


class TestTicketQueryServerHandleTask(unittest.TestCase):
    """测试票务服务器的任务处理逻辑"""

    def test_handle_task_success(self):
        """测试正常票务查询成功"""
        mock_result = {
            "status": "success",
            "message": "北京 到 上海: 车次G1，二等座，553元"
        }
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.ticket_server import TicketQueryServer
            server = TicketQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京到上海火车票"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "completed")

    def test_handle_task_error(self):
        """测试票务查询失败"""
        mock_result = {"status": "error", "message": "查询出错"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.ticket_server import TicketQueryServer
            server = TicketQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "查询火车票"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "failed")

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

    def test_handle_task_success(self):
        """测试正常行程查询成功"""
        mock_result = {
            "status": "success",
            "message": "北京 到 上海: 车型SUV，价格300元/天"
        }
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.trip_server import TripQueryServer
            server = TripQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "北京租车到上海"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "completed")

    def test_handle_task_needs_input(self):
        """测试需要追问场景"""
        mock_result = {
            "status": "success",
            "message": "请提供取车城市和日期。"
        }
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.trip_server import TripQueryServer
            server = TripQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "我想租车"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "input-required")

    def test_handle_task_error(self):
        """测试行程查询失败"""
        mock_result = {"status": "error", "message": "查询出错"}
        with patch('asyncio.run', return_value=mock_result):
            from SmartVoyage.a2a_server.trip_server import TripQueryServer
            server = TripQueryServer()
            task = MagicMock()
            task.message = {"content": {"text": "租车"}}

            result = server.handle_task(task)
            self.assertEqual(result.status.state.value, "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
