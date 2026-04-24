## 学习目标

* 理解 MCP 工具注册与 FastAPI 启动流程
* 理解票务MCP服务的实现
* 理解行程MCP的实现，以及和RAG的结合
* 了解单元测试与集成测试的区别，以及 unittest 框架的基本用法



## 一、天气 MCP 服务器

mcp\_weather\_server.py 是天气 MCP 服务器，提供 weather\_data 表的**参数化查询**接口，返回 JSON 格式结果。

**核心功能**：

- 支持**双数据源**：根据 `conf.weather_source` 配置，可从 MySQL 数据库或和风天气 API 直接获取天气数据
- 使用**参数化查询**（`cursor.execute(sql, params)`）防止 SQL 注入，LLM 无需生成 SQL
- 格式化日期和数值字段，确保 JSON 序列化兼容
- 通过 FastAPI 提供 HTTP 接口，响应 MCP 工具调用（端口 8002）

### 1 格式编码

format.py 中包含一个编码器方法和 JSON 编码器类。

**目标**：定义编码器方法，用于格式化单个对象；自定义 JSON 编码器，处理 MySQL 查询结果中的非标准类型。

**功能**：将 MySQL 查询结果中的 date、datetime、timedelta 和 Decimal 类型转换为 JSON 兼容的字符串或数值。

**位置**：SmartVoyage/utils/format.py

```python
import json
from datetime import date, datetime, timedelta
from decimal import Decimal


def default_encoder(obj):
    """格式化单个对象，将非标准类型转换为JSON兼容格式"""
    if isinstance(obj, datetime):
        return obj.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(obj, date):
        return obj.strftime('%Y-%m-%d')
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

# 自定义JSON编码器类，处理非标准类型序列化
class DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.strftime('%Y-%m-%d %H:%M:%S') if isinstance(obj, datetime) else obj.strftime('%Y-%m-%d')
        if isinstance(obj, timedelta):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)
```

### 2 WeatherService 类

**目标**：提供天气数据查询服务，使用参数化查询。

**功能**：初始化 MySQL 连接，执行参数化查询（`city`、`start_date`、`end_date`），格式化结果为 JSON。

**位置**：SmartVoyage/mcp_server/mcp_weather_server.py

```python
import mysql.connector
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from mcp.server.fastmcp import FastMCP

from SmartVoyage.config import Config
from SmartVoyage.create_logger import logger
from SmartVoyage.utils.format import DateEncoder, default_encoder

conf = Config()


# 天气服务类
class WeatherService:
    def __init__(self):
        self.conn = mysql.connector.connect(
            host=conf.host,
            user=conf.user,
            password=conf.password,
            database=conf.database
        )

    # 执行参数化查询，返回JSON字符串
    def query_weather(self, city: str, start_date: str, end_date: str) -> str:
        try:
            cursor = self.conn.cursor(dictionary=True)
            sql = ("SELECT city, fx_date, temp_max, temp_min, text_day, text_night, "
                   "humidity, wind_dir_day, wind_scale_day, precip FROM weather_data "
                   "WHERE city = %s AND fx_date BETWEEN %s AND %s ORDER BY fx_date")
            cursor.execute(sql, [city, start_date, end_date])
            results = cursor.fetchall()
            cursor.close()
            for result in results:
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):
                        result[key] = default_encoder(value)
            return json.dumps(
                {"status": "success", "data": results} if results
                else {"status": "no_data", "message": "未找到天气数据，请确认城市和日期。"},
                cls=DateEncoder, ensure_ascii=False
            )
        except Exception as e:
            logger.error(f"天气查询错误: {str(e)}")
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
```

> **特殊说明**：为了教学方便，本项目中天气数据使用 MySQL 数据库模拟。实际工作中，天气数据需要从第三方 API（如和风天气）实时获取，涉及 API 认证、限流、数据解析等更复杂的对接工作。项目中通过 `conf.weather_source` 配置支持双数据源切换：`"database"` 模式从数据库查询（教学用），`"api"` 模式直接调用和风天气 API（生产用）。

### 3 双数据源：和风天气 API 模式

当 `conf.weather_source = "api"` 时，服务器会绕过数据库，直接调用和风天气 API 获取实时天气数据。

