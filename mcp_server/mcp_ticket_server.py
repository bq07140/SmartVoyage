"""
需求：实现基于MCP的票务查询服务器，提供火车票、机票和演唱会票的SQL查询功能
思路步骤：
1. 导入必要的模块和库
2. 初始化配置和日志记录器
3. 创建TicketService类（封装数据库操作逻辑）
4. 实现execute_query方法（执行SQL查询并返回JSON结果）
5. 创建create_ticket_mcp_server函数（创建MCP服务器）
6. 定义query_tickets工具（处理票务查询请求）
7. 主函数（启动MCP服务器）
"""
import mysql.connector
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from mcp.server.fastmcp import FastMCP

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from SmartVoyage.utils.format import DateEncoder, default_encoder

conf = Config()

# 票务服务类
class TicketService:  # 定义票务服务类，封装数据库操作逻辑
    def __init__(self):  # 初始化方法，建立数据库连接
        # 连接数据库
        self.conn = mysql.connector.connect(
            host=conf.host,
            user=conf.user,
            password=conf.password,
            database=conf.database
        )

    # 定义执行SQL查询方法，输入SQL字符串，返回JSON字符串
    def execute_query(self, sql: str) -> str:
        try:
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            cursor.close()
            # 格式化结果
            for result in results:  # 遍历每个结果字典
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):  # 检查值是否为特殊类型
                        result[key] = default_encoder(value)  # 使用自定义编码器格式化该值
            # 序列化为JSON，如果有结果返回success，否则no_data；使用DateEncoder，非ASCII不转义
            return json.dumps({"status": "success", "data": results} if results else {"status": "no_data",
                                                                                      "message": "未找到票务数据，请确认查询条件。"},
                              cls=DateEncoder, ensure_ascii=False)
        except Exception as e:
            logger.error(f"票务查询错误: {str(e)}")
            # 返回错误JSON响应
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

# 创建票务MCP服务器
def create_ticket_mcp_server():
    # 创建FastMCP实例
    ticket_mcp = FastMCP(name="TicketTools",
                         instructions="票务查询工具，基于 train_tickets, flight_tickets, concert_tickets 表。只支持查询。",
                         log_level="ERROR",
                         host="127.0.0.1", port=8001)

    # 实例化票务服务对象
    service = TicketService()

    @ticket_mcp.tool(
        name="query_tickets",
        description="查询票务数据，输入 SQL，如 'SELECT * FROM train_tickets WHERE departure_city = \"北京\" AND arrival_city = \"上海\"'"
    )
    def query_tickets(sql: str) -> str:
        logger.info(f"执行票务查询: {sql}")
        return service.execute_query(sql)

    # 打印服务器信息
    logger.info("=== 票务MCP服务器信息 ===")
    logger.info(f"名称: {ticket_mcp.name}")
    logger.info(f"描述: {ticket_mcp.instructions}")

    # 运行服务器
    try:
        print("服务器已启动，请访问 http://127.0.0.1:8001/mcp")
        ticket_mcp.run(transport="streamable-http")  # 使用 streamable-http 传输方式
    except Exception as e:
        print(f"服务器启动失败: {e}")

if __name__ == '__main__':
    create_ticket_mcp_server()