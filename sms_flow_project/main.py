import uvicorn
import httpx
import asyncio
import os
import sys
import uuid
import json
import random
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

from .register_flow import run_protocol_register
from .login_flow import run_protocol_login
from .oauth_flow import run_oauth_branch
from .db_manager import DBManager

app = FastAPI(title="FastAPI + Camoufox 动态配置注册与登录服务")

# 初始化数据库管理器
db = DBManager()

# --- Pydantic Schemas ---

class GUIConfig(BaseModel):
    login_headless: bool = Field(default=False, description="登录/注册过程是否无 GUI")
    oauth_headless: bool = Field(default=True, description="获取 OAuth 授权过程是否无 GUI")
    auto_detect_geo: bool = Field(default=True, description="是否基于代理 IP 自动定位区域与时区")
    locale: str = Field(default="en-US")
    timezone: str = Field(default="America/New_York")

DEFAULT_LOCALE_MAP = {
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "AU": "en-AU",
    "SG": "en-SG",
    "ID": "id-ID",
    "RU": "ru-RU",
    "UA": "uk-UA",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "DE": "de-DE",
    "FR": "fr-FR",
    "ES": "es-ES",
    "IT": "it-IT",
    "NL": "nl-NL",
    "BR": "pt-BR",
    "IN": "en-IN",
    "HK": "zh-HK",
    "TW": "zh-TW",
    "PH": "en-PH",
    "VN": "vi-VN",
    "MY": "ms-MY",
    "TH": "th-TH"
}

SMS_COUNTRY_NAMES = {
    "0": "俄罗斯 (Russia)",
    "1": "乌克兰 (Ukraine)",
    "2": "哈萨克斯坦 (Kazakhstan)",
    "3": "中国 (China)",
    "4": "菲律宾 (Philippines)",
    "5": "缅甸 (Myanmar)",
    "6": "印尼 (Indonesia)",
    "7": "马来西亚 (Malaysia)",
    "8": "肯尼亚 (Kenya)",
    "9": "坦桑尼亚 (Tanzania)",
    "10": "越南 (Vietnam)",
    "11": "吉尔吉斯斯坦 (Kyrgyzstan)",
    "12": "美国 (USA)",
    "15": "波兰 (Poland)",
    "22": "印度 (India)",
    "36": "加拿大 (Canada)",
    "40": "罗马尼亚 (Romania)",
    "85": "英国 (United Kingdom)",
    "86": "德国 (Germany)",
    "87": "法国 (France)",
    "93": "新加坡 (Singapore)",
    "94": "柬埔寨 (Cambodia)",
    "122": "泰国 (Thailand)",
}

class ProxyConfig(BaseModel):
    mode: str = Field(default="api", description="'api' (动态获取) 或 'static' (静态代理)")
    api_url: Optional[str] = Field(default=None, description="动态获取代理的单个 API URL (向后兼容)")
    api_urls: Optional[List[str]] = Field(default=None, description="动态获取代理的 API URL 列表池")
    strategy: str = Field(default="fixed", description="提取策略: 'fixed' (固定使用) 或 'random' (随机使用)")
    static_proxy: Optional[str] = Field(default=None, description="静态代理地址，如 socks5h://user:pass@host:port")

class GPTSMSConfig(BaseModel):
    provider: str = Field(default="smsbower", description="支持 smsbower, smsactivate, herosms")
    api_key: str = Field(..., description="接码平台 API Key")
    service: str = Field(default="dr")
    country: str = Field(default="6", description="默认国家ID")
    preferred_countries: Optional[list[str]] = Field(default=None, description="首选国家ID列表，按顺序尝试")
    min_price: Optional[float] = Field(default=None, description="最低接受价格（价格区间下限）")
    max_price: Optional[float] = Field(default=None, description="最高接受价格（价格区间上限）")
    activation_id: Optional[str] = Field(default=None, description="登录二次验证时可选传入的历史激活 ID")
    otp_timeout_seconds: int = Field(default=180, description="等待验证码超时时间（秒），超时后注册流程可自动换码")
    otp_retry_attempts: int = Field(default=3, description="注册流程接码超时后的自动换码最大尝试次数")
    otp_timeout_seconds: int = Field(default=180, description="等待验证码超时时间（秒），超时后注册分支将换号重试")