```python
import requests

def fetch_weather_from_api(city: str, start_date: str, end_date: str) -> str:
    """从和风天气 API 获取天气数据"""
    logger.info(f"从和风天气API获取数据: {city}, {start_date} ~ {end_date}")

    # 城市转地理位置
    geo_url = f"https://geoapi.qweather.com/v2/city/lookup?location={city}&key={conf.qweather_api_key}"
    geo_resp = requests.get(geo_url)
    geo_data = geo_resp.json()
    if not geo_data.get("location"):
        return json.dumps({"status": "no_data", "message": f"未找到城市：{city}"}, ensure_ascii=False)

    location_id = geo_data["location"][0]["id"]

    # 获取天气预报
    weather_url = f"https://devapi.qweather.com/v7/weather/7d?location={location_id}&key={conf.qweather_api_key}"
    weather_resp = requests.get(weather_url)
    weather_data = weather_resp.json()

    # 过滤日期范围并格式化
    results = []
    for day in weather_data.get("daily", []):
        if start_date <= day.get("fxDate", "") <= end_date:
            results.append({
                "city": city,
                "fx_date": day.get("fxDate"),
                "temp_max": int(day.get("tempMax", 0)),
                "temp_min": int(day.get("tempMin", 0)),
                "text_day": day.get("textDay", ""),
                "text_night": day.get("textNight", ""),
                "humidity": int(day.get("humidity", 0)),
                "wind_dir_day": day.get("windDirDay", ""),
                "wind_scale_day": day.get("windScaleDay", ""),
                "precip": float(day.get("precip", 0))
            })

    if results:
        return json.dumps({"status": "success", "data": results}, ensure_ascii=False)
    return json.dumps({"status": "no_data", "message": "未找到天气数据。"}, ensure_ascii=False)
```

### 4 启动 MCP 服务器

create\_weather\_mcp\_server() 函数

**目标**：创建并启动天气 MCP 服务器。

**功能**：初始化 FastMCP，注册 query\_weather 工具，启动 FastAPI 服务器，监听端口 8002。

```python
def create_weather_mcp_server():
    weather_mcp = FastMCP(
        name="WeatherTools",
        instructions="天气查询工具，支持数据库或和风天气API双数据源。",
        log_level="ERROR",
        host="127.0.0.1", port=8002
    )

    service = WeatherService()

    @weather_mcp.tool(
        name="query_weather",
        description="查询天气数据，参数：city(城市), start_date(开始日期YYYY-MM-DD), end_date(结束日期YYYY-MM-DD)"
    )
    def query_weather(city: str, start_date: str, end_date: str) -> str:
        logger.info(f"执行天气查询: {city}, {start_date} ~ {end_date}")
        if conf.weather_source == "api":
            return fetch_weather_from_api(city, start_date, end_date)
        return service.query_weather(city, start_date, end_date)

    logger.info("=== 天气MCP服务器信息 ===")
    logger.info(f"名称: {weather_mcp.name}")
    logger.info(f"描述: {weather_mcp.instructions}")

    try:
        print("服务器已启动，请访问 http://127.0.0.1:8002/mcp")
        weather_mcp.run(transport="streamable-http")
    except Exception as e:
        print(f"服务器启动失败: {e}")
```



## 二、票务 MCP 服务器

mcp\_ticket\_server.py 是统一的票务 MCP 服务器，提供火车票、机票和演唱会票的**参数化查询**和**预定**功能（原查询和预定服务已合并）。

**核心功能**：

- **参数化查询**：LLM 仅提取参数（出发城市、到达城市、日期、座位类型等），SQL 拼接逻辑收敛到 MCP 端，使用 `cursor.execute(sql, params)` 防止 SQL 注入
- **预定功能**：火车票、飞机票、演唱会票预定（模拟返回）
- 通过 FastAPI 提供 HTTP 接口，响应 MCP 工具调用（共 6 个工具：3 查询 + 3 预定）
- 端口：8001

### 1 TicketService 类

**目标**：提供票务数据查询和预定服务，使用参数化查询防止 SQL 注入。

**功能**：初始化 MySQL 连接，执行参数化查询，格式化结果为 JSON。

**位置**：SmartVoyage/mcp_server/mcp_ticket_server.py

