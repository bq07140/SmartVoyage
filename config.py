"""
需求：管理SmartVoyage项目的配置信息，包括大模型、数据库、日志等配置
思路步骤：
1. 定义项目根目录路径
2. 设置环境变量（生产/测试/开发/预生产）
3. 创建Config类管理所有配置项
4. 配置大模型参数（API地址、密钥、模型名称）
5. 配置数据库参数（主机、用户名、密码、数据库名）
6. 配置日志文件路径
7. 配置票务查询接口地址
8. 配置意图映射字典
9. 实现根据环境获取不同数据库配置的方法
"""

import os

# 项目根目录
project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

# 生产环境
# env = "prod"
# 测试环境
env = "test"


# 开发环境
# env = "dev"
# 预生产环境
# env = "pre_prod"


# 定义配置文件
class Config:

    def __init__(self):
        # 大模型配置
        self.base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "sk-4e52780bc4fb4497952360499a5503cf")
        self.model_name = 'qwen3.6-plus'

        # 数据库配置
        self.host = 'localhost'
        self.user = 'root'
        self.password = '123456'  # 不是root
        self.database = 'travel_rag'

        # 日志配置
        self.log_file = os.path.join(project_root, 'SmartVoyage4', 'logs/app.log')

        # 票务查询的12306接口地址
        self.url_123 = ""

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

        # 天气数据源配置
        # 可选值："database"（从数据库获取） / "api"（直接从和风API获取）
        # 使用 "api" 时，需要确保和风 API 密钥（spider_weather.py 中的 API_KEY）有效
        self.weather_source = "api"

    def get_mysql_config(self, env):
        """
        通过不同的环境获取不同的数据库配置
        :return:
        """
        if env == 'prod':
            # 数据库配置 生产
            self.host = 'localhost'
            self.user = 'root'
            self.password = 'root'
            self.database = 'travel_rag'
        elif env == 'dev':
            # 数据库配置 开发
            self.host = 'localhost1'
            self.user = 'root1'
            self.password = 'root1'
            self.database = 'travel_rag'
        elif env == 'test':
            # 数据库配置 测试
            self.host = 'localhost2'
            self.user = 'root2'
            self.password = 'root2'
            self.database = 'travel_rag'
        else:
            # 数据库配置 预生产
            self.host = 'localhost3'
            self.user = 'root3'
            self.password = 'root3'
            self.database = 'travel_rag'

        return self.host, self.user, self.password, self.database


if __name__ == '__main__':
    print(Config().log_file)
    print(Config().get_mysql_config(env))
    # ('localhost', 'root', 'root', 'travel_rag')
    # ('localhost', 'root2', 'root2', 'travel_rag')