class FixedConfig(BaseModel):
    phone: str = Field(..., description="已取回的固定号码")
    sms_url: Optional[str] = Field(default=None, description="直接拉取短信的单个 URL (向后兼容)")
    sms_urls: Optional[List[str]] = Field(default=None, description="短信拉取的 URL 列表池")
    strategy: str = Field(default="fixed", description="拉取策略: 'fixed' (固定使用第一个) 或 'random' (随机使用)")

class PayPalSMSConfig(BaseModel):
    provider: str = Field(default="yihao", description="yihao 代表一浩美国API接码")
    mode: str = Field(default="temp", description="接码模式: temp (临时接码下单) 或 fixed (使用已有的固定号码)")
    api_key: Optional[str] = Field(default=None, description="临时接码时必填")
    service: Optional[str] = Field(default=None, description="临时接码时对应一浩的 goods_id (平台-国家-租期)")
    otp_timeout_seconds: int = Field(default=180, description="等待验证码超时时间（秒）")
    fixed_config: Optional[FixedConfig] = Field(default=None, description="固定号码接码配置 (mode 为 fixed 时生效)")

class SMSConfig(BaseModel):
    gpt_sms: GPTSMSConfig
    paypal_sms: PayPalSMSConfig

class AccountConfig(BaseModel):
    email: Optional[str] = Field(default=None, description="注册时使用的邮箱 (若为 catch-all)")
    password: Optional[str] = Field(default=None, description="登录或注册指定的密码 (为空则自动生成)")
    phone: Optional[str] = Field(default=None, description="登录分支必填，注册分支留空则自动接码")

class FlowRequest(BaseModel):
    flow_type: str = Field(..., description="运行分支: 'register' (注册) 或 'login' (登录)")
    gui_config: GUIConfig
    proxy_config: ProxyConfig
    sms_config: SMSConfig
    account_config: AccountConfig

# --- Helper Functions ---

def normalize_proxy_text(proxy_text: str) -> str:
    """将代理 API 返回的文本标准化为可被 httpx/curl_cffi 消费的代理 URL。"""
    value = (proxy_text or "").strip()
    if not value:
        raise ValueError("代理 API 返回空数据")

    if value.startswith(("socks5://", "socks5h://", "http://", "https://")):
        return value

    parts = value.split(":")
    if len(parts) == 4 and "@" not in value:
        host, port, username, password = parts
        if port.isdigit():
            return f"socks5://{username}:{password}@{host}:{port}"

    if "@" in value:
        return f"socks5://{value}"

    if len(parts) == 2 and parts[1].isdigit():
        return f"socks5://{value}"

    raise ValueError(f"无法识别的代理格式: {value}")


async def get_proxy_url(proxy_cfg: ProxyConfig) -> str:
    """解析并获取代理 URL"""
    if proxy_cfg.mode == "static":
        if not proxy_cfg.static_proxy:
            raise HTTPException(status_code=400, detail="静态代理模式下必须提供 static_proxy")
        return normalize_proxy_text(proxy_cfg.static_proxy)
    elif proxy_cfg.mode == "api":
        # 合并 api_urls 池和 api_url
        urls = []
        if proxy_cfg.api_urls:
            urls.extend([u.strip() for u in proxy_cfg.api_urls if u.strip()])
        if proxy_cfg.api_url and proxy_cfg.api_url.strip():
            urls.append(proxy_cfg.api_url.strip())

        # 去重并保持顺序
        unique_urls = []
        for u in urls:
            if u not in unique_urls:
                unique_urls.append(u)

        if not unique_urls:
            raise HTTPException(status_code=400, detail="动态代理模式下必须提供至少一个 api_url")

        attempts = max(2, len(unique_urls))
        last_error = None
        for attempt in range(1, attempts + 1):
            if proxy_cfg.strategy == "random":
                selected_url = random.choice(unique_urls)
            else:
                selected_url = unique_urls[(attempt - 1) % len(unique_urls)]

            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(selected_url, timeout=15)
                proxy_text = r.text.strip()
                normalized_proxy = normalize_proxy_text(proxy_text)
                if attempt > 1:
                    print(f"[PROXY] 第 {attempt} 次重新获取代理成功: {selected_url}")
                return normalized_proxy
            except Exception as e:
                last_error = e
                print(f"[PROXY] 第 {attempt}/{attempts} 次获取代理失败: {e} (使用接口: {selected_url})")

        raise HTTPException(status_code=502, detail=f"获取动态代理失败: {last_error}")
    return ""