```python
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
class TicketService:
    def __init__(self):
        self.conn = mysql.connector.connect(
            host=conf.host,
            user=conf.user,
            password=conf.password,
            database=conf.database
        )

    # 执行参数化查询，返回JSON字符串
    def _execute_query(self, sql: str, params: list = None) -> str:
        try:
            cursor = self.conn.cursor(dictionary=True)
            cursor.execute(sql, params or [])
            results = cursor.fetchall()
            cursor.close()
            for result in results:
                for key, value in result.items():
                    if isinstance(value, (date, datetime, timedelta, Decimal)):
                        result[key] = default_encoder(value)
            return json.dumps(
                {"status": "success", "data": results} if results
                else {"status": "no_data", "message": "未找到票务数据，请确认查询条件。"},
                cls=DateEncoder, ensure_ascii=False
            )
        except Exception as e:
            logger.error(f"票务查询错误: {str(e)}")
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    # 查询火车票（参数化）
    def query_train(self, departure_city: str, arrival_city: str, date: str, seat_type: str = None) -> str:
        sql = ("SELECT id, departure_city, arrival_city, departure_time, arrival_time, "
               "train_number, seat_type, price, remaining_seats FROM train_tickets "
               "WHERE departure_city = %s AND arrival_city = %s AND DATE(departure_time) = %s")
        params = [departure_city, arrival_city, date]
        if seat_type:
            sql += " AND seat_type = %s"
            params.append(seat_type)
        return self._execute_query(sql, params)

    # 查询机票（参数化）
    def query_flight(self, departure_city: str, arrival_city: str, date: str, cabin_type: str = None) -> str:
        sql = ("SELECT id, departure_city, arrival_city, departure_time, arrival_time, "
               "flight_number, cabin_type, price, remaining_seats FROM flight_tickets "
               "WHERE departure_city = %s AND arrival_city = %s AND DATE(departure_time) = %s")
        params = [departure_city, arrival_city, date]
        if cabin_type:
            sql += " AND cabin_type = %s"
            params.append(cabin_type)
        return self._execute_query(sql, params)

    # 查询演唱会票（参数化）
    def query_concert(self, city: str, artist: str, date: str, ticket_type: str = None) -> str:
        sql = ("SELECT id, artist, city, venue, start_time, end_time, "
               "ticket_type, price, remaining_seats FROM concert_tickets "
               "WHERE city = %s AND artist = %s AND DATE(start_time) = %s")
        params = [city, artist, date]
        if ticket_type:
            sql += " AND ticket_type = %s"
            params.append(ticket_type)
        return self._execute_query(sql, params)

    # 预定方法（模拟返回）
    def order_train(self, departure_date: str, train_number: str, seat_type: str, number: int) -> str:
        return f"恭喜，火车票预定成功！日期：{departure_date}，车次：{train_number}，座位：{seat_type}，数量：{number}张。"

    def order_flight(self, departure_date: str, flight_number: str, cabin_type: str, number: int) -> str:
        return f"恭喜，机票预定成功！日期：{departure_date}，航班号：{flight_number}，舱位：{cabin_type}，数量：{number}张。"

    def order_concert(self, start_date: str, artist: str, venue: str, ticket_type: str, number: int) -> str:
        return f"恭喜，演出票预定成功！日期：{start_date}，艺人：{artist}，场馆：{venue}，票类型：{ticket_type}，数量：{number}张。"
```

> **特殊说明**：为了教学方便，本项目中的票务预定功能使用数据库模拟，返回预定的成功消息。实际工作中需要对接第三方票务平台的 API，涉及实时库存查询、支付接口、订单管理等复杂的业务流程。

### 2 启动 MCP 服务器

create\_ticket\_mcp\_server() 函数

**目标**：创建并启动票务 MCP 服务器。

**功能**：初始化 FastMCP，注册 6 个工具（3 查询 + 3 预定），启动 FastAPI 服务器，监听端口 8001。

