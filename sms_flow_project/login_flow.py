import sys
import os

# 将 Gpt-Agreement-Payment-main/CTF-reg 目录加入 sys.path 以便导入 auth_flow
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
ctf_reg_dir = os.path.join(project_root, "Gpt-Agreement-Payment-main", "CTF-reg")
if ctf_reg_dir not in sys.path:
    sys.path.insert(0, ctf_reg_dir)

from config import Config as RegConfig, MailConfig, CaptchaConfig
from auth_flow import AuthFlow
from .register_flow import ProtocolMailProvider, _extract_cookies_from_session

def run_protocol_login(config: dict, proxy_url: str):
    """
    使用纯协议直连方式登录已有的 ChatGPT 账号 (不使用浏览器)
    """
    phone = config["account_config"]["phone"]
    password = config["account_config"]["password"]
    otp_timeout = max(30, int(config["sms_config"]["gpt_sms"].get("otp_timeout_seconds", 180) or 180))
    os.environ["OTP_TIMEOUT"] = str(otp_timeout)
    
    # 1. 构造协议流所需的 Config 对象
    reg_cfg = RegConfig()
    reg_cfg.proxy = proxy_url
    
    # 2. 初始化接码平台 (用于登录二次验证 OTP)
    from .sms_manager import DynamicSMSProvider
    gpt_sms = DynamicSMSProvider(config["sms_config"]["gpt_sms"])
    
    # 3. 实例化自定义的 MailProvider
    mail_prov = ProtocolMailProvider(gpt_sms)
    # 登录时，我们需要将已有的手机号和激活 ID 绑定到 mail_prov 中
    mail_prov.phone = phone
    mail_prov.activation_id = config["sms_config"]["gpt_sms"].get("activation_id")
    
    # 4. 启动协议流
    flow = AuthFlow(reg_cfg)
    
    # 5. 执行协议登录
    auth_result = flow.run_protocol_login(mail_prov, email=phone, password=password)
    
    # 6. 组装返回结果
    return {
        "success": True,
        "phone": phone,
        "accessToken": auth_result.access_token,
        "sessionToken": auth_result.session_token,
        "cookies": _extract_cookies_from_session(flow.session)
    }
