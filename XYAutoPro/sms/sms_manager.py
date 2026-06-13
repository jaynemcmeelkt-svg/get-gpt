import httpx
import asyncio
import re
import random

class SMSProviderError(RuntimeError):
    """接码平台基础异常"""
    pass

class InsufficientBalanceError(SMSProviderError):
    """接码平台余额不足"""
    pass

class InvalidApiKeyError(SMSProviderError):
    """接码平台 API Key 无效或错误"""
    pass

class NoAvailableNumbersError(SMSProviderError):
    """所有配置国家均无号或超限价"""
    pass

class DynamicSMSProvider:
    def __init__(self, sms_setting: dict):
        self.provider = sms_setting.get("provider", "herosms")
        self.mode = sms_setting.get("mode", "temp") # "temp" 或 "fixed"
        self.api_key = sms_setting.get("api_key")
        self.service = sms_setting.get("service", "ot")
        self.country = sms_setting.get("country")
        self.preferred_countries = sms_setting.get("preferred_countries") or []
        self.min_price = sms_setting.get("min_price")
        self.max_price = sms_setting.get("max_price")
        self.otp_timeout_seconds = max(30, int(sms_setting.get("otp_timeout_seconds", 180) or 180))
        self.otp_retry_attempts = max(1, int(sms_setting.get("otp_retry_attempts", 3) or 3))
        self.phone_exception = sms_setting.get("phone_exception")
        self.last_cost = 0.0  # 用于记录最近一次的真实扣费价格
        
        # 固定号码配置
        self.fixed_config = sms_setting.get("fixed_config", {})
        
        # 根据不同平台配置基础 URL
        if self.provider == "herosms":
            self.base_url = "https://hero-sms.com/stubs/handler_api.php"
        elif self.provider == "smsbower":
            self.base_url = "https://smsbower.page/stubs/handler_api.php"
        elif self.provider == "smsactivate":
            self.base_url = "https://api.sms-activate.org/stubs/handler_api.php"
        elif self.provider == "yihao":
            self.base_url = "https://YOUR_SMS_API_HOST"
        else:
            self.base_url = "https://hero-sms.com/stubs/handler_api.php"

        # 数据库挂钩与采购指标状态追踪
        from core.phone_db import PhoneDB
        self.db = PhoneDB()
        self.last_country = None
        self.last_operator = None

    def _parse_fixed_sms_entries(self) -> list[dict]:
        """解析固定号码短信拉取配置，兼容 `号码|URL` 和纯 URL 两种格式"""
        fixed_cfg = self.fixed_config or {}
        raw_entries = []

        config_urls = fixed_cfg.get("sms_urls")
        if config_urls:
            raw_entries.extend([u.strip() for u in config_urls if isinstance(u, str) and u.strip()])

        single_url = fixed_cfg.get("sms_url")
        if isinstance(single_url, str) and single_url.strip():
            raw_entries.append(single_url.strip())

        parsed_entries = []
        seen_urls = set()
        for raw in raw_entries:
            phone = ""
            url = raw
            if "|" in raw:
                left, right = raw.split("|", 1)
                phone = left.strip()
                url = right.strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            parsed_entries.append({"phone": phone, "url": url})

        return parsed_entries

    async def _select_fixed_sms_entry(self) -> dict:
        entries = self._parse_fixed_sms_entries()
        if not entries:
            raise ValueError("固定号码模式下必须提供至少一个 sms_url 短信拉取链接")

        strategy = (self.fixed_config or {}).get("strategy", "fixed")
        if strategy == "random":
            entry = random.choice(entries)
            print(f"[SMS] 固定号码模式：使用随机策略选择号码/链接: {entry['phone'] or '(未配置号码)'} -> {entry['url']}")
            return entry

        entry = entries[0]
        print(f"[SMS] 固定号码模式：使用固定策略选择号码/链接: {entry['phone'] or '(未配置号码)'} -> {entry['url']}")
        return entry

    async def _query_country_price(self, country_id: str, client) -> float | None:
        """查询单个国家的服务价格，返回 float 或 None"""
        params = {
            "api_key": self.api_key,
            "action": "getPrices",
            "service": self.service,
            "country": country_id
        }
        try:
            r = await client.get(self.base_url, params=params, timeout=10)
            text = r.text.strip()
            if text == "BAD_KEY":
                raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
            elif text == "NO_BALANCE":
                raise InsufficientBalanceError("接码服务商账户余额不足")
            res = r.json()
            country_data = res.get(str(country_id), {})
            service_data = country_data.get(self.service, {})
            price = None
            if isinstance(service_data, dict):
                price = service_data.get("cost")
            elif isinstance(service_data, list) and len(service_data) > 0:
                price = service_data[0].get("cost")
            if price is not None:
                return float(price)
        except SMSProviderError:
            raise
        except Exception as e:
            print(f"[SMS] 查询国家 {country_id} 价格异常: {e}")
        return None

    def _price_in_range(self, price: float) -> bool:
        """检查价格是否在 min_price ~ max_price 范围内"""
        if self.max_price is not None and price > self.max_price:
            return False
        if self.min_price is not None and price < self.min_price:
            return False
        return True

    async def _get_detailed_prices(self) -> dict[str, tuple[float, str]]:
        """通过 getTopCountriesByService 获取所有热门国家的真实最低价供应商价格，返回 {country_id: (price, operator_id)}"""
        detailed_prices = {}
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(
                    self.base_url,
                    params={
                        "api_key": self.api_key,
                        "action": "getTopCountriesByService",
                        "service": self.service,
                    },
                    timeout=12,
                )
                text = r.text.strip()
                if text == "BAD_KEY":
                    raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                elif text == "NO_BALANCE":
                    raise InsufficientBalanceError("接码服务商账户余额不足")
                top_data = r.json()
                if isinstance(top_data, dict):
                    name_to_id = {
                        "portugal": "117", "india": "22", "indonesia": "6", "philippines": "4",
                        "ukraine": "1", "kazakhstan": "2", "colombia": "33", "japan": "1001",
                        "russia": "0", "united-states": "12", "united-kingdom": "44",
                        "chile": "151", "nigeria": "19", "south-africa": "31", "saudi-arabia": "29",
                        "ghana": "38", "canada": "32", "germany": "16", "spain": "34",
                        "france": "15", "malaysia": "7", "australia": "45", "newzealand": "37",
                    }
                    # 动态读取并拉取服务商级别的黑名单与优质运营商数据库记录
                    blacklisted_ops = self.db.get_blacklisted_operators(self.service)
                    if blacklisted_ops:
                        print(f"[SMS] 载入供应商黑名单: {blacklisted_ops}")
                    
                    premium_ops = self.db.get_premium_operators(self.service, threshold=20)
                    if premium_ops:
                        print(f"[SMS] 载入优质供应商白名单: {premium_ops}")

                    for country_name, providers in top_data.items():
                        if not isinstance(providers, dict):
                            continue
                        cid = name_to_id.get(country_name.lower())
                        if not cid or cid in ("12", "32"):  # 自动跳过美国(12)和加拿大(32) VoIP号源
                            continue

                        # 筛选出最低价格的运营商，完美排除黑名单，且优先选择优质运营商
                        min_price_premium = None
                        min_op_premium = None
                        min_price_regular = None
                        min_op_regular = None

                        for op_id, op_info in providers.items():
                            if not isinstance(op_info, dict):
                                continue
                            op_id_str = str(op_id)
                            cid_str = str(cid)
                            if (cid_str, op_id_str) in blacklisted_ops:
                                print(f"[SMS] 供应商 {op_id_str} (国家 {cid_str}) 处于动态风控黑名单中，过滤跳过采购！")
                                continue
                            try:
                                price_val = float(op_info.get("price") or op_info.get("cost"))
                                if (cid_str, op_id_str) in premium_ops:
                                    if min_price_premium is None or price_val < min_price_premium:
                                        min_price_premium = price_val
                                        min_op_premium = op_id_str
                                else:
                                    if min_price_regular is None or price_val < min_price_regular:
                                        min_price_regular = price_val
                                        min_op_regular = op_id_str
                            except (TypeError, ValueError):
                                continue

                        if min_price_premium is not None:
                            detailed_prices[str(cid)] = (min_price_premium, min_op_premium)
                        elif min_price_regular is not None:
                            detailed_prices[str(cid)] = (min_price_regular, min_op_regular)

            except SMSProviderError:
                raise
            except Exception as e:
                print(f"[SMS] 通过 getTopCountriesByService 获取明细价格异常: {e}")
        return detailed_prices

    async def _get_priced_whitelist_countries(self) -> list[tuple[float, str, str]]:
        """批量查询白名单国家价格，优先使用明细数据，无明细时采用单国精准查价，返回 (price, country_id, operator_id) 列表"""
        if not self.preferred_countries:
            return []
        
        detailed_prices = await self._get_detailed_prices()
        priced = []
        async with httpx.AsyncClient() as client:
            for country_id in self.preferred_countries:
                country_str = str(country_id)
                if country_str in ("12", "32"):  # 自动跳过美国(12)和加拿大(32) VoIP号源
                    print(f"[SMS] 白名单国家 {country_str} 是美国/加拿大VoIP号源，自动跳过")
                    continue
                price = None
                op_id = None
                
                if country_str in detailed_prices:
                    price, op_id = detailed_prices[country_str]
                else:
                    price = await self._query_country_price(country_str, client)
                
                if price is None:
                    print(f"[SMS] 白名单国家 {country_str}: 无价格信息，跳过")
                    continue
                if not self._price_in_range(price):
                    print(f"[SMS] 白名单国家 {country_str}: 最低价={price:.3f}，超出限价[{self.min_price}, {self.max_price}]，跳过")
                    continue
                priced.append((price, country_str, op_id))
                
            priced.sort(key=lambda x: x[0])
        return priced

    async def _get_all_priced_countries(self) -> list[tuple[float, str, str]]:
        """获取全平台所有国家价格（基于明细查价），返回 [(price, country_id, operator_id), ...] 升序"""
        detailed_prices = await self._get_detailed_prices()
        priced = []
        for cid, val in detailed_prices.items():
            price, op_id = val
            if cid in ("12", "32"):  # 自动跳过美国(12)和加拿大(32) VoIP号源
                continue
            if self._price_in_range(price):
                priced.append((price, cid, op_id))
        priced.sort(key=lambda x: x[0])
        return priced

    async def _place_order(self, country_id: str, operator_id: str = None):
        """直接下单（不查价格，价格已在调用方确认过），支持显式传入运营商 ID"""
        async with httpx.AsyncClient() as client:
            params = {
                "api_key": self.api_key,
                "action": "getNumber",
                "service": self.service,
                "country": country_id
            }
            if operator_id:
                params["operator"] = str(operator_id)
            if self.max_price is not None:
                params["maxPrice"] = str(self.max_price)
            if self.phone_exception:
                params["phoneException"] = self.phone_exception
            try:
                r = await client.get(self.base_url, params=params, timeout=15)
                text = r.text.strip()
                if text == "BAD_KEY":
                    raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                elif text == "NO_BALANCE":
                    raise InsufficientBalanceError("接码服务商账户余额不足")
                if r.text.startswith("ACCESS_NUMBER"):
                    parts = r.text.split(":")
                    # 记录最后成功获取的参数，用于失败风控定位
                    self.last_country = str(country_id)
                    self.last_operator = str(operator_id) if operator_id else "default"
                    print(f"[SMS] 成功获取号码: {parts[2]} | 运营商: {self.last_operator}")
                    return parts[1], parts[2]
                elif "BANNED" in r.text or "NO_NUMBERS" in r.text:
                    print(f"[SMS] 国家 {country_id} 暂时无号或被封禁: {r.text}")
                    return None
                else:
                    print(f"[SMS] 国家 {country_id} 下单返回: {r.text}")
                    return None
            except SMSProviderError:
                raise
            except Exception as e:
                print(f"[SMS] 请求国家 {country_id} 下单异常: {e}")
                return None

    async def _check_price_and_get(self, country_id: str):
        """检查特定国家的价格是否符合限价，如果符合则下单，否则返回 None"""
        async with httpx.AsyncClient() as client:
            need_price_check = (self.max_price is not None or self.min_price is not None)
            price_val_to_save = 0.0
            if need_price_check and self.provider in ("smsactivate", "smsbower", "herosms"):
                params = {
                    "api_key": self.api_key,
                    "action": "getPrices",
                    "service": self.service,
                    "country": country_id
                }
                try:
                    r = await client.get(self.base_url, params=params, timeout=10)
                    text = r.text.strip()
                    if text == "BAD_KEY":
                        raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                    elif text == "NO_BALANCE":
                        raise InsufficientBalanceError("接码服务商账户余额不足")
                    res = r.json()
                    country_data = res.get(str(country_id), {})
                    service_data = country_data.get(self.service, {})

                    price = None
                    if isinstance(service_data, dict):
                        price = service_data.get("cost")
                    elif isinstance(service_data, list) and len(service_data) > 0:
                        price = service_data[0].get("cost")

                    if price is not None:
                        price = float(price)
                        print(f"[SMS] 国家 {country_id} 价格: {price}")
                        if self.max_price is not None and price > self.max_price:
                            print(f"[SMS] 国家 {country_id} 价格为 {price}，超过上限限价 {self.max_price}，跳过")
                            return None
                        if self.min_price is not None and price < self.min_price:
                            print(f"[SMS] 国家 {country_id} 价格为 {price}，低于下限价格 {self.min_price}，跳过")
                            return None
                        price_val_to_save = price
                except SMSProviderError:
                    raise
                except Exception as e:
                    print(f"[SMS] 查询价格异常: {e}，为了防止高价扣费，放弃本次下单")
                    return None

            # 尝试下单
            params = {
                "api_key": self.api_key,
                "action": "getNumber",
                "service": self.service,
                "country": country_id
            }
            if self.max_price is not None:
                params["maxPrice"] = str(self.max_price)
            if self.phone_exception:
                params["phoneException"] = self.phone_exception
            try:
                r = await client.get(self.base_url, params=params, timeout=15)
                text = r.text.strip()
                if text == "BAD_KEY":
                    raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                elif text == "NO_BALANCE":
                    raise InsufficientBalanceError("接码服务商账户余额不足")
                if r.text.startswith("ACCESS_NUMBER"):
                    parts = r.text.split(":")
                    self.last_cost = price_val_to_save  # 记录扣费价格
                    return parts[1], parts[2] # activation_id, phone
                elif "BANNED" in r.text or "NO_NUMBERS" in r.text:
                    print(f"[SMS] 国家 {country_id} 暂时无号或被封禁: {r.text}")
                    return None
                else:
                    print(f"[SMS] 国家 {country_id} 下单返回: {r.text}")
                    return None
            except SMSProviderError:
                raise
            except Exception as e:
                print(f"[SMS] 请求国家 {country_id} 下单异常: {e}")
                return None

    async def _get_lowest_price_country_candidates(self) -> list[str]:
        """获取当前服务可用的最低价国家候选列表（按价格升序）。"""
        candidates = []
        seen = set()

        async with httpx.AsyncClient() as client:
            # 1. 优先尝试传统 getPrices 接口
            try:
                params = {
                    "api_key": self.api_key,
                    "action": "getPrices",
                    "service": self.service,
                }
                r = await client.get(self.base_url, params=params, timeout=12)
                text = r.text.strip()
                if text == "BAD_KEY":
                    raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                elif text == "NO_BALANCE":
                    raise InsufficientBalanceError("接码服务商账户余额不足")
                price_data = r.json()
                if isinstance(price_data, dict):
                    priced = []
                    for country_id, country_info in price_data.items():
                        if not isinstance(country_info, dict):
                            continue
                        service_info = country_info.get(self.service, {})

                        price = None
                        if isinstance(service_info, dict):
                            price = service_info.get("cost")
                        elif isinstance(service_info, list) and service_info:
                            first = service_info[0]
                            if isinstance(first, dict):
                                price = first.get("cost")

                        try:
                            price_val = float(price)
                        except (TypeError, ValueError):
                            continue

                        if self.max_price is not None and price_val > self.max_price:
                            continue
                        if self.min_price is not None and price_val < self.min_price:
                            continue
                        if str(country_id) in ("12", "32"):  # 自动跳过美国(12)和加拿大(32) VoIP号源
                            continue
                        priced.append((price_val, str(country_id)))

                    priced.sort(key=lambda item: item[0])
                    for _, country_id in priced:
                        if country_id not in seen:
                            seen.add(country_id)
                            candidates.append(country_id)
            except SMSProviderError:
                raise
            except Exception as e:
                print(f"[SMS] 拉取最低价国家列表失败，将尝试平台兼容回退: {e}")

            # 2. SmsBower 兼容回退：用 top countries 近似最低价候选
            if not candidates and self.provider == "smsbower":
                try:
                    r = await client.get(
                        self.base_url,
                        params={
                            "api_key": self.api_key,
                            "action": "getTopCountriesByService",
                            "service": self.service,
                        },
                        timeout=12,
                    )
                    text = r.text.strip()
                    if text == "BAD_KEY":
                        raise InvalidApiKeyError("接码服务商 API Key 无效或错误")
                    elif text == "NO_BALANCE":
                        raise InsufficientBalanceError("接码服务商账户余额不足")
                    top_data = r.json()
                    priced = []
                    if isinstance(top_data, dict):
                        for country_name, providers in top_data.items():
                            if not isinstance(providers, dict):
                                continue
                            prices = []
                            for provider_info in providers.values():
                                if not isinstance(provider_info, dict):
                                    continue
                                try:
                                    price_val = float(provider_info.get("price"))
                                except (TypeError, ValueError):
                                    continue
                                if self.max_price is not None and price_val > self.max_price:
                                    continue
                                if self.min_price is not None and price_val < self.min_price:
                                    continue
                                prices.append(price_val)
                            if prices:
                                # 检查是否为美国或加拿大，若是则跳过
                                name_to_id = {
                                    "portugal": "117", "india": "22", "indonesia": "6", "philippines": "4",
                                    "ukraine": "1", "kazakhstan": "2", "colombia": "33", "japan": "1001",
                                    "russia": "0", "united-states": "12", "united-kingdom": "44",
                                    "chile": "151", "nigeria": "19", "south-africa": "31", "saudi-arabia": "29",
                                    "ghana": "38", "canada": "32", "germany": "16", "spain": "34",
                                    "france": "15", "malaysia": "7", "australia": "45", "newzealand": "37",
                                }
                                cid = name_to_id.get(country_name.lower())
                                if cid in ("12", "32"):  # 自动跳过美国(12)和加拿大(32) VoIP号源
                                    continue
                                priced.append((min(prices), str(country_name)))

                    priced.sort(key=lambda item: item[0])
                    for _, country_id in priced:
                        if country_id not in seen:
                            seen.add(country_id)
                            candidates.append(country_id)
                except SMSProviderError:
                    raise
                except Exception as e:
                    print(f"[SMS] 拉取 SmsBower 最低价候选失败: {e}")

        return candidates

    async def get_number(self) -> tuple[str, str]:
        """获取手机号，返回 (activation_id, phone)"""
        # 如果是固定号码模式，直接返回配置的号码，activation_id 设为 "fixed"
        if self.mode == "fixed":
            phone = (self.fixed_config or {}).get("phone")
            if phone:
                return "fixed", phone

            entry = await self._select_fixed_sms_entry()
            if entry.get("phone"):
                return "fixed", entry["phone"]

            raise ValueError("固定号码模式下必须提供 fixed_config.phone，或在 sms_url/sms_urls 中使用 `号码|URL` 格式")

        if self.provider == "yihao":
            # 一浩接码平台下单接口 (POST /api/v1/get)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            payload = {
                "goods_id": self.service, # 对应 goods_id，如 "12-1-7"
                "num": 1
            }
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.post(f"{self.base_url}/api/v1/get", json=payload, headers=headers, timeout=15)
                    res = r.json()
                    if res.get("code") == 1:
                        token_info = res["data"]["tokens"][0]
                        # 一浩的 token 相当于传统接码的 activation_id
                        return token_info["token"], token_info["number"]
                    msg = res.get("msg") or ""
                    if "余额" in msg or "不足" in msg:
                        raise InsufficientBalanceError(f"一浩接码下单失败: {msg}")
                    elif "Key" in msg or "Token" in msg or "权限" in msg or "秘钥" in msg:
                        raise InvalidApiKeyError(f"一浩接码下单失败: {msg}")
                    else:
                        raise SMSProviderError(f"一浩接码下单失败: {msg}")
            except SMSProviderError:
                raise
            except Exception as e:
                raise RuntimeError(f"请求一浩接码平台失败: {e}")
        else:
            # 传统接码平台 (herosms, smsbower, smsactivate)
            # 策略：先批量查询价格 → 价格过滤 → 白名单优先 → 按价格升序下单
            priced_countries = await self._get_priced_whitelist_countries()
            all_priced = await self._get_all_priced_countries()

            # 载入优质运营商白名单，执行双重排序机制：优质运营商优先，其次按价格升序
            premium_ops = self.db.get_premium_operators(self.service, threshold=20)
            
            def sort_by_premium_priority(item):
                price_val, country_id, op_id = item
                is_premium = (str(country_id), str(op_id)) in premium_ops if op_id else False
                return (0 if is_premium else 1, price_val)

            priced_countries.sort(key=sort_by_premium_priority)
            all_priced.sort(key=sort_by_premium_priority)

            # 白名单国家（价格过滤后）优先
            if priced_countries:
                print(f"[SMS] 白名单国家(价格过滤后): {[(c, f'{p:.3f}') for p, c, _ in priced_countries]}")
                for price_val, country_id, op_id in priced_countries:
                    print(f"[SMS] 白名单国家下单: {country_id} (价格={price_val:.3f}, 运营商={op_id})")
                    result = await self._place_order(str(country_id), op_id)
                    if result:
                        self.last_cost = price_val  # 记录真实扣费价格
                        return result

            # 白名单全部无号，尝试默认国家
            if self.country and str(self.country) not in ("12", "32"):
                # 检查默认国家是否在 all_priced 中且通过价格过滤
                default_ok = False
                for price_val, country_id, op_id in all_priced:
                    if str(country_id) == str(self.country):
                        print(f"[SMS] 默认国家下单: {self.country} (价格={price_val:.3f}, 运营商={op_id})")
                        result = await self._place_order(str(self.country), op_id)
                        if result:
                            self.last_cost = price_val  # 记录真实扣费价格
                            return result
                        default_ok = True
                        break
                if not default_ok:
                    print(f"[SMS] 尝试默认国家(无价格信息): {self.country}")
                    result = await self._check_price_and_get(str(self.country))
                    if result:
                        return result

            # 全部失败，按全平台最低价国家列表继续
            tried = {str(c) for _, c, _ in priced_countries}
            if self.country:
                tried.add(str(self.country))
            for price_val, country_id, op_id in all_priced:
                if str(country_id) in tried:
                    continue
                tried.add(str(country_id))
                print(f"[SMS] 最低价国家下单: {country_id} (价格={price_val:.3f}, 运营商={op_id})")
                result = await self._place_order(str(country_id), op_id)
                if result:
                    self.last_cost = price_val  # 记录真实扣费价格
                    return result

            raise NoAvailableNumbersError("所有配置国家与最低价候选国家均无法成功下单（可能无号、超限价或接口报错）")

    async def get_otp(self, activation_id: str, timeout: int = 120) -> str:
        """轮询获取验证码"""
        # 如果是固定号码模式，根据配置的池和策略提取 sms_url
        if self.mode == "fixed":
            entry = await self._select_fixed_sms_entry()
            sms_url = entry["url"]

            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < timeout:
                await asyncio.sleep(3)
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(sms_url, timeout=10)
                        # 响应格式示例: "no|暂无验证码" 或 "ok|123456"
                        text = r.text.strip()
                        if text.startswith("ok|"):
                            return text.split("|")[1]
                        elif "|" in text:
                            # 兼容其他格式，如 "yes|123456" 或直接返回验证码
                            parts = text.split("|")
                            if len(parts) > 1 and parts[1].isdigit():
                                return parts[1]
                except Exception as e:
                    print(f"[SMS] 固定号码拉取异常: {e} (URL: {sms_url})")
            raise TimeoutError(f"等待固定号码验证码超时 (使用链接: {sms_url})")

        if self.provider == "yihao":
            # 一浩接码平台获取短信接口 (GET /api/v1/msg)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            params = {
                "token": activation_id, # 传入下单时返回的 token
                "limit": 5
            }
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < timeout:
                await asyncio.sleep(3)
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(f"{self.base_url}/api/v1/msg", params=params, headers=headers, timeout=10)
                        res = r.json()
                        if res.get("code") == 1 and res.get("data"):
                            # 假设返回 of data 是短信记录列表，提取最新一条短信中的验证码
                            sms_list = res["data"]
                            if sms_list:
                                latest_sms = sms_list[0].get("sms", "")
                                # 正则匹配 6 位数字验证码
                                code_match = re.search(r"\b\d{6}\b", latest_sms)
                                if code_match:
                                    return code_match.group(0)
                except Exception as e:
                    print(f"[SMS] 一浩轮询异常: {e}")
            raise TimeoutError("等待一浩验证码超时")
        else:
            # 传统接码平台轮询
            params = {
                "api_key": self.api_key,
                "action": "getStatus",
                "id": activation_id
            }
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < timeout:
                await asyncio.sleep(3)
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(self.base_url, params=params, timeout=10)
                        if r.text.startswith("STATUS_OK"):
                            return r.text.split(":")[1]
                        elif r.text.startswith("STATUS_CANCEL"):
                            raise RuntimeError("接码任务被取消")
                except Exception as e:
                    print(f"[SMS] 轮询异常: {e}")
            raise TimeoutError("等待验证码超时")

    async def set_status(self, activation_id: str, status: int) -> str | None:
        """反馈状态，返回响应文本或None"""
        if self.provider == "yihao" or self.mode == "fixed":
            return None
        params = {
            "api_key": self.api_key,
            "action": "setStatus",
            "id": activation_id,
            "status": str(status)
        }
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(self.base_url, params=params, timeout=10)
                return r.text.strip()
        except Exception as e:
            print(f"[SMS] 反馈状态异常: {e}")
            return None

    async def cancel_activation(self, activation_id: str, max_retries: int = 3):
        """取消当前激活，处理 EARLY_CANCEL_DENIED（购买后2分钟内不可取消，需等待重试）"""
        if not activation_id or activation_id == "fixed":
            return
        if self.provider == "yihao" or self.mode == "fixed":
            return

        for attempt in range(max_retries):
            resp = await self.set_status(activation_id, 8)
            if resp is None:
                print(f"[SMS] 取消激活请求失败(第{attempt+1}次)")
                await asyncio.sleep(5)
                continue

            if resp == "ACCESS_CANCEL":
                print(f"[SMS] 取消激活成功: {activation_id}")
                return
            elif resp == "EARLY_CANCEL_DENIED":
                wait_sec = 125 if attempt == 0 else 60
                print(f"[SMS] 取消激活被拒(购买未满2分钟)，等待{wait_sec}秒后重试(第{attempt+1}/{max_retries}次)")
                await asyncio.sleep(wait_sec)
            elif resp == "NO_ACTIVATION":
                print(f"[SMS] 取消激活: 激活不存在(id={activation_id})，跳过")
                return
            elif resp == "BAD_STATUS":
                print(f"[SMS] 取消激活: 状态不正确(id={activation_id})，可能已取消/完成")
                return
            elif resp == "BAD_KEY":
                print(f"[SMS] 取消激活: API密钥错误")
                return
            elif resp == "BAD_ACTION":
                print(f"[SMS] 取消激活: 动作参数错误")
                return
            else:
                print(f"[SMS] 取消激活未知响应: {resp} (id={activation_id})")
                return

        print(f"[SMS] 取消激活重试耗尽({max_retries}次)，放弃: {activation_id}")