```python
def create_ticket_mcp_server():
    ticket_mcp = FastMCP(
        name="TicketTools",
        instructions="票务工具，支持火车票、机票、演唱会票的查询与预定。",
        log_level="ERROR",
        host="127.0.0.1", port=8001
    )

    service = TicketService()

    # 注册查询工具
    @ticket_mcp.tool(
        name="query_train",
        description="查询火车票信息，参数：departure_city(出发城市), arrival_city(到达城市), date(日期), seat_type(座位类型，可选)"
    )
    def query_train(departure_city: str, arrival_city: str, date: str, seat_type: str = None) -> str:
        return service.query_train(departure_city, arrival_city, date, seat_type)

    @ticket_mcp.tool(
        name="query_flight",
        description="查询机票信息，参数：departure_city(出发城市), arrival_city(到达城市), date(日期), cabin_type(舱位类型，可选)"
    )
    def query_flight(departure_city: str, arrival_city: str, date: str, cabin_type: str = None) -> str:
        return service.query_flight(departure_city, arrival_city, date, cabin_type)

    @ticket_mcp.tool(
        name="query_concert",
        description="查询演唱会票信息，参数：city(城市), artist(艺人), date(日期), ticket_type(票类型，可选)"
    )
    def query_concert(city: str, artist: str, date: str, ticket_type: str = None) -> str:
        return service.query_concert(city, artist, date, ticket_type)

    # 注册预定工具
    @ticket_mcp.tool(
        name="order_train",
        description="根据日期、车次、座位类型、数量预定火车票"
    )
    def order_train(departure_date: str, train_number: str, seat_type: str, number: int) -> str:
        return service.order_train(departure_date, train_number, seat_type, number)

    @ticket_mcp.tool(
        name="order_flight",
        description="根据日期、航班号、舱位类型、数量预定机票"
    )
    def order_flight(departure_date: str, flight_number: str, cabin_type: str, number: int) -> str:
        return service.order_flight(departure_date, flight_number, cabin_type, number)

    @ticket_mcp.tool(
        name="order_concert",
        description="根据日期、艺人、场馆、票类型、数量预定演出票"
    )
    def order_concert(start_date: str, artist: str, venue: str, ticket_type: str, number: int) -> str:
        return service.order_concert(start_date, artist, venue, ticket_type, number)

    logger.info("=== 票务MCP服务器信息 ===")
    logger.info(f"名称: {ticket_mcp.name}")
    logger.info(f"描述: {ticket_mcp.instructions}")

    try:
        print("服务器已启动，请访问 http://127.0.0.1:8001/mcp")
        ticket_mcp.run(transport="streamable-http")
    except Exception as e:
        print(f"服务器启动失败: {e}")
```



## 三、行程 MCP 服务器

mcp\_trip\_server.py 是行程管家 MCP 服务器，提供租车、旅游团、保险的查询及预订功能。

**核心功能**：

- **租车/保险**：从 MySQL 数据库真实查询
- **旅游团**：从 Milvus 向量数据库进行语义检索（RAG 模式），支持自然语言描述需求（如"想看雪山的地方"）
- **预定功能**：模拟返回
- 端口：8003，共 6 个工具（3 查询 + 3 预定）

### 1 Milvus 向量数据库配置

旅游团使用 RAG（检索增强生成）模式，通过 Milvus 本地向量数据库存储旅游团信息。

```python
MILVUS_HOST = "localhost"
MILVUS_PORT = 19530
COLLECTION_NAME = "tour_groups"
EMBEDDING_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
EMBEDDING_DIM = 1024


def get_embedding(text: str) -> list:
    """调用 Qwen Embedding API 生成 1024 维文本向量"""
    headers = {
        "Authorization": f"Bearer {conf.api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "text-embedding-v3",
        "input": [text],
        "dimensions": EMBEDDING_DIM
    }
    response = requests.post(EMBEDDING_URL, headers=headers, json=payload, timeout=30)
    return response.json()["data"][0]["embedding"]


def search_tour_groups_in_milvus(query_text: str, city: str = None, limit: int = 5) -> list:
    """在 Milvus 中进行语义搜索，找到最匹配的旅游团"""
    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION_NAME)
    collection.load()

    results = collection.search(
        data=[get_embedding(query_text)],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"nprobe": 10}},
        limit=limit,
        output_fields=["tour_id", "tour_name", "city", "days", "price",
                       "total_seats", "remaining_seats", "agency", "rating",
                       "departure_dates", "highlights"],
        expr=f'city == "{city}"' if city else None
    )

    tour_list = []
    for hits in results:
        for hit in hits:
            tour_list.append({
                "tour_id": hit.entity.get("tour_id"),
                "tour_name": hit.entity.get("tour_name"),
                "city": hit.entity.get("city"),
                "days": hit.entity.get("days"),
                "price": hit.entity.get("price"),
                "similarity": round(hit.distance, 4),
                # ... 其他字段
            })
    return tour_list
```

