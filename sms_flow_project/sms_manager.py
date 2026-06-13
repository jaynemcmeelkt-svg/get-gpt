import httpx
import asyncio
import re
import random

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

    async def _get_priced_whitelist_countries(self) -> list[tuple[float, str]]:
        """批量查询白名单国家价格，过滤+排序，返回 [(price, country_id), ...] 升序"""
        if not self.preferred_countries:
            return []
        async with httpx.AsyncClient() as client:
            priced = []
            for country_id in self.preferred_countries:
                country_str = str(country_id)
                price = await self._query_country_price(country_str, client)
                if price is None:
                    print(f"[SMS] 白名单国家 {country_str}: 无价格信息，跳过")
                    continue
                if not self._price_in_range(price):
                    print(f"[SMS] 白名单国家 {country_str}: 价格={price:.3f}，超出限价[{self.min_price}, {self.max_price}]，跳过")
                    continue
                priced.append((price, country_str))
            priced.sort(key=lambda x: x[0])
        return priced

    async def _get_all_priced_countries(self) -> list[tuple[float, str]]:
        """获取全平台所有国家价格（过滤+排序），返回 [(price, country_id), ...] 升序"""
        return await self._get_lowest_price_country_candidates_priced()

    async def _get_lowest_price_country_candidates_priced(self) -> list[tuple[float, str]]:
        """获取全平台最低价国家候选（带价格，已过滤排序）"""
        candidates = []
        async with httpx.AsyncClient() as client:
            try:
                params = {
                    "api_key": self.api_key,
                    "action": "getPrices",
                    "service": self.service,
                }
                r = await client.get(self.base_url, params=params, timeout=12)
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
                        if not self._price_in_range(price_val):
                            continue
                        priced.append((price_val, str(country_id)))
                    priced.sort(key=lambda item: item[0])
                    candidates = priced
            except Exception as e:
                print(f"[SMS] 拉取全平台价格列表失败: {e}")

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
                                if not self._price_in_range(price_val):
                                    continue
                                prices.append(price_val)
                            if prices:
                                priced.append((min(prices), str(country_name)))
                    priced.sort(key=lambda item: item[0])
                    candidates = priced
                except Exception as e:
                    print(f"[SMS] 拉取 SmsBower 最低价候选失败: {e}")

        return candidates

    async def _place_order(self, country_id: str):
        """直接下单（不查价格，价格已在调用方确认过）"""
        async with httpx.AsyncClient() as client:
            params = {
                "api_key": self.api_key,
                "action": "getNumber",
                "service": self.service,
                "country": country_id
            }
            try:
                r = await client.get(self.base_url, params=params, timeout=15)
                if r.text.startswith("ACCESS_NUMBER"):
                    parts = r.text.split(":")
                    return parts[1], parts[2]
                elif "BANNED" in r.text or "NO_NUMBERS" in r.text:
                    print(f"[SMS] 国家 {country_id} 暂时无号或被封禁: {r.text}")
                    return None
                else:
                    print(f"[SMS] 国家 {country_id} 下单返回: {r.text}")
                    return None
            except Exception as e:
                print(f"[SMS] 请求国家 {country_id} 下单异常: {e}")
                return None

    async def _check_price_and_get(self, country_id: str):
        """检查特定国家的价格是否符合限价，如果符合则下单，否则返回 None"""
        async with httpx.AsyncClient() as client:
            need_price_check = (self.max_price is not None or self.min_price is not None)
            if need_price_check and self.provider in ("smsactivate", "smsbower", "herosms"):
                params = {
                    "api_key": self.api_key,
                    "action": "getPrices",
                    "service": self.service,
                    "country": country_id
                }
                try:
                    r = await client.get(self.base_url, params=params, timeout=10)
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
                except Exception as e:
                    print(f"[SMS] 查询价格异常: {e}，将直接尝试下单")

            # 尝试下单
            params = {
                "api_key": self.api_key,
                "action": "getNumber",
                "service": self.service,
                "country": country_id
            }
            try:
                r = await client.get(self.base_url, params=params, timeout=15)
                if r.text.startswith("ACCESS_NUMBER"):
                    parts = r.text.split(":")
                    return parts[1], parts[2] # activation_id, phone
                elif "BANNED" in r.text or "NO_NUMBERS" in r.text:
                    print(f"[SMS] 国家 {country_id} 暂时无号或被封禁: {r.text}")
                    return None
                else:
                    print(f"[SMS] 国家 {country_id} 下单返回: {r.text}")
                    return None
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
                        priced.append((price_val, str(country_id)))

                    priced.sort(key=lambda item: item[0])
                    for _, country_id in priced:
                        if country_id not in seen:
                            seen.add(country_id)
                            candidates.append(country_id)
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
                                priced.append((min(prices), str(country_name)))

                    priced.sort(key=lambda item: item[0])
                    for _, country_id in priced:
                        if country_id not in seen:
                            seen.add(country_id)
                            candidates.append(country_id)
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
                    raise RuntimeError(f"一浩接码下单失败: {res.get('msg')}")
            except Exception as e:
                raise RuntimeError(f"请求一浩接码平台失败: {e}")
        else:
            # 传统接码平台 (herosms, smsbower, smsactivate)
            # 策略：先批量查询价格 → 价格过滤 → 白名单优先 → 按价格升序下单
            priced_countries = await self._get_priced_whitelist_countries()
            all_priced = await self._get_all_priced_countries()

            # 白名单国家（价格过滤后）优先
            if priced_countries:
                print(f"[SMS] 白名单国家(价格过滤后): {[(c, f'{p:.3f}') for p, c in priced_countries]}")
                for price_val, country_id in priced_countries:
                    print(f"[SMS] 白名单国家下单: {country_id} (价格={price_val:.3f})")
                    result = await self._place_order(str(country_id))
                    if result:
                        return result

            # 白名单全部无号，尝试默认国家
            if self.country:
                # 检查默认国家是否在 all_priced 中且通过价格过滤
                default_ok = False
                for price_val, country_id in all_priced:
                    if str(country_id) == str(self.country):
                        print(f"[SMS] 默认国家下单: {self.country} (价格={price_val:.3f})")
                        result = await self._place_order(str(self.country))
                        if result:
                            return result
                        default_ok = True
                        break
                if not default_ok:
                    print(f"[SMS] 尝试默认国家(无价格信息): {self.country}")
                    result = await self._check_price_and_get(str(self.country))
                    if result:
                        return result

            # 全部失败，按全平台最低价国家列表继续
            tried = {str(c) for _, c in priced_countries}
            if self.country:
                tried.add(str(self.country))
            for price_val, country_id in all_priced:
                if str(country_id) in tried:
                    continue
                tried.add(str(country_id))
                print(f"[SMS] 最低价国家下单: {country_id} (价格={price_val:.3f})")
                result = await self._place_order(str(country_id))
                if result:
                    return result

            raise RuntimeError("所有配置国家与最低价候选国家均无法成功下单（可能无号、超限价或接口报错）")

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

    async def cancel_activation(self, activation_id: str):
        """取消当前接码任务，便于超时后换号重试"""
        if not activation_id or activation_id == "fixed":
            return

        if self.provider == "yihao":
            headers = {"Authorization": f"Bearer {self.api_key}"}
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(f"{self.base_url}/api/v1/cancel", json={"token": activation_id}, headers=headers, timeout=10)
            except Exception as e:
                print(f"[SMS] 一浩取消接码任务异常: {e}")
            return

        params = {
            "api_key": self.api_key,
            "action": "setStatus",
            "id": activation_id,
            "status": "8"
        }
        try:
            async with httpx.AsyncClient() as client:
                await client.get(self.base_url, params=params, timeout=10)
        except Exception as e:
            print(f"[SMS] 取消接码任务异常: {e}")

    async def set_status(self, activation_id: str, status: int):
        """反馈状态 (一浩平台和固定号码模式暂不需要，这里对传统平台生效)"""
        if self.provider == "yihao" or self.mode == "fixed":
            pass
        else:
            params = {
                "api_key": self.api_key,
                "action": "setStatus",
                "id": activation_id,
                "status": str(status)
            }
            try:
                async with httpx.AsyncClient() as client:
                    await client.get(self.base_url, params=params, timeout=10)
            except Exception as e:
                print(f"[SMS] 反馈状态异常: {e}")

    async def cancel_activation(self, activation_id: str):
        """超时后尽力取消当前激活，避免继续占用号码资源"""
        if not activation_id or activation_id == "fixed":
            return
        if self.provider == "yihao" or self.mode == "fixed":
            return
        try:
            await self.set_status(activation_id, 8)
        except Exception as e:
            print(f"[SMS] 取消激活异常: {e}")
