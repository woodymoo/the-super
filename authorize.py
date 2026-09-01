"""一次性 OAuth 引导 —— 生成 token.json。

用法:
    source .venv/bin/activate
    python authorize.py

会弹出浏览器让你登录授权。授权完 token.json 落在项目根目录,
之后 main.py 和 adk web 都直接复用它,不再需要浏览器。

⚠️ 登录时务必选**有 Google Voice 的那个账号**。选错了整条短信通道都不通。
   脚本最后会打印实际授权的邮箱,和 .env 里的 LANDLORD_EMAIL 对不上会警告。

重新授权(比如 7 天过期后):删掉 token.json 再跑一次。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv(Path(__file__).parent / "the_super" / ".env")

# gmail.modify 一个 scope 就够:读邮件、读附件、建草稿、发送都覆盖。
# 不用 mail.google.com —— 那个还包含永久删除权限,没必要要。
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "token.json")


def get_credentials() -> Credentials:
    """拿到可用凭据。有 token 就复用,过期就刷新,都不行才走浏览器授权。

    这个函数之后会被 tools/gmail.py 直接复用。
    """
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        # 静默刷新 —— 定时任务无人值守时走的就是这条路
        creds.refresh(Request())
    else:
        if not Path(CREDENTIALS_FILE).exists():
            sys.exit(
                f"找不到 {CREDENTIALS_FILE}。\n"
                "先去 Google Cloud Console 创建「桌面应用」类型的 OAuth 客户端 ID,\n"
                "下载 JSON 改名成 credentials.json 放到项目根目录。"
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

    Path(TOKEN_FILE).write_text(creds.to_json())
    return creds


if __name__ == "__main__":
    creds = get_credentials()

    # 确认授权到的是哪个账号 —— 两个 Gmail 账号很容易点错
    profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
    actual = profile["emailAddress"]

    print(f"\n✅ 授权成功:{actual}")
    print(f"   token 已写入 {TOKEN_FILE}")
    print(f"   邮箱内共 {profile['messagesTotal']} 封邮件")

    expected = os.environ.get("LANDLORD_EMAIL", "")
    if expected and expected.lower() != actual.lower():
        print(
            f"\n⚠️  和 .env 里的 LANDLORD_EMAIL ({expected}) 不一致!\n"
            f"   如果 {actual} 不是有 Google Voice 的账号,\n"
            f"   删掉 {TOKEN_FILE} 重跑,登录时选对账号。"
        )
    elif not expected:
        print(f"\n提示:把 LANDLORD_EMAIL={actual} 填进 the_super/.env")
