import sys
import os
import time
import random

# 将 Gpt-Agreement-Payment-main/CTF-reg 目录加入 sys.path 以便导入 auth_flow
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
ctf_reg_dir = os.path.join(project_root, "Gpt-Agreement-Payment-main", "CTF-reg")
if ctf_reg_dir not in sys.path:
    sys.path.insert(0, ctf_reg_dir)

from typing import Optional
from config import Config as RegConfig, MailConfig, CaptchaConfig
from auth_flow import AuthFlow
from mail_provider import MailProvider


def _run_async_in_sync_context(coro):
    """在同步上下文中安全执行协程，兼容线程池工作线程无默认 event loop 的情况。"""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


def _is_retryable_registration_error(error: Exception) -> bool:
    """判断是否属于应当换号重试的注册错误。"""
    message = str(error or "").lower()
    retry_markers = [
        "authorize/continue",
        "just a moment",
        "cloudflare",
        "challenge",
        "http 403",
        "403 forbidden",
    ]
    return any(marker in message for marker in retry_markers)


class ProtocolMailProvider(MailProvider):
    """
    自定义的 MailProvider，用于桥接我们的 DynamicSMSProvider 到协议注册流中。
    """
    def __init__(self, sms_provider, run_id: Optional[str] = None, catch_all_domain: str = ""):
        super().__init__(catch_all_domain)
        self.sms_provider = sms_provider
        self.run_id = run_id
        self.activation_id = None
        self.phone = None

    def create_mailbox(self) -> str:
        # 协议流中，我们使用手机号作为 username 注册
        # 1. 获取手机号
        self.activation_id, self.phone = _run_async_in_sync_context(self.sms_provider.get_number())
        if not self.phone.startswith("+"):
            self.phone = "+" + self.phone
            
        # 2. 如果有 run_id，实时更新数据库中的手机号
        if self.run_id:
            try:
                from .db_manager import DBManager
                db = DBManager()
                db.update_run_phone(self.run_id, self.phone)
            except Exception as e:
                print(f"[DB] 实时更新运行手机号失败: {e}")
                
        return self.phone

    def wait_for_otp(self, email: str, timeout: int = 180, issued_after: float = 0.0) -> str:
        # 轮询获取验证码，优先使用当前接码配置中的超时时间
        effective_timeout = getattr(self.sms_provider, "otp_timeout_seconds", timeout) or timeout
        otp_code = _run_async_in_sync_context(self.sms_provider.get_otp(self.activation_id, timeout=effective_timeout))
        return otp_code

def run_protocol_register(config: dict, proxy_url: str, run_id: Optional[str] = None):
    """
    使用纯协议直连方式注册 ChatGPT 账号 (不使用浏览器)
    """
    from .sms_manager import DynamicSMSProvider

    sms_config = config["sms_config"]["gpt_sms"]
    otp_timeout = max(30, int(sms_config.get("otp_timeout_seconds", 180) or 180))
    max_attempts = max(1, int(sms_config.get("otp_retry_attempts", 3) or 3))
    last_error = None

    for attempt in range(1, max_attempts + 1):
        gpt_sms = DynamicSMSProvider(sms_config)
        mail_prov = ProtocolMailProvider(gpt_sms, run_id=run_id)
        flow = None

        try:
            # 1. 构造协议流所需的 Config 对象
            reg_cfg = RegConfig()
            reg_cfg.proxy = proxy_url

            # 2. 初始化打码平台配置 (如果有的话)
            captcha_cfg = config.get("captcha_config", {})
            reg_cfg.captcha = CaptchaConfig(
                api_url=captcha_cfg.get("api_url", ""),
                client_key=captcha_cfg.get("client_key", "")
            )

            # 3. 启动协议流
            flow = AuthFlow(reg_cfg)

            # 4. 当前轮注册
            os.environ["OTP_TIMEOUT"] = str(otp_timeout)
            auth_result = flow.run_register(mail_prov)

            return {
                "success": True,
                "phone": auth_result.email,
                "password": auth_result.password,
                "accessToken": auth_result.access_token,
                "sessionToken": auth_result.session_token,
                "cookies": _extract_cookies_from_session(flow.session)
            }
        except TimeoutError as e:
            last_error = e
            print(f"[SMS] 第 {attempt}/{max_attempts} 次等待验证码超时，准备换号重试: {e}")
            try:
                if mail_prov.activation_id:
                    _run_async_in_sync_context(gpt_sms.cancel_activation(mail_prov.activation_id))
            except Exception as cancel_error:
                print(f"[SMS] 取消超时接码任务失败: {cancel_error}")

            if attempt >= max_attempts:
                raise TimeoutError(f"等待验证码超时，已连续换号重试 {max_attempts} 次仍失败")
        except Exception as e:
            last_error = e
            if _is_retryable_registration_error(e):
                print(f"[REG] 第 {attempt}/{max_attempts} 次注册遇到风控/挑战，准备换号重试: {e}")
                try:
                    if mail_prov.activation_id:
                        _run_async_in_sync_context(gpt_sms.cancel_activation(mail_prov.activation_id))
                except Exception as cancel_error:
                    print(f"[SMS] 取消被风控拦截的接码任务失败: {cancel_error}")

                if attempt >= max_attempts:
                    raise RuntimeError(f"注册入口连续触发风控/挑战，已换号重试 {max_attempts} 次仍失败: {e}")
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("注册流程异常结束")

def _extract_cookies_from_session(session) -> list:
    """从 curl_cffi/requests session 中提取 cookies 列表"""
    cookies = []
    try:
        for cookie in session.cookies:
            cookies.append({
                "name": getattr(cookie, "name", ""),
                "value": getattr(cookie, "value", ""),
                "domain": getattr(cookie, "domain", ""),
                "path": getattr(cookie, "path", "/"),
                "secure": getattr(cookie, "secure", True),
                "httpOnly": getattr(cookie, "httpOnly", False)
            })
    except Exception:
        pass
    return cookies