### 2 TripService 类

**目标**：提供行程查询与预订服务，混合数据源（MySQL + Milvus）。

**位置**：SmartVoyage/mcp_server/mcp_trip_server.py

```python
class TripService:
    def __init__(self):
        self.conn = mysql.connector.connect(
            host=conf.host, user=conf.user,
            password=conf.password, database=conf.database
        )

    def _execute_query(self, sql: str, params: list = None) -> str:
        """执行 SQL 查询，返回 JSON 格式结果"""
        # ... 与 TicketService 相同的参数化查询逻辑

    # 租车查询（MySQL）
    def query_car_rental(self, pickup_city: str, return_city: str, date: str, car_type: str = None) -> str:
        sql = ("SELECT ... FROM car_rentals "
               "WHERE pickup_city = %s AND return_city = %s AND pickup_date = %s")
        params = [pickup_city, return_city, date]
        if car_type:
            sql += " AND car_type = %s"
            params.append(car_type)
        return self._execute_query(sql, params)

    # 旅游团查询（Milvus RAG）
    def query_tour_group(self, query_text: str, city: str = None) -> str:
        tour_list = search_tour_groups_in_milvus(query_text, city, limit=5)
        if tour_list:
            return json.dumps({"status": "success", "data": tour_list}, ensure_ascii=False)
        return json.dumps({"status": "no_data", "message": "未找到匹配的旅游团"}, ensure_ascii=False)

    # 保险查询（MySQL）
    def query_insurance(self, insurance_type: str = None) -> str:
        sql = "SELECT ... FROM insurances"
        params = []
        if insurance_type:
            sql += " WHERE insurance_type = %s"
            params.append(insurance_type)
        return self._execute_query(sql, params)

    # 预定方法（模拟返回）
    def order_car_rental(self, date: str, car_type: str, number: int) -> str:
        return f"恭喜，租车预订成功！日期：{date}，车型：{car_type}，数量：{number}辆。"

    def order_tour_group(self, date: str, tour_name: str, number: int) -> str:
        return f"恭喜，旅游团报名成功！团名：{tour_name}，日期：{date}，人数：{number}人。"

    def order_insurance(self, insurance_type: str, date: str, number: int) -> str:
        return f"恭喜，旅行保险购买成功！类型：{insurance_type}，日期：{date}，份数：{number}份。"
```

### 3 启动 MCP 服务器

```python
def create_trip_mcp_server():
    trip_mcp = FastMCP(
        name="TripTools",
        instructions="行程管家工具。租车和保险通过 MySQL 查询，旅游团通过 Milvus 语义检索。支持查询和预订。",
        log_level="ERROR",
        host="127.0.0.1", port=8003
    )

    service = TripService()

    # 注册查询工具
    @trip_mcp.tool(name="query_car_rental", description="查询租车信息")
    def query_car_rental(pickup_city: str, return_city: str, date: str, car_type: str = None) -> str:
        return service.query_car_rental(pickup_city, return_city, date, car_type)

    @trip_mcp.tool(name="query_tour_group", description="查询旅游团（语义搜索）")
    def query_tour_group(query_text: str, city: str = None) -> str:
        return service.query_tour_group(query_text, city)

    @trip_mcp.tool(name="query_insurance", description="查询旅行保险产品")
    def query_insurance(insurance_type: str = None) -> str:
        return service.query_insurance(insurance_type)

    # 注册预订工具
    @trip_mcp.tool(name="order_car_rental", description="预订租车服务")
    def order_car_rental(date: str, car_type: str, number: int) -> str:
        return service.order_car_rental(date, car_type, number)

    @trip_mcp.tool(name="order_tour_group", description="报名旅游团")
    def order_tour_group(date: str, tour_name: str, number: int) -> str:
        return service.order_tour_group(date, tour_name, number)

    @trip_mcp.tool(name="order_insurance", description="购买旅行保险")
    def order_insurance(insurance_type: str, date: str, number: int) -> str:
        return service.order_insurance(insurance_type, date, number)

    trip_mcp.run(transport="streamable-http")
```



## 四、MCP 服务器测试

### 1 测试基础知识

在编写测试代码之前，先了解三个基本概念：**单元测试**、**集成测试**和 **unittest 框架**。

#### 1.1 什么是单元测试

