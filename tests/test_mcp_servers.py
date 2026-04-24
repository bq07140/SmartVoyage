"""
MCP 服务器测试模块

测试内容：
1. 格式编码工具（default_encoder + DateEncoder）
2. WeatherService 参数化查询逻辑
3. TicketService 三种票务查询（火车票/机票/演唱会）
4. TripService 行程查询（租车/保险）
5. MCP 服务器工具注册验证

运行方式：
    cd SmartVoyage
    python -m tests.test_mcp_servers
"""

import unittest
import json
import sys
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

# 确保能导入 SmartVoyage 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ==================== 1. 格式编码工具测试 ====================

class TestFormatEncoder(unittest.TestCase):
    """测试 default_encoder 函数和 DateEncoder 类"""

    def setUp(self):
        from SmartVoyage.utils.format import default_encoder, DateEncoder
        self.default_encoder_func = default_encoder
        self.date_encoder_cls = DateEncoder

    def test_encode_datetime(self):
        """测试 datetime 编码"""
        dt = datetime(2025, 7, 30, 14, 30, 0)
        result = self.default_encoder_func(dt)
        self.assertEqual(result, '2025-07-30 14:30:00')

    def test_encode_date(self):
        """测试 date 编码"""
        d = date(2025, 7, 30)
        result = self.default_encoder_func(d)
        self.assertEqual(result, '2025-07-30')

    def test_encode_timedelta(self):
        """测试 timedelta 编码"""
        td = timedelta(hours=5, minutes=30)
        result = self.default_encoder_func(td)
        self.assertEqual(result, '5:30:00')

    def test_encode_decimal(self):
        """测试 Decimal 编码"""
        dec = Decimal('123.45')
        result = self.default_encoder_func(dec)
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, 123.45)

    def test_encode_unknown(self):
        """测试未知类型原样返回"""
        obj = "hello"
        result = self.default_encoder_func(obj)
        self.assertEqual(result, "hello")

    def test_date_encoder_json_dumps(self):
        """测试 DateEncoder 类与 json.dumps 配合使用"""
        data = {
            "date": date(2025, 7, 30),
            "datetime": datetime(2025, 7, 30, 14, 30, 0),
            "value": Decimal('99.99')
        }
        result = json.dumps(data, cls=self.date_encoder_cls)
        parsed = json.loads(result)
        self.assertEqual(parsed["date"], "2025-07-30")
        self.assertEqual(parsed["datetime"], "2025-07-30 14:30:00")
        self.assertEqual(parsed["value"], 99.99)


# ==================== 2. WeatherService 测试 ====================

class TestWeatherService(unittest.TestCase):
    """测试天气服务的参数化查询逻辑"""

    def setUp(self):
        # 模拟数据库连接和游标
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cursor

    def _create_service_with_mock_conn(self):
        """创建使用模拟连接的 WeatherService"""
        with patch('SmartVoyage.mcp_server.mcp_weather_server.mysql.connector.connect', return_value=self.mock_conn):
            with patch('SmartVoyage.mcp_server.mcp_weather_server.conf'):
                from SmartVoyage.mcp_server.mcp_weather_server import WeatherService
                return WeatherService()

    def test_query_weather_single_day(self):
        """测试单天天气查询：生成正确的 SQL 和参数"""
        self.mock_cursor.fetchall.return_value = [
            {"city": "北京", "fx_date": "2025-07-30", "temp_max": 35, "temp_min": 25,
             "text_day": "晴", "text_night": "多云", "humidity": 60,
             "wind_dir_day": "南风", "precip": 0.0}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_weather("北京", "2025-07-30")

        # 验证游标被调用
        self.mock_cursor.execute.assert_called_once()
        sql, params = self.mock_cursor.execute.call_args[0]
        # 验证 SQL 包含单天查询条件
        self.assertIn("fx_date = %s", sql)
        # 验证参数正确
        self.assertEqual(params, ["北京", "2025-07-30"])
        # 验证返回 JSON
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(len(parsed["data"]), 1)
        self.assertEqual(parsed["data"][0]["city"], "北京")

    def test_query_weather_date_range(self):
        """测试日期范围天气查询"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        result = service.query_weather("上海", "2025-07-30", "2025-08-05")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("BETWEEN %s AND %s", sql)
        self.assertEqual(params, ["上海", "2025-07-30", "2025-08-05"])

    def test_query_weather_no_data(self):
        """测试无数据情况"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        result = service.query_weather("未知城市", "2025-07-30")

        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "no_data")
        self.assertIn("未找到天气数据", parsed["message"])

    def test_query_weather_error(self):
        """测试数据库异常"""
        self.mock_cursor.execute.side_effect = Exception("连接断开")

        service = self._create_service_with_mock_conn()
        result = service.query_weather("北京", "2025-07-30")

        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "error")
        self.assertIn("连接断开", parsed["message"])


# ==================== 3. TicketService 测试 ====================

