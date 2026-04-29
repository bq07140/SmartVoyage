import mysql.connector  # MySQL 数据库驱动
import json  # JSON 处理，用于序列化查询结果
from datetime import date, datetime, timedelta  # 时间处理
from decimal import Decimal  # 高精度小数类型
import gzip  # gzip 解压，和风 API 返回 gzip 压缩数据
import requests  # HTTP 请求库，用于调用和风天气 API
import uvicorn

# from mcp.server.fastmcp import FastMCP  # FastMCP 框架，快速创建 MCP 服务器
from python_a2a import FastMCP, create_fastapi_app

from SmartVoyage.config import Config  # 项目配置（数据库连接信息、天气数据源配置等）
from SmartVoyage.create_logger import logger  # 日志模块
from SmartVoyage.utils.format import DateEncoder, default_encoder  # 日期格式化工具

conf = Config()  # 全局配置实例


# ==================== 和风天气 API 配置 ====================
# 和风天气 API 密钥（与 spider_weather.py 共用）
HEFENG_API_KEY = "fe4ecfc532fa4de0bf84a20b30db7d52"
# 和风天气 API 基础地址
HEFENG_BASE_URL = "https://n94bjbmfte.re.qweatherapi.com/v7/weather/30d"

# 城市名称到和风城市代码的映射
# 和风 API 使用城市代码而不是城市名称来查询天气
CITY_CODES = {
    "北京": "101010100",
    "上海": "101020100",
    "杭州": "101280101",
    "南京": "101190101",
    "广州": "101280101",
    "深圳": "101280601",
}


def fetch_weather_from_api(city: str, start_date: str, end_date: str = None) -> str:
    # 检查城市代码是否存在
    location = CITY_CODES.get(city)
    if not location:
        return json.dumps({
            "status": "error",
            "message": f"不支持的城市：{city}。目前支持的城市有：{', '.join(CITY_CODES.keys())}"
        }, ensure_ascii=False)

    # 调用和风天气 API
    headers = {
        "X-QW-Api-Key": HEFENG_API_KEY,
        "Accept-Encoding": "gzip"
    }
    url = f"{HEFENG_BASE_URL}?location={location}"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # 和风 API 有时返回 gzip 压缩数据，有时返回纯 JSON 文本
        # 策略：先尝试判断是否为 JSON，如果是则直接解析；否则尝试 gzip 解压
        raw_content = response.content
        if raw_content.startswith(b'{') or raw_content.startswith(b'['):
            # 看起来是 JSON 文本，直接解析
            data = response.text
        elif response.headers.get('Content-Encoding') == 'gzip':
            # 是 gzip 压缩数据，解压后解析
            data = gzip.decompress(raw_content).decode('utf-8')
        else:
            # 默认直接解析
            data = response.text

        api_result = json.loads(data)

        # 检查 API 返回状态码
        if api_result.get("code") != "200":
            return json.dumps({
                "status": "error",
                "message": f"和风天气 API 返回错误码：{api_result.get('code')}"
            }, ensure_ascii=False)

        # 从 API 返回的原始数据中提取并格式化
        daily_data = api_result.get("daily", [])
        if not daily_data:
            return json.dumps({
                "status": "no_data",
                "message": "和风天气 API 未返回预报数据。"
            }, ensure_ascii=False)

        # 将 API 返回的数据格式化为与数据库查询一致的格式
        formatted_data = []
        for day in daily_data:
            fx_date = day.get("fxDate", "")

            # 如果指定了日期范围，则过滤
            if start_date and fx_date < start_date:
                continue
            if end_date and fx_date > end_date:
                break

            formatted_data.append({
                "city": city,
                "fx_date": fx_date,
                "temp_max": int(day.get("tempMax", 0)),
                "temp_min": int(day.get("tempMin", 0)),
                "text_day": day.get("textDay", ""),
                "text_night": day.get("textNight", ""),
                "humidity": int(day.get("humidity", 0)),
                "wind_dir_day": day.get("windDirDay", ""),
                "wind_scale_day": day.get("windScaleDay", ""),
                "wind_speed_day": int(day.get("windSpeedDay", 0)),
                "precip": float(day.get("precip", 0.0)),
                "uv_index": int(day.get("uvIndex", 0)),
                "pressure": int(day.get("pressure", 0)),
                "vis": int(day.get("vis", 0)),
                "cloud": int(day.get("cloud", 0)),
                "sunrise": day.get("sunrise", ""),
                "sunset": day.get("sunset", ""),
                "moonrise": day.get("moonrise", ""),
                "moonset": day.get("moonset", ""),
                "moon_phase": day.get("moonPhase", ""),
                "icon_day": day.get("iconDay", ""),
                "icon_night": day.get("iconNight", ""),
                "wind360_day": int(day.get("wind360Day", 0)),
                "wind360_night": int(day.get("wind360Night", 0)),
                "wind_dir_night": day.get("windDirNight", ""),
                "wind_scale_night": day.get("windScaleNight", ""),
                "wind_speed_night": int(day.get("windSpeedNight", 0)),
                "update_time": api_result.get("updateTime", ""),
            })

        if not formatted_data:
            return json.dumps({
                "status": "no_data",
                "message": f"未找到 {city} 在 {start_date} 到 {end_date or start_date} 之间的天气数据。"
            }, ensure_ascii=False)

        return json.dumps({"status": "success", "data": formatted_data}, cls=DateEncoder, ensure_ascii=False)

    except requests.RequestException as e:
        logger.error(f"请求 {city} 和风天气 API 失败: {e}")
        return json.dumps({"status": "error", "message": f"和风天气 API 请求失败：{str(e)}"}, ensure_ascii=False)
    except json.JSONDecodeError as e:
        logger.error(f"{city} 和风天气 JSON 解析错误: {e}")
        return json.dumps({"status": "error", "message": f"和风天气 JSON 解析失败：{str(e)}"}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"和风天气 API 查询出错: {str(e)}")
        return json.dumps({"status": "error", "message": f"和风天气 API 查询出错：{str(e)}"}, ensure_ascii=False)