单元测试（Unit Test）是对代码中**最小可测试单元**（通常是单个函数、方法或类）进行验证的测试方式。它的特点：

- **独立性强**：只测一个功能点，不依赖外部系统（数据库、网络、第三方服务）
- **运行快**：通常在毫秒级别完成
- **定位准**：测试失败时能快速定位到具体问题

本项目中的**格式编码测试**就是典型的单元测试 —— 只验证 `default_encoder` 函数能否正确转换 `datetime`、`Decimal` 等类型，不需要连接数据库或启动服务器。

```python
# 单元测试示例：验证 datetime 编码
def test_encode_datetime(self):
    dt = datetime(2025, 7, 30, 14, 30, 0)
    result = self.default_encoder_func(dt)
    self.assertEqual(result, '2025-07-30 14:30:00')
```

#### 1.2 什么是集成测试

集成测试（Integration Test）用于验证**多个组件协同工作**是否正确。它的特点：

- **需要真实环境**：依赖数据库、外部服务、网络等
- **覆盖面广**：能发现单元测试无法捕获的问题（如 SQL 拼接错误、数据格式不匹配）
- **运行较慢**：涉及 I/O 操作

本项目中的 **MCP 服务器集成测试**就是典型例子 —— 直接实例化 `WeatherService`，调用其 `query_weather` 方法，验证与 MySQL 数据库的真实交互：

```python
# 集成测试示例：验证数据库查询
def test_query_weather_single_day(self):
    result = self.service.query_weather("北京", "2025-07-30")
    parsed = json.loads(result)
    self.assertEqual(parsed["status"], "success")
    self.assertEqual(parsed["data"][0]["city"], "北京")
```

#### 1.3 unittest 框架

`unittest` 是 Python 标准库自带的测试框架，核心概念：

| 概念 | 说明 |
|------|------|
| `TestCase` | 测试用例类，继承 `unittest.TestCase`，包含多个测试方法 |
| `test_xxx` | 以 `test_` 开头的方法会被自动识别为测试用例 |
| `assertEqual` | 断言方法，验证两个值是否相等，失败时抛出异常 |
| `assertTrue` / `assertFalse` | 断言布尔条件是否为真 / 假 |
| `assertGreater` | 断言前者大于后者 |
| `assertIn` | 断言前者包含在后者中 |
| `setUp` | 每个测试方法运行前执行，用于准备测试环境 |
| `setUpClass` | 类级别初始化，只在类创建时执行一次（常用在集成测试中创建数据库连接） |

```python
import unittest

class TestExample(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """类级别初始化，只执行一次"""
        cls.service = SomeService()

    def setUp(self):
        """每个测试方法运行前执行"""
        pass

    def test_something(self):
        self.assertEqual(1 + 1, 2)       # 相等断言
        self.assertTrue(True)             # 布尔断言
        self.assertGreater(5, 3)          # 大小断言
        self.assertIn("a", ["a", "b"])    # 包含断言
```

运行方式：

```bash
# 运行整个测试模块
python -m tests.test_mcp_servers

# 运行单个测试类
python -m unittest tests.test_mcp_servers.TestFormatEncoder

# 运行单个测试方法
python -m unittest tests.test_mcp_servers.TestFormatEncoder.test_encode_date
```

---

### 2 格式编码测试（纯函数，零依赖）

测试 `default_encoder` 函数和 `DateEncoder` 类，确保日期、Decimal 等特殊类型能正确序列化为 JSON。这是纯函数测试，不需要连接数据库或启动服务器。

```python
import unittest
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from SmartVoyage.utils.format import default_encoder, DateEncoder


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
```

### 3 MCP 服务器集成测试（需要数据库）

直接实例化 `WeatherService`、`TicketService`、`TripService` 类，调用其查询方法，验证与真实数据库的交互是否正确。