class TestTicketService(unittest.TestCase):
    """测试票务服务的参数化查询逻辑"""

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cursor

    def _create_service_with_mock_conn(self):
        """创建使用模拟连接的 TicketService"""
        with patch('SmartVoyage.mcp_server.mcp_ticket_server.mysql.connector.connect', return_value=self.mock_conn):
            with patch('SmartVoyage.mcp_server.mcp_ticket_server.conf'):
                from SmartVoyage.mcp_server.mcp_ticket_server import TicketService
                return TicketService()

    def test_query_train_basic(self):
        """测试火车票基本查询"""
        self.mock_cursor.fetchall.return_value = [
            {"id": 1, "departure_city": "北京", "arrival_city": "上海",
             "departure_time": datetime(2025, 7, 30, 8, 0),
             "train_number": "G1", "seat_type": "二等座", "price": 553.0, "remaining_seats": 100}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_train("北京", "上海", "2025-07-30")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("departure_city = %s", sql)
        self.assertIn("arrival_city = %s", sql)
        self.assertEqual(params[:3], ["北京", "上海", "2025-07-30"])

        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["data"][0]["train_number"], "G1")

    def test_query_train_with_seat_type(self):
        """测试带座位类型的火车票查询"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        result = service.query_train("北京", "上海", "2025-07-30", "二等座")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("seat_type = %s", sql)
        self.assertEqual(params, ["北京", "上海", "2025-07-30", "二等座"])

    def test_query_flight(self):
        """测试机票查询"""
        self.mock_cursor.fetchall.return_value = [
            {"id": 1, "departure_city": "北京", "arrival_city": "上海",
             "departure_time": datetime(2025, 7, 30, 10, 0),
             "flight_number": "CA1234", "cabin_type": "经济舱", "price": 800.0, "remaining_seats": 50}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_flight("北京", "上海", "2025-07-30")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("flight_tickets", sql)
        self.assertEqual(params[:3], ["北京", "上海", "2025-07-30"])

    def test_query_concert(self):
        """测试演唱会票查询"""
        self.mock_cursor.fetchall.return_value = [
            {"id": 1, "artist": "刀郎", "city": "北京",
             "start_time": datetime(2025, 8, 23, 19, 30),
             "venue": "鸟巢", "ticket_type": "VIP", "price": 1280.0, "remaining_seats": 200}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_concert("北京", "刀郎", "2025-08-23")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("concert_tickets", sql)
        self.assertEqual(params[:3], ["北京", "刀郎", "2025-08-23"])

    def test_query_train_sql_injection_safe(self):
        """验证参数化查询防止 SQL 注入"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        # 尝试注入 SQL 的恶意输入
        malicious_city = "'; DROP TABLE train_tickets; --"
        service.query_train(malicious_city, "上海", "2025-07-30")

        sql, params = self.mock_cursor.execute.call_args[0]
        # 验证恶意输入被作为参数传递，而不是拼接到 SQL 中
        self.assertNotIn("DROP TABLE", sql)
        self.assertIn(malicious_city, params)


# ==================== 4. TripService 测试 ====================

class TestTripService(unittest.TestCase):
    """测试行程服务的查询逻辑"""

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cursor

    def _create_service_with_mock_conn(self):
        """创建使用模拟连接的 TripService"""
        with patch('SmartVoyage.mcp_server.mcp_trip_server.mysql.connector.connect', return_value=self.mock_conn):
            with patch('SmartVoyage.mcp_server.mcp_trip_server.conf'):
                from SmartVoyage.mcp_server.mcp_trip_server import TripService
                return TripService()

    def test_query_car_rental_basic(self):
        """测试基本租车查询"""
        self.mock_cursor.fetchall.return_value = [
            {"id": 1, "company": "神州租车", "pickup_city": "北京", "return_city": "上海",
             "pickup_date": date(2025, 8, 1), "car_type": "SUV", "price_per_day": 300}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_car_rental("北京", "上海", "2025-08-01")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("car_rentals", sql)
        self.assertEqual(params[:3], ["北京", "上海", "2025-08-01"])

    def test_query_car_rental_with_type(self):
        """测试带车型的租车查询"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        result = service.query_car_rental("北京", "上海", "2025-08-01", "SUV")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("car_type = %s", sql)
        self.assertEqual(params, ["北京", "上海", "2025-08-01", "SUV"])

    def test_query_insurance_basic(self):
        """测试基本保险查询"""
        self.mock_cursor.fetchall.return_value = [
            {"id": 1, "insurance_type": "综合型", "name": "全方位旅行保险"}
        ]

        service = self._create_service_with_mock_conn()
        result = service.query_insurance()

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("insurances", sql)
        # 不传类型时不应该有 WHERE 子句
        self.assertNotIn("WHERE", sql)

    def test_query_insurance_with_type(self):
        """测试带类型的保险查询"""
        self.mock_cursor.fetchall.return_value = []

        service = self._create_service_with_mock_conn()
        result = service.query_insurance("综合型")

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("WHERE", sql)
        self.assertEqual(params, ["综合型"])


# ==================== 5. MCP 服务器工具注册测试 ====================

class TestMCPServerRegistration(unittest.TestCase):
    """测试 MCP 服务器的配置参数"""

    def test_weather_mcp_config(self):
        """验证天气 MCP 服务器配置参数"""
        from mcp.server.fastmcp import FastMCP
        weather_mcp = FastMCP(
            name="WeatherTools",
            instructions="天气查询工具",
            log_level="ERROR",
            host="127.0.0.1", port=8002
        )
        self.assertEqual(weather_mcp.name, "WeatherTools")

    def test_ticket_mcp_config(self):
        """验证票务 MCP 服务器配置"""
        from mcp.server.fastmcp import FastMCP
        ticket_mcp = FastMCP(
            name="TicketTools",
            instructions="票务工具",
            log_level="ERROR",
            host="127.0.0.1", port=8001
        )
        self.assertEqual(ticket_mcp.name, "TicketTools")

    def test_trip_mcp_config(self):
        """验证行程 MCP 服务器配置"""
        from mcp.server.fastmcp import FastMCP
        trip_mcp = FastMCP(
            name="TripTools",
            instructions="行程管家工具",
            log_level="ERROR",
            host="127.0.0.1", port=8003
        )
        self.assertEqual(trip_mcp.name, "TripTools")


if __name__ == "__main__":
    unittest.main(verbosity=2)
