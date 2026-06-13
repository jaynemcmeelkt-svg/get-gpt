import asyncio
import httpx
from camoufox.async_api import AsyncCamoufox

async def run_oauth_branch(config: dict, proxy_url: str, login_result: dict):
    """
    Codex OAuth + PKCE 授权流程
    使用 gui_config.oauth_headless 控制是否显示浏览器窗口
    """
    browser_opts = {
        "headless": config["gui_config"]["oauth_headless"],
        "locale": config["gui_config"]["locale"],
        "exclude_addons": ["UBO"]  # 排除下载失败的插件
    }
    if proxy_url:
        browser_opts["proxy"] = {"server": proxy_url}
        
    async with AsyncCamoufox(**browser_opts) as browser:
        context = await browser.new_context()
        # 注入之前登录/注册成功保存的 Cookies，避免重新登录
        await context.add_cookies(login_result["cookies"])
        page = await context.new_page()
        
        # 1. 构造 Codex 授权 URL
        client_id = "pdlLIX2Y72FxZ2jYrGxoMbA9g53qLSrj" # 示例 Client ID
        state = "random_state_value"
        redirect_uri = "http://localhost:1455/auth/callback"
        
        oauth_url = (
            f"https://auth.openai.com/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope=openid%20email%20profile%20offline_access"
            f"&state={state}"
        )
        
        # 2. 拦截本地回调请求，用于提取授权码 code
        auth_code = None
        async def handle_route(route):
            nonlocal auth_code
            url = route.request.url
            if redirect_uri in url:
                # 从 URL 中提取 code
                from urllib.parse import urlparse, parse_qs
                query = urlparse(url).query
                auth_code = parse_qs(query).get("code", [None])[0]
                # 阻止请求真正发送到本地端口，直接返回 200
                await route.fulfill(status=200, body="Authorization successful! You can close this window.")
            else:
                await route.continue_()
                
        await page.route("**/auth/callback*", handle_route)
        
        # 3. 访问授权页面
        await page.goto(oauth_url)
        
        # 4. 如果页面出现 "Continue" 确认按钮，自动点击
        try:
            await page.click("button:has-text('Continue')", timeout=5000)
        except:
            pass # 如果没有出现确认页，说明之前已授权过，会自动重定向
            
        # 5. 等待拦截到 code
        for _ in range(10):
            if auth_code:
                break
            await asyncio.sleep(1)
            
        if not auth_code:
            raise RuntimeError("获取 OAuth Code 失败")
            
        # 6. 拿到 code 后，在后台通过 HTTP 请求换取 refresh_token
        token_url = "https://auth.openai.com/oauth/token"
        payload = {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": "your_pkce_verifier" # 需与生成 authorize url 时的 verifier 一致
        }
        
        # 换取 Token
        async with httpx.AsyncClient() as client:
            r = await client.post(token_url, json=payload, timeout=10)
            token_data = r.json()
        
        return {
            "success": True,
            "refresh_token": token_data.get("refresh_token"),
            "access_token": token_data.get("access_token")
        }