# ==================== 天气服务类（数据库模式） ====================
class WeatherService:
    def __init__(self):
        # 建立数据库连接
        self.conn = mysql.connector.connect(
            host=conf.host,
            user=conf.user,
            password=conf.password,
            database=conf.database
        )

    def execute_query(self, sql: str, params: list = None) -> str:
        try:
            # 使用 dictionary=True，返回的结果每一行是一个字典（字段名为 key）
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(sql, params or [])
            results = cursor.fetchall()
            cursor.close()

            # 处理特殊类型（日期、Decimal 等），转换为 JSON 可序列化的格式
            for result in results:
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):
                        result[key] = default_encoder(value)

            # 根据查询结果构造不同的 JSON 响应
            if results:
                response = {"status": "success", "data": results}
            else:
                response = {"status": "no_data", "message": "未找到天气数据，请确认城市和日期。"}

            return json.dumps(response, cls=DateEncoder, ensure_ascii=False)

        except Exception as e:
            logger.error(f"天气查询错误: {str(e)}")
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    def query_weather(self, city: str, start_date: str, end_date: str = None) -> str:
        # 基础查询字段：选择用户最常关心的天气信息
        sql = (
            "SELECT city, fx_date, temp_max, temp_min, text_day, text_night, "
            "humidity, wind_dir_day, precip "
            "FROM weather_data "
            "WHERE city = %s"
        )
        params = [city]

        if end_date and end_date != start_date:
            # 有结束日期且与开始日期不同 → 日期范围查询
            sql += " AND fx_date BETWEEN %s AND %s ORDER BY fx_date"
            params.extend([start_date, end_date])
        else:
            # 单天查询
            sql += " AND fx_date = %s"
            params.append(start_date)

        logger.info(f"天气查询参数: city={city}, start_date={start_date}, end_date={end_date}")
        logger.info(f"生成的SQL: {sql}, 参数: {params}")

        return self.execute_query(sql, params)


# ==================== 创建 MCP 服务器 ====================
def create_weather_mcp_server():
    # 创建 FastMCP 实例（MCP 服务器）
    # weather_mcp = FastMCP(
    #     name="WeatherTools",
    #     instructions="天气查询工具，支持参数化查询。数据来源可配置（数据库或和风API）。",
    #     log_level="ERROR",
    #     host="127.0.0.1", port=8002
    # )

    weather_mcp = FastMCP(
        name="WeatherTools"
    )

    # 实例化数据库模式的天气服务（仅在 database 模式下使用）
    service = WeatherService()

    # 打印数据源配置信息
    logger.info(f"天气数据源配置: {conf.weather_source}")

    @weather_mcp.tool(
        name="query_weather",
        description=(
            "查询天气数据。参数：city(城市名称), start_date(开始日期，格式YYYY-MM-DD), "
            "end_date(结束日期，格式YYYY-MM-DD，可选，不传则只查start_date当天)。"
            "示例：query_weather(city='北京', start_date='2025-07-30') 查询北京2025-07-30的天气"
        )
    )
    def query_weather(city: str, start_date: str, end_date: str = None) -> str:
        logger.info(f"执行天气查询: city={city}, start_date={start_date}, end_date={end_date}, 数据源={conf.weather_source}")

        if conf.weather_source == "api":
            # 模式1：直接从和风天气 API 获取
            logger.info("使用和风天气 API 模式")
            return fetch_weather_from_api(city, start_date, end_date)
        else:
            # 模式2：从数据库查询（默认）
            logger.info("使用数据库查询模式")
            return service.query_weather(city, start_date, end_date)

    # 打印服务器信息
    logger.info("=== 天气MCP服务器信息 ===")
    logger.info(f"名称: {weather_mcp.name}")
    # logger.info(f"描述: {weather_mcp.description}")

    # 启动服务器
    try:
        # weather_mcp.run(transport="streamable-http")

        print("服务器已启动，请访问 http://127.0.0.1:8002/mcp")
        weather_mcp_server = create_fastapi_app(weather_mcp)
        uvicorn.run(weather_mcp_server, host='0.0.0.0', port=8002)

    except Exception as e:
        print(f"服务器启动失败: {e}")


if __name__ == '__main__':
    create_weather_mcp_server()
