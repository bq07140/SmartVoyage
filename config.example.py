"""
配置文件模板 —— 复制此文件为 config.py 并填入真实配置
cp config.example.py config.py
"""

import os

project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

env = "dev"  # prod / test / dev / pre_prod

class Config:
    def __init__(self):
        # ===== 大模型配置 =====
        self.base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "your-api-key-here")
        self.model_name = 'qwen-plus'  # 替换为你使用的模型

        # ===== 数据库配置 =====
        self.host = 'localhost'
        self.user = 'your_db_user'       # 替换为实际用户名
        self.password = 'your_password'   # 替换为实际密码
        self.database = 'travel_rag'

        # ===== 日志配置 =====
        self.log_file = os.path.join(project_root, 'SmartVoyage', 'logs/app.log')

        # ===== 票务接口 =====
        self.url_123 = ""  # 12306 接口地址

        # ===== 意图映射 =====
        self.intent = {
            "weather": "WeatherQueryAssistant",
            "flight": "TicketAssistant",
            "train": "TicketAssistant",
            "concert": "TicketAssistant",
            "order": "TicketAssistant",
            "car_rental": "TripAssistant",
            "tour_group": "TripAssistant",
            "insurance": "TripAssistant",
            "trip_order": "TripAssistant",
        }

        self.temperature = 0.1

        # 天气数据源："database" 或 "api"
        self.weather_source = "api"

    def get_mysql_config(self, env):
        configs = {
            'prod':     ('prod-host',   'prod-user',   'prod-pass',   'travel_rag'),
            'dev':      ('localhost',   'dev-user',    'dev-pass',    'travel_rag'),
            'test':     ('localhost',   'test-user',   'test-pass',   'travel_rag'),
            'pre_prod': ('pre-host',    'pre-user',    'pre-pass',    'travel_rag'),
        }
        host, user, pwd, db = configs.get(env, configs['dev'])
        self.host, self.user, self.password, self.database = host, user, pwd, db
        return self.host, self.user, self.password, self.database
