import os
import hmac
import hashlib
import html
import urllib.parse
from typing import Optional

AUTH_COOKIE_NAME = "novel_access_token"

def get_app_password() -> str:
    """
    Get configured password from environment variable APP_PASSWORD.
    Defaults to 'novel1234' if not explicitly configured.
    """
    return os.environ.get("APP_PASSWORD", "").strip() or "novel1234"

def generate_auth_token(password: str) -> str:
    """
    Generate deterministic HMAC SHA-256 token based on password.
    """
    salt = b"lucky-novel-auth-secret-key-salt"
    return hmac.new(salt, password.encode("utf-8"), hashlib.sha256).hexdigest()

def is_valid_token(token: Optional[str]) -> bool:
    """
    Validate provided token against current environment password.
    """
    if not token:
        return False
    current_pwd = get_app_password()
    expected_token = generate_auth_token(current_pwd)
    return hmac.compare_digest(token, expected_token)

def sanitize_next_url(next_url: Optional[str]) -> str:
    """
    Ensure redirect URL is local to prevent open redirect vulnerabilities.
    """
    if not next_url:
        return "/"
    # Must start with / and not //
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"

def render_login_page(error_msg: str = "", next_url: str = "/") -> str:
    safe_next = html.escape(sanitize_next_url(next_url))
    error_html = f"""
    <div class="alert-error" role="alert">
        <span>⚠️</span>
        <div>{html.escape(error_msg)}</div>
    </div>
    """ if error_msg else ""

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>เข้าสู่ระบบ • Lucky Novel Reader</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #3182ce;
            --primary-hover: #2b6cb0;
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #334155;
            --input-bg: #0f172a;
            --font-family: 'Sarabun', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: var(--font-family);
        }}

        body {{
            background: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            background-image: 
                radial-gradient(at 0% 0%, rgba(49, 130, 206, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.12) 0px, transparent 50%);
        }}

        .login-container {{
            width: 100%;
            max-width: 420px;
        }}

        .login-card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 36px 30px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3);
            text-align: center;
        }}

        .logo-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 64px;
            height: 64px;
            background: rgba(49, 130, 206, 0.15);
            border: 1px solid rgba(49, 130, 206, 0.3);
            border-radius: 16px;
            font-size: 32px;
            margin-bottom: 20px;
        }}

        h1 {{
            font-size: 1.5rem;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 8px;
        }}

        p.subtitle {{
            font-size: 0.95rem;
            color: var(--text-muted);
            margin-bottom: 28px;
            line-height: 1.5;
        }}

        .alert-error {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #fca5a5;
            padding: 12px 16px;
            border-radius: 10px;
            font-size: 0.9rem;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            text-align: left;
            animation: shake 0.3s ease-in-out;
        }}

        @keyframes shake {{
            0%, 100% {{ transform: translateX(0); }}
            25% {{ transform: translateX(-5px); }}
            75% {{ transform: translateX(5px); }}
        }}

        .input-group {{
            margin-bottom: 22px;
            text-align: left;
        }}

        .input-label {{
            display: block;
            font-size: 0.88rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}

        .password-wrapper {{
            position: relative;
            display: flex;
            align-items: center;
        }}

        .password-input {{
            width: 100%;
            background: var(--input-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 13px 45px 13px 16px;
            color: #ffffff;
            font-size: 1rem;
            outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
        }}

        .password-input:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(49, 130, 206, 0.25);
        }}

        .toggle-btn {{
            position: absolute;
            right: 12px;
            background: none;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            font-size: 1.15rem;
            padding: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .toggle-btn:hover {{
            color: #ffffff;
        }}

        .btn-submit {{
            width: 100%;
            background: var(--primary);
            color: #ffffff;
            border: none;
            border-radius: 10px;
            padding: 13px;
            font-size: 1.05rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}

        .btn-submit:hover {{
            background: var(--primary-hover);
        }}

        .btn-submit:active {{
            transform: scale(0.99);
        }}

        .footer-note {{
            margin-top: 24px;
            font-size: 0.8rem;
            color: var(--text-muted);
            opacity: 0.8;
        }}
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-card">
            <div class="logo-badge">🔒</div>
            <h1>Lucky Novel Reader</h1>
            <p class="subtitle">ระบบถูกจำกัดสิทธิ์เฉพาะส่วนบุคคล<br>กรุณากรอกรหัสผ่านเพื่อเข้าใช้งาน</p>

            {error_html}

            <form method="POST" action="/login">
                <input type="hidden" name="next" value="{safe_next}">
                
                <div class="input-group">
                    <label class="input-label" for="password">รหัสผ่าน (Password)</label>
                    <div class="password-wrapper">
                        <input 
                            type="password" 
                            id="password" 
                            name="password" 
                            class="password-input" 
                            placeholder="กรอกรหัสผ่านเข้าใช้งาน..." 
                            required 
                            autofocus
                        >
                        <button type="button" class="toggle-btn" onclick="togglePassword()" title="แสดง/ซ่อนรหัสผ่าน">👁️</button>
                    </div>
                </div>

                <button type="submit" class="btn-submit">
                    <span>เข้าสู่ระบบ</span>
                    <span>→</span>
                </button>
            </form>

            <div class="footer-note">
                Lucky Novel Manager • ส่วนตัว & ปลอดภัย
            </div>
        </div>
    </div>

    <script>
        function togglePassword() {{
            const input = document.getElementById('password');
            if (input.type === 'password') {{
                input.type = 'text';
            }} else {{
                input.type = 'password';
            }}
        }}
    </script>
</body>
</html>
"""
