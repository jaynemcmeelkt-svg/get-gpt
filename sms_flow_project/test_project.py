import unittest
import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sms_flow_project.address_generator import LocalAddressGenerator
from sms_flow_project.sms_manager import DynamicSMSProvider
from sms_flow_project.register_flow import ProtocolMailProvider, _is_retryable_registration_error, run_protocol_register
from sms_flow_project import main as app_main

class TestSMSFlowProject(unittest.TestCase):
    def setUp(self):
        self.current_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.current_dir, "data")

    def test_address_generator(self):
        """测试地址生成器是否能正确加载数据并生成地址"""
        generator = LocalAddressGenerator(self.data_dir)
        
        # 1. 测试随机生成
        addr = generator.generate_us_address()
        self.assertIsNotNone(addr)
        self.assertIn("name", addr)
        self.assertIn("full_address", addr)
        self.assertIn("state_code", addr)
        self.assertIn(addr["state_code"], ["OR", "DE", "MT", "NH"])
        
        # 2. 测试指定免税州生成
        addr_or = generator.generate_us_address("OR")
        self.assertEqual(addr_or["state_code"], "OR")
        self.assertEqual(addr_or["state"], "Oregon")
        self.assertTrue(addr_or["phone"].startswith("+1"))
        
        print("\n[Test] Generated Address Sample:")
        print(json.dumps(addr_or, indent=2, ensure_ascii=False))

    def test_sms_provider_fixed_mode(self):
        """测试固定号码模式下的接码配置"""
        sms_setting = {
            "provider": "yihao",
            "mode": "fixed",
            "fixed_config": {
                "phone": "+10000000002",
                "sms_url": "http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=mock_key"
            }
        }
        provider = DynamicSMSProvider(sms_setting)
        
        activation_id, phone = asyncio.run(provider.get_number())
        
        self.assertEqual(activation_id, "fixed")
        self.assertEqual(phone, "+10000000002")

    def test_sms_provider_fixed_mode_parses_phone_and_url_pair(self):
        """测试固定号码模式支持 `号码|URL` 格式"""
        sms_setting = {
            "provider": "yihao",
            "mode": "fixed",
            "fixed_config": {
                "phone": "",
                "sms_urls": [
                    "+10000000001|http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=test_key"
                ]
            }
        }
        provider = DynamicSMSProvider(sms_setting)

        entries = provider._parse_fixed_sms_entries()
        self.assertEqual(entries, [
            {
                "phone": "+10000000001",
                "url": "http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=test_key"
            }
        ])

        activation_id, phone = asyncio.run(provider.get_number())
        self.assertEqual(activation_id, "fixed")
        self.assertEqual(phone, "+10000000001")

    def test_protocol_mail_provider_works_in_worker_thread(self):
        """测试协议注册的邮箱桥接器在工作线程中也能获取号码"""

        class StubSMSProvider:
            async def get_number(self):
                return "fixed", "+12345678901"

        mail_provider = ProtocolMailProvider(StubSMSProvider())

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mail_provider.create_mailbox)
            phone = future.result()

        self.assertEqual(phone, "+12345678901")

    def test_protocol_mail_provider_wait_for_otp_in_worker_thread(self):
        """测试登录 OTP 路径在工作线程中也能等待验证码"""

        class StubSMSProvider:
            otp_timeout_seconds = 30

            async def get_otp(self, activation_id, timeout=120):
                return "123456"

        mail_provider = ProtocolMailProvider(StubSMSProvider())
        mail_provider.activation_id = "fixed"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mail_provider.wait_for_otp, "+12345678901", 180, 0.0)
            otp_code = future.result()

        self.assertEqual(otp_code, "123456")

    def test_sms_provider_preferred_countries_and_price(self):
        """测试首选国家和限价逻辑"""
        sms_setting = {
            "provider": "smsactivate",
            "api_key": "mock_api_key",
            "service": "dr",
            "country": "6",
            "preferred_countries": ["1", "2"],
            "max_price": 15.0
        }
        provider = DynamicSMSProvider(sms_setting)
        self.assertEqual(provider.preferred_countries, ["1", "2"])
        self.assertEqual(provider.max_price, 15.0)
        self.assertEqual(provider.country, "6")

    def test_normalize_proxy_text_supports_host_port_user_pass(self):
        """测试代理文本 `host:port:user:pass` 会被标准化为合法 socks5 URL"""
        normalized = app_main.normalize_proxy_text("YOUR_PROXY_GATEWAY_HOST:1111:demo_user:demo_pass")
        self.assertEqual(normalized, "socks5://demo_user:demo_pass@YOUR_PROXY_GATEWAY_HOST:1111")

    def test_get_proxy_url_retries_when_first_proxy_is_invalid(self):
        """测试代理 API 第一次返回坏代理时会自动重试拉取新代理"""

        class DummyResponse:
            def __init__(self, text):
                self.text = text

        class DummyClient:
            def __init__(self, responses):
                self._responses = responses
                self.calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, timeout=15):
                response = self._responses[self.calls]
                self.calls += 1
                return response

        dummy_client = DummyClient([
            DummyResponse("bad-proxy-format"),
            DummyResponse("demo_user:demo_pass@YOUR_PROXY_GATEWAY_HOST:1111"),
        ])

        proxy_cfg = app_main.ProxyConfig(
            mode="api",
            api_url="http://proxy-api.example.com/get",
            strategy="fixed"
        )

        with patch("sms_flow_project.main.httpx.AsyncClient", return_value=dummy_client):
            proxy_url = asyncio.run(app_main.get_proxy_url(proxy_cfg))

        self.assertEqual(proxy_url, "socks5://demo_user:demo_pass@YOUR_PROXY_GATEWAY_HOST:1111")
        self.assertEqual(dummy_client.calls, 2)

    def test_detect_geo_from_proxy_uses_project_http_session(self):
        """测试 GEO 检测可通过项目实际 HTTP 会话获取代理出口信息"""

        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "status": "success",
                    "countryCode": "JP",
                    "timezone": "Asia/Tokyo"
                }

        class DummySession:
            def get(self, url, timeout=8):
                return DummyResponse()

        with patch("sms_flow_project.main.create_proxy_probe_session", return_value=DummySession()):
            locale, timezone = asyncio.run(
                app_main.detect_geo_from_proxy("socks5://demo_user:demo_pass@YOUR_PROXY_GATEWAY_HOST:1111")
            )

        self.assertEqual(locale, "ja-JP")
        self.assertEqual(timezone, "Asia/Tokyo")

    def test_sms_provider_get_number_uses_price_check_helper(self):
        """测试传统接码平台可通过价格检查辅助方法成功下单"""

        class DummyResponse:
            def __init__(self, text):
                self.text = text

        class DummyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, params=None, timeout=15):
                return DummyResponse("ACCESS_NUMBER:token123:+819012345678")

        provider = DynamicSMSProvider({
            "provider": "smsbower",
            "api_key": "mock_api_key",
            "service": "dr",
            "country": "colombia"
        })

        with patch("sms_flow_project.sms_manager.httpx.AsyncClient", return_value=DummyClient()):
            activation_id, phone = asyncio.run(provider.get_number())

        self.assertEqual(activation_id, "token123")
        self.assertEqual(phone, "+819012345678")

    def test_sms_provider_falls_back_to_lowest_price_countries(self):
        """测试默认国家失败后，会按最低价国家列表继续轮询下单"""
        provider = DynamicSMSProvider({
            "provider": "smsbower",
            "api_key": "mock_api_key",
            "service": "dr",
            "country": "colombia"
        })

        async def fake_check(country_id):
            if country_id == "colombia":
                return None
            if country_id == "8":
                return None
            if country_id == "10":
                return ("token456", "+628123456789")
            return None

        with patch.object(provider, "_check_price_and_get", side_effect=fake_check) as mock_check, \
             patch.object(provider, "_get_lowest_price_country_candidates", return_value=["8", "10"]):
            activation_id, phone = asyncio.run(provider.get_number())

        self.assertEqual((activation_id, phone), ("token456", "+628123456789"))
        self.assertEqual([call.args[0] for call in mock_check.await_args_list], ["colombia", "8", "10"])

    def test_retryable_registration_error_detects_cloudflare_403(self):
        """测试 Cloudflare 403 / challenge 错误会被识别为可换号重试"""
        err = RuntimeError(
            "authorize/continue 失败(screen_hint=signup): HTTP 403 - <!DOCTYPE html><html><title>Just a moment...</title>"
        )
        self.assertTrue(_is_retryable_registration_error(err))

    def test_run_protocol_register_retries_on_cloudflare_403(self):
        """测试注册入口遇到 403 challenge 时会取消当前号码并换号重试"""
        config = {
            "sms_config": {
                "gpt_sms": {
                    "otp_timeout_seconds": 180,
                    "otp_retry_attempts": 2,
                }
            },
            "captcha_config": {
                "api_url": "",
                "client_key": "",
            },
        }

        auth_flow_results = [
            RuntimeError("authorize/continue 失败(screen_hint=signup): HTTP 403 - Just a moment..."),
            type("Result", (), {
                "email": "+15550002222",
                "password": "pass-123",
                "access_token": "access-xyz",
                "session_token": "session-xyz",
            })(),
        ]
        created_sms_providers = []
        created_mail_providers = []

        class DummySMSProvider:
            def __init__(self, sms_setting):
                self.sms_setting = sms_setting
                self.cancelled_ids = []
                created_sms_providers.append(self)

            async def cancel_activation(self, activation_id):
                self.cancelled_ids.append(activation_id)

        class DummyMailProvider:
            def __init__(self, sms_provider, run_id=None):
                self.sms_provider = sms_provider
                self.run_id = run_id
                self.activation_id = f"activation-{len(created_mail_providers) + 1}"
                created_mail_providers.append(self)

        class DummyAuthFlow:
            def __init__(self, cfg):
                self.cfg = cfg
                self.session = type("Session", (), {"cookies": []})()

            def run_register(self, mail_provider):
                outcome = auth_flow_results.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        with patch("sms_flow_project.sms_manager.DynamicSMSProvider", DummySMSProvider), \
             patch("sms_flow_project.register_flow.ProtocolMailProvider", DummyMailProvider), \
             patch("sms_flow_project.register_flow.AuthFlow", DummyAuthFlow):
            result = run_protocol_register(config, "socks5://demo_user:demo_pass@YOUR_PROXY_GATEWAY_HOST:1111", run_id="run-test")

        self.assertTrue(result["success"])
        self.assertEqual(result["phone"], "+15550002222")
        self.assertEqual(len(created_sms_providers), 2)
        self.assertEqual(created_sms_providers[0].cancelled_ids, ["activation-1"])
        self.assertEqual(created_sms_providers[1].cancelled_ids, [])

    def test_database_manager(self):
        """测试 SQLite 数据库管理器的 CRUD 操作"""
        from sms_flow_project.db_manager import DBManager
        
        # 使用内存数据库进行测试，避免污染本地文件
        db = DBManager(":memory:")
        
        run_id = "test_run_001"
        phone = "+10000000099"
        email = "test@example.com"
        
        # 1. 测试创建运行记录
        success = db.create_run(
            run_id=run_id,
            flow_type="register",
            data_path="/tmp/test_run_001",
            email_used=email,
            proxy_used="socks5://127.0.0.1:1080"
        )
        self.assertTrue(success)
        
        # 2. 测试获取运行记录
        run = db.get_run(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["flow_type"], "register")
        self.assertEqual(run["status"], "running")
        
        # 3. 测试更新手机号
        success = db.update_run_phone(run_id, phone)
        self.assertTrue(success)
        run = db.get_run(run_id)
        self.assertEqual(run["phone_used"], phone)
        
        # 4. 测试更新状态
        success = db.update_run_status(run_id, "success")
        self.assertTrue(success)
        run = db.get_run(run_id)
        self.assertEqual(run["status"], "success")
        
        # 5. 测试插入账号资产
        success = db.upsert_account(
            phone=phone,
            email=email,
            password="test_password",
            access_token="access_token_123",
            session_token="session_token_456",
            refresh_token=None,
            run_id=run_id
        )
        self.assertTrue(success)
        
        # 6. 测试获取账号资产
        account = db.get_account(phone)
        self.assertIsNotNone(account)
        self.assertEqual(account["email"], email)
        self.assertEqual(account["password"], "test_password")
        self.assertEqual(account["status"], "active")
        
        # 7. 测试更新账号资产 (例如追加 refresh_token)
        success = db.upsert_account(
            phone=phone,
            email=None,
            password=None,
            access_token=None,
            session_token=None,
            refresh_token="refresh_token_789",
            run_id=None
        )
        self.assertTrue(success)
        account = db.get_account(phone)
        self.assertEqual(account["refresh_token"], "refresh_token_789")
        self.assertEqual(account["password"], "test_password") # 验证 COALESCE 保留了原值
        
        # 8. 测试列出账号
        accounts = db.list_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["phone"], phone)

if __name__ == "__main__":
    unittest.main()