def create_proxy_probe_session(proxy_url: str):
    """创建用于代理探测的同步 HTTP 会话，复用协议流已验证可用的 SOCKS 实现。"""
    ctf_reg_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Gpt-Agreement-Payment-main", "CTF-reg")
    if ctf_reg_dir not in sys.path:
        sys.path.insert(0, ctf_reg_dir)

    from http_client import create_http_session

    return create_http_session(proxy=proxy_url)


async def detect_geo_from_proxy(proxy_url: str) -> tuple:
    """
    通过代理请求 ip-api.com 获取该代理的精确时区和所属国家代码。
    返回: (locale, timezone)
    """
    if not proxy_url:
        return None, None

    try:
        def _probe():
            session = create_proxy_probe_session(proxy_url)
            return session.get("http://ip-api.com/json/?lang=en", timeout=8)

        r = await asyncio.to_thread(_probe)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                country_code = data.get("countryCode")
                timezone = data.get("timezone")
                locale = DEFAULT_LOCALE_MAP.get(country_code, "en-US")
                return locale, timezone
    except Exception as e:
        print(f"[GEO] 基于代理定位 IP 归属失败: {e}")
    return None, None

def prepare_run_directory(run_id: str, req_data: dict) -> str:
    """创建本次运行的数据文件夹并保存输入配置"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    run_dir = os.path.join(current_dir, "runs", run_id)
    
    # 创建子目录
    os.makedirs(os.path.join(run_dir, "config"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "auth"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(run_dir, "debug"), exist_ok=True)
    
    # 保存原始输入配置
    with open(os.path.join(run_dir, "config", "input.json"), "w", encoding="utf-8") as f:
        json.dump(req_data, f, indent=2, ensure_ascii=False)
        
    return run_dir

class TestSMSRequest(BaseModel):
    provider: str
    api_key: str
    service: Optional[str] = "dr"

# 配置文件存储路径
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "config.json")

def load_stored_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# --- API Endpoints ---

@app.get("/api/config")
def get_config():
    """获取保存在后端的长效配置"""
    return load_stored_config()

@app.post("/api/config")
def save_config(config_data: dict):
    """保存配置到后端"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {e}")

@app.post("/api/sms/test")
async def test_sms_key(req: TestSMSRequest):
    """测试接码平台的 API Key 并获取账户余额"""
    if req.provider == "yihao":
        headers = {"Authorization": f"Bearer {req.api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                # 1. 验证 Key 并获取用户信息
                r_info = await client.get("https://YOUR_SMS_API_HOST/api/v1/info", headers=headers, timeout=10)
                info_res = r_info.json()
                if info_res.get("code") != 1:
                    raise HTTPException(status_code=400, detail=info_res.get("msg") or "一浩接码 Key 测试失败")
                
                return {
                    "ok": True,
                    "balance": f"{info_res['data'].get('money', 0)} USD"
                }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"一浩接口测试失败: {e}")
    else:
        # 传统接码平台 smsbower, smsactivate, herosms
        if req.provider == "herosms":
            base_url = "https://hero-sms.com/stubs/handler_api.php"
        elif req.provider == "smsbower":
            base_url = "https://smsbower.page/stubs/handler_api.php"
        elif req.provider == "smsactivate":
            base_url = "https://api.sms-activate.org/stubs/handler_api.php"
        else:
            raise HTTPException(status_code=400, detail="不支持的接码提供商")
        
        try:
            async with httpx.AsyncClient() as client:
                # 1. 验证 Key 并拉取余额
                r = await client.get(base_url, params={"api_key": req.api_key, "action": "getBalance"}, timeout=10)
                text = r.text
                if not text.startswith("ACCESS_BALANCE:"):
                    raise HTTPException(status_code=400, detail=f"接码平台返回: {text}")
                
                balance = text.split(":")[1]
                return {
                    "ok": True,
                    "balance": f"{balance} RUB/USD"
                }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"接口调用失败: {e}")