```python
class TestWeatherMCPIntegration(unittest.TestCase):
    """集成测试：天气 MCP 服务器"""

    @classmethod
    def setUpClass(cls):
        from SmartVoyage.mcp_server.mcp_weather_server import WeatherService
        cls.service = WeatherService()

    def test_query_weather_single_day(self):
        """测试单天天气查询"""
        result = self.service.query_weather("北京", "2025-07-30")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")
        self.assertGreater(len(parsed["data"]), 0)
        first = parsed["data"][0]
        self.assertEqual(first["city"], "北京")
        self.assertEqual(first["fx_date"], "2025-07-30")

    def test_query_weather_no_data(self):
        """测试无数据情况"""
        result = self.service.query_weather("火星", "2025-07-30")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "no_data")

    def test_weather_mcp_server_config(self):
        """验证天气 MCP 服务器配置"""
        from SmartVoyage.mcp_server.mcp_weather_server import create_weather_mcp_server
        server = create_weather_mcp_server()
        self.assertEqual(server.name, "WeatherTools")


class TestTicketMCPIntegration(unittest.TestCase):
    """集成测试：票务 MCP 服务器"""

    @classmethod
    def setUpClass(cls):
        from SmartVoyage.mcp_server.mcp_ticket_server import TicketService
        cls.service = TicketService()

    def test_query_train(self):
        """测试火车票查询"""
        result = self.service.query_train("北京", "上海", "2025-07-30")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")
        self.assertGreater(len(parsed["data"]), 0)

    def test_query_train_with_seat(self):
        """测试带座位类型的火车票查询"""
        result = self.service.query_train("北京", "上海", "2025-07-30", "二等座")
        parsed = json.loads(result)
        for item in parsed["data"]:
            self.assertEqual(item["seat_type"], "二等座")

    def test_query_flight(self):
        """测试机票查询"""
        result = self.service.query_flight("北京", "上海", "2025-07-30")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")

    def test_query_concert(self):
        """测试演唱会票查询"""
        result = self.service.query_concert("北京", "刀郎", "2025-08-23")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")
        first = parsed["data"][0]
        self.assertEqual(first["artist"], "刀郎")


class TestTripMCPIntegration(unittest.TestCase):
    """集成测试：行程 MCP 服务器"""

    @classmethod
    def setUpClass(cls):
        from SmartVoyage.mcp_server.mcp_trip_server import TripService
        cls.service = TripService()

    def test_query_car_rental(self):
        """测试租车查询"""
        result = self.service.query_car_rental("北京", "上海", "2025-08-01")
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "success")

    def test_query_insurance_with_type(self):
        """测试带类型的保险查询"""
        result = self.service.query_insurance("综合型")
        parsed = json.loads(result)
        for item in parsed["data"]:
            self.assertEqual(item["insurance_type"], "综合型")

    def test_trip_mcp_server_config(self):
        """验证行程 MCP 服务器配置"""
        from SmartVoyage.mcp_server.mcp_trip_server import create_trip_mcp_server
        server = create_trip_mcp_server()
        self.assertEqual(server.name, "TripTools")
```

### 4 运行测试

```bash
cd SmartVoyage
python -m tests.test_mcp_servers
```

预期输出：

```
test_encode_date (__main__.TestFormatEncoder.test_encode_date) ... ok
test_encode_datetime (__main__.TestFormatEncoder.test_encode_datetime) ... ok
test_encode_decimal (__main__.TestFormatEncoder.test_encode_decimal) ... ok
test_encode_timedelta (__main__.TestFormatEncoder.test_encode_timedelta) ... ok
test_encode_unknown (__main__.TestFormatEncoder.test_encode_unknown) ... ok
test_date_encoder_json_dumps (__main__.TestFormatEncoder.test_date_encoder_json_dumps) ... ok
test_query_weather_single_day (__main__.TestWeatherMCPIntegration) ... ok
test_query_weather_no_data (__main__.TestWeatherMCPIntegration) ... ok
test_weather_mcp_server_config (__main__.TestWeatherMCPIntegration) ... ok
test_query_train (__main__.TestTicketMCPIntegration) ... ok
test_query_train_with_seat (__main__.TestTicketMCPIntegration) ... ok
test_query_flight (__main__.TestTicketMCPIntegration) ... ok
test_query_concert (__main__.TestTicketMCPIntegration) ... ok
test_query_car_rental (__main__.TestTripMCPIntegration) ... ok
test_query_insurance_with_type (__main__.TestTripMCPIntegration) ... ok
test_trip_mcp_server_config (__main__.TestTripMCPIntegration) ... ok
----------------------------------------------------------------------
Ran 16 tests in 0.05s

OK
```

> **注意**：集成测试部分需要 MySQL 数据库已启动且包含测试数据。纯函数测试（格式编码部分）无需任何依赖即可运行。