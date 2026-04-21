"""
需求：获取北京天气API数据，用于天气数据更新
思路步骤：
1. 导入必要的模块和库
2. 配置API密钥和请求参数
3. 发送HTTP请求获取北京天气数据
4. 解析和处理API响应
5. 输出测试结果
"""
import requests
import json

# 配置（使用自己的密钥）
# API_KEY = "9ef68fe55401485180dd968fac902300"
API_KEY = "8daeab4446d84ef3881bfa0cefc026b1"
# url = "https://m7487r6ych.re.qweatherapi.com/v7/weather/30d?location=101010100"  # 北京30天预报
url = "https://mq2tup2e7f.re.qweatherapi.com/v7/weather/30d?location=101010100"  # 北京30天预报
headers = {
    "X-QW-Api-Key": API_KEY,
    "Accept-Encoding": "gzip"  # 请求gzip，但不强制
}
try:
    print("正在请求API...")
    response = requests.get(url, headers=headers, timeout=10)
    data = response.text
    parsed_data = json.loads(data)
    print("直接解析成功！")
    print(parsed_data)
except requests.RequestException as e:
    print(f"直接解析失败哦: {e}")