@app.post("/api/sms/sync")
async def sync_sms_goods(req: TestSMSRequest):
    """根据 API Key 和 Service 代码同步可用的商品或国家定价数据"""
    if req.provider == "yihao":
        headers = {"Authorization": f"Bearer {req.api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                # 1. 拉取一浩可用的商品列表
                r_goods = await client.get("https://YOUR_SMS_API_HOST/api/v1/goods", headers=headers, timeout=10)
                goods_res = r_goods.json()
                goods_list = goods_res.get("data", []) if goods_res.get("code") == 1 else []
                return {
                    "ok": True,
                    "goods": goods_list
                }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"一浩商品数据同步失败: {e}")
    else:
        # 传统接码平台 smsbower, smsactivate, herosms
        if req.provider == "herosms":
            base_url = "https://hero-sms.com/stubs/handler_api.php"
        elif req.provider == "smsbower":
            base_url = "https://smsbower.page/stubs/handler_api.php"
        elif req.provider == "smsactivate":
            base_url = "https://api.sms-activate.org/stubs/handler_api.php"
        else:
            raise HTTPException(status_code=400, detail="不支持的接码提供商")
        
        try:
            async with httpx.AsyncClient() as client:
                services_list = []
                selected_service = req.service or "dr"
                countries_list = []

                if req.provider == "smsbower":
                    country_map = {}
                    try:
                        r_countries = await client.get(
                            base_url,
                            params={"api_key": req.api_key, "action": "getCountries"},
                            timeout=12
                        )
                        countries_json = r_countries.json()
                        print(f"[SMS][SmsBower] getCountries status={r_countries.status_code} type={type(countries_json).__name__}")
                        if isinstance(countries_json, list):
                            for item in countries_json:
                                if not isinstance(item, dict):
                                    continue
                                country_id = item.get("id")
                                if country_id is None:
                                    continue
                                label = str(item.get("chn") or item.get("eng") or item.get("rus") or country_id)
                                aliases = {
                                    str(item.get("eng", "")).strip().lower(),
                                    str(item.get("chn", "")).strip().lower(),
                                    str(item.get("rus", "")).strip().lower(),
                                    str(country_id).strip().lower(),
                                }
                                aliases.discard("")
                                for alias in aliases:
                                    country_map[alias] = {"id": str(country_id), "name": label}
                            print(f"[SMS][SmsBower] countries loaded={len(countries_json)} aliases={len(country_map)}")
                        else:
                            print(f"[SMS][SmsBower] getCountries unexpected payload={str(countries_json)[:300]}")
                    except Exception as ex:
                        print(f"[SMS] 拉取 SmsBower 国家列表失败: {ex}")

                    try:
                        r_services = await client.get(
                            base_url,
                            params={"api_key": req.api_key, "action": "getServicesList"},
                            timeout=12
                        )
                        services_json = r_services.json()
                        print(f"[SMS][SmsBower] getServicesList status={r_services.status_code} type={type(services_json).__name__}")
                        if isinstance(services_json, dict) and services_json.get("status") == "success":
                            services_list = [
                                {"id": str(item.get("code", "")), "name": str(item.get("name", item.get("code", "")))}
                                for item in services_json.get("services", [])
                                if item.get("code")
                            ]
                            print(f"[SMS][SmsBower] services loaded={len(services_list)}")
                        else:
                            print(f"[SMS][SmsBower] getServicesList unexpected payload={str(services_json)[:300]}")
                    except Exception as ex:
                        print(f"[SMS] 拉取 SmsBower 商品列表失败: {ex}")

                    if services_list:
                        service_ids = {item["id"] for item in services_list}
                        if selected_service not in service_ids:
                            selected_service = services_list[0]["id"]
                    else:
                        services_list = [{"id": selected_service, "name": selected_service.upper()}]

                    try:
                        r_top = await client.get(
                            base_url,
                            params={"api_key": req.api_key, "action": "getTopCountriesByService", "service": selected_service},
                            timeout=12
                        )
                        top_json = r_top.json()
                        print(f"[SMS][SmsBower] getTopCountriesByService service={selected_service} status={r_top.status_code} type={type(top_json).__name__}")
                        if isinstance(top_json, dict):
                            for country_name, providers in top_json.items():
                                if not isinstance(providers, dict):
                                    continue
                                country_info = country_map.get(str(country_name).strip().lower(), {
                                    "id": str(country_name),
                                    "name": str(country_name)
                                })
                                prices = []
                                total_count = 0
                                for _, provider_info in providers.items():
                                    if not isinstance(provider_info, dict):
                                        continue
                                    price_val = provider_info.get("price")
                                    count_val = provider_info.get("count", 0)
                                    try:
                                        prices.append(float(price_val))
                                    except Exception:
                                        pass
                                    try:
                                        total_count += int(count_val)
                                    except Exception:
                                        pass
                                if prices:
                                    countries_list.append({
                                        "id": country_info["id"],
                                        "name": country_info["name"],
                                        "min_price": min(prices),
                                        "max_price": max(prices),
                                        "total_count": total_count or 0
                                    })
                            countries_list.sort(key=lambda x: x["total_count"], reverse=True)
                            print(f"[SMS][SmsBower] top countries loaded={len(countries_list)}")
                        else:
                            print(f"[SMS][SmsBower] getTopCountriesByService unexpected payload={str(top_json)[:300]}")
                    except Exception as ex:
                        print(f"[SMS] 拉取 SmsBower 国家价格失败: {ex}")
                else:
                    prices_json = {}
                    try:
                        r_prices = await client.get(
                            base_url,
                            params={"api_key": req.api_key, "action": "getPrices"},
                            timeout=12
                        )
                        prices_json = r_prices.json()
                    except Exception as ex:
                        print(f"[SMS] 拉取全量商品价格失败: {ex}")

                    if isinstance(prices_json, dict):
                        service_codes = set()
                        for _, country_val in prices_json.items():
                            if isinstance(country_val, dict):
                                for service_code in country_val.keys():
                                    if isinstance(service_code, str) and service_code:
                                        service_codes.add(service_code)

                        services_list = [
                            {"id": code, "name": code.upper()}
                            for code in sorted(service_codes)
                        ]

                        if services_list:
                            service_ids = {item["id"] for item in services_list}
                            if selected_service not in service_ids:
                                selected_service = services_list[0]["id"]

                        for c_id, val in prices_json.items():
                            if isinstance(val, dict):
                                service_info = val.get(selected_service)
                                if service_info is None:
                                    continue

                                min_p = None
                                max_p = None
                                total_count = 99

                                if isinstance(service_info, dict):
                                    prices_of_c = []
                                    total_count_sum = 0
                                    for p_str, count in service_info.items():
                                        try:
                                            p_val = float(p_str)
                                            prices_of_c.append(p_val)
                                            total_count_sum += int(count)
                                        except Exception:
                                            pass
                                    if prices_of_c:
                                        min_p = min(prices_of_c)
                                        max_p = max(prices_of_c)
                                        total_count = total_count_sum
                                else:
                                    try:
                                        p_val = float(service_info)
                                        min_p = p_val
                                        max_p = p_val
                                    except Exception:
                                        pass

                                if min_p is not None:
                                    c_name = SMS_COUNTRY_NAMES.get(c_id, f"国家 {c_id}")
                                    countries_list.append({
                                        "id": c_id,
                                        "name": f"{c_name}",
                                        "min_price": min_p,
                                        "max_price": max_p,
                                        "total_count": total_count
                                    })

                        countries_list.sort(key=lambda x: x["total_count"], reverse=True)

                    if not services_list:
                        services_list = [{"id": selected_service, "name": selected_service.upper()}]

                # 强力硬兜底：如果 API 实在拉不到定价，使用内置常用国家作为备选下拉列表以支持选择
                if not countries_list:
                    print("[SMS] API 定价为空，同步时使用内置国家进行硬兜底显示")
                    for c_id, c_name in SMS_COUNTRY_NAMES.items():
                        countries_list.append({
                            "id": c_id,
                            "name": c_name,
                            "min_price": 10.0,
                            "max_price": 50.0,
                            "total_count": 999
                        })

                return {
                    "ok": True,
                    "selected_service": selected_service,
                    "services": services_list,
                    "goods": countries_list
                }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"同步数据失败: {e}")

@app.get("/api/runs")
def list_runs(limit: int = 50):
    """列出最近历史运行状态"""
    return db.list_runs(limit)

@app.get("/api/runs/stats")
def get_run_stats():
    """获取历史运行成功率及常见错误统计"""
    runs = db.list_runs(limit=100)
    total = len(runs)
    if total == 0:
        return {"total": 0, "success": 0, "failed": 0, "success_rate": 0, "errors": {}}
    
    success = sum(1 for r in runs if r.get("status") == "success")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    
    error_stats = {}
    for r in runs:
        err = r.get("error_message")
        if err:
            simplified_err = err.split(":")[0] if ":" in err else err
            simplified_err = simplified_err[:60] + "..." if len(simplified_err) > 60 else simplified_err
            error_stats[simplified_err] = error_stats.get(simplified_err, 0) + 1
            
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "success_rate": round(success / total * 100, 1),
        "errors": error_stats
    }

@app.get("/api/runs/{run_id}/log")
def get_run_log(run_id: str):
    """读取并返回某个运行的详细日志或错误文件"""
    run_info = db.get_run(run_id)
    if not run_info:
        raise HTTPException(status_code=404, detail="未找到该运行记录")
    
    run_dir = run_info.get("data_path")
    error_file = os.path.join(run_dir, "debug", "error.json")
    if os.path.exists(error_file):
        try:
            with open(error_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
            
    oauth_error_file = os.path.join(run_dir, "debug", "oauth_error.json")
    if os.path.exists(oauth_error_file):
        try:
            with open(oauth_error_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
            
    return {"run_id": run_id, "status": run_info.get("status"), "message": "运行成功，无错误日志。"}

# --- API Endpoints ---

@app.post("/api/flow/run")
async def run_flow(req: FlowRequest):
    # 1. 生成唯一的 Run ID 并创建本地文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_suffix = uuid.uuid4().hex[:6]
    run_id = f"run_{timestamp}_{unique_suffix}"
    
    req_data = req.model_dump()
    run_dir = prepare_run_directory(run_id, req_data)
    
    try:
        # 2. 获取代理
        proxy_url = await get_proxy_url(req.proxy_config)
        
        # 如果开启了自动定位，通过代理检测地理归属并覆写 locale 和 timezone
        if req.gui_config.auto_detect_geo and proxy_url:
            print(f"[GEO] 正在通过代理 {proxy_url} 识别地理位置时区与语言环境...")
            det_locale, det_timezone = await detect_geo_from_proxy(proxy_url)
            if det_locale and det_timezone:
                print(f"[GEO] 自动识别成功 -> 时区: {det_timezone}, 区域语言: {det_locale}")
                req_data["gui_config"]["locale"] = det_locale
                req_data["gui_config"]["timezone"] = det_timezone
                # 同步更新存入 runs 文件夹下的 input.json 配置
                try:
                    with open(os.path.join(run_dir, "config", "input.json"), "w", encoding="utf-8") as f:
                        json.dump(req_data, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            else:
                print(f"[GEO] 自动识别未成功，将使用配置中的参数 -> 时区: {req.gui_config.timezone}, 区域语言: {req.gui_config.locale}")
        
        # 3. 在数据库中初始化运行记录
        db.create_run(
            run_id=run_id,
            flow_type=req.flow_type,
            data_path=run_dir,
            email_used=req.account_config.email,
            proxy_used=proxy_url
        )
        
        # 4. 根据 flow_type 执行对应分支 (协议直连方式，不使用浏览器)
        import asyncio
        loop = asyncio.get_event_loop()
        
        if req.flow_type == "register":
            # 协议注册分支
            # 传入 run_id 以便在注册过程中更新手机号
            result = await loop.run_in_executor(None, run_protocol_register, req_data, proxy_url, run_id)
        elif req.flow_type == "login":
            # 协议登录分支
            # 登录时手机号是已知的，直接更新到 runs 表
            db.update_run_phone(run_id, req.account_config.phone)
            result = await loop.run_in_executor(None, run_protocol_login, req_data, proxy_url)
        else:
            raise HTTPException(status_code=400, detail="不支持的 flow_type，仅支持 'register' 或 'login'")
            
        # 5. 运行成功，保存凭证文件并更新数据库
        # 保存 tokens.json
        tokens_data = {
            "access_token": result.get("accessToken"),
            "session_token": result.get("sessionToken"),
            "phone": result.get("phone")
        }
        with open(os.path.join(run_dir, "auth", "tokens.json"), "w", encoding="utf-8") as f:
            json.dump(tokens_data, f, indent=2, ensure_ascii=False)
            
        # 保存 cookies.json
        with open(os.path.join(run_dir, "auth", "cookies.json"), "w", encoding="utf-8") as f:
            json.dump(result.get("cookies", []), f, indent=2, ensure_ascii=False)
            
        # 更新 runs 表状态为 success
        db.update_run_status(run_id, "success")
        if req.flow_type == "register":
            db.update_run_phone(run_id, result.get("phone"))
            
        # 写入/更新 accounts 资产表
        db.upsert_account(
            phone=result.get("phone"),
            email=req.account_config.email,
            password=result.get("password") or req.account_config.password,
            access_token=result.get("accessToken"),
            session_token=result.get("sessionToken"),
            refresh_token=None,  # 此时还没有进行 Codex OAuth 授权
            run_id=run_id
        )
        
        # 返回结果中带上 run_id，方便前端后续调用 OAuth 接口
        result["run_id"] = run_id
        return result
        
    except Exception as e:
        error_msg = str(e)
        # 运行失败，更新数据库状态并保存错误日志
        db.update_run_status(run_id, "failed", error_msg)
        
        error_data = {
            "run_id": run_id,
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        }
        with open(os.path.join(run_dir, "debug", "error.json"), "w", encoding="utf-8") as f:
            json.dump(error_data, f, indent=2, ensure_ascii=False)
            
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/api/flow/oauth")
async def run_oauth(req: FlowRequest, login_result: Dict[str, Any]):
    """
    OAuth 授权接口，需要传入完整的配置以及之前登录/注册成功返回的 login_result (包含 cookies 和 run_id)
    """
    run_id = login_result.get("run_id")
    run_dir = None
    if run_id:
        run_info = db.get_run(run_id)
        if run_info:
            run_dir = run_info.get("data_path")
            
    try:
        proxy_url = await get_proxy_url(req.proxy_config)
        result = await run_oauth_branch(req.model_dump(), proxy_url, login_result)
        
        # 授权成功，更新 accounts 表中的 refresh_token
        phone = login_result.get("phone")
        if phone:
            db.upsert_account(
                phone=phone,
                email=req.account_config.email,
                password=login_result.get("password") or req.account_config.password,
                access_token=result.get("access_token"),
                session_token=login_result.get("sessionToken"),
                refresh_token=result.get("refresh_token"),
                run_id=run_id
            )
            
        # 如果找到了运行目录，将 refresh_token 也追加保存到本地 tokens.json 中
        if run_dir and os.path.exists(os.path.join(run_dir, "auth", "tokens.json")):
            try:
                with open(os.path.join(run_dir, "auth", "tokens.json"), "r", encoding="utf-8") as f:
                    tokens_data = json.load(f)
                tokens_data["refresh_token"] = result.get("refresh_token")
                tokens_data["oauth_access_token"] = result.get("access_token")
                with open(os.path.join(run_dir, "auth", "tokens.json"), "w", encoding="utf-8") as f:
                    json.dump(tokens_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[OAuth] 追加保存 Token 文件失败: {e}")
                
        return result
    except Exception as e:
        if run_dir:
            error_data = {
                "error": f"OAuth 授权失败: {e}",
                "timestamp": datetime.now().isoformat()
            }
            with open(os.path.join(run_dir, "debug", "oauth_error.json"), "w", encoding="utf-8") as f:
                json.dump(error_data, f, indent=2, ensure_ascii=False)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/accounts")
def list_accounts(status: Optional[str] = None):
    """获取所有已注册/登录的账号资产列表"""
    return db.list_accounts(status)

@app.get("/api/accounts/{phone}")
def get_account(phone: str):
    """获取单个账号资产的详细信息"""
    account = db.get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="未找到该账号")
    return account

# 挂载前端静态文件
# 生产模式：显式返回 Vite index.html，并挂载 /assets，避免旧 static 根挂载缓存/冲突
# 开发模式：访问 http://127.0.0.1:5173，由 Vite 通过 proxy 转发 /api 到本服务
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_vite_dist = os.path.join(_project_root, "web", "dist")
_vite_assets = os.path.join(_vite_dist, "assets")
_legacy_static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

if os.path.exists(_vite_dist):
    if os.path.exists(_vite_assets):
        app.mount("/assets", StaticFiles(directory=_vite_assets), name="vite-assets")

    @app.get("/")
    def read_vite_index():
        return FileResponse(os.path.join(_vite_dist, "index.html"))

    @app.get("/{full_path:path}")
    def read_vite_spa(full_path: str):
        # API 路由已在前面注册；这里仅用于 Vue Router history fallback
        return FileResponse(os.path.join(_vite_dist, "index.html"))
elif os.path.exists(_legacy_static):
    app.mount("/", StaticFiles(directory=_legacy_static, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("sms_flow_project.main:app", host="127.0.0.1", port=18000, reload=True)
