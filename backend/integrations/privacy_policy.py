"""隐私政策正文能力（用户端公开页 /privacy）。

正文原先写死在 user_frontend 的 PrivacyView.vue 里，改一个字都要
发一版前端。现改为 SystemConfig 单键 ``privacy_policy_md``（家族
privacy_policy，整份 Markdown），管理后台「系统设置 → 隐私政策」
卡片编辑，公开端点 /api/site/privacy-policy 下发，用户端读到非空
正文时按 Markdown 渲染。

读取口径：
- 存了非空正文 → 以它为准（管理员全权接管）；
- 没存 / 空串 → 用户端回落 PrivacyView 内置的现行写死正文
  （一字不丢，Google OAuth 验证挂的那份原文）。

``DEFAULT_MARKDOWN`` 是现行写死正文的 Markdown 转写，只给管理端
编辑器起稿用（未存自定义正文时预填它，管理员改完保存才生效）；
用户端回落走前端内置 HTML，不经这份转写。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.integrations import store

KEY = "privacy_policy_md"


def read_custom(db: Session) -> str:
    """已保存的自定义正文原文（直查口径）；未存/空串返回 ""。"""
    value = store.read_value(db, KEY, "")
    return value if isinstance(value, str) else ""


def public_info(db: Session) -> dict:
    """公开下发：markdown 为空即表示前端回落内置正文。"""
    md = read_custom(db)
    return {"markdown": md, "has_custom": bool(md.strip())}


#: 现行写死正文（PrivacyView.vue 内置版）的 Markdown 转写，文字与
#: 内置版逐句一致，仅把 HTML 结构换成 Markdown 记号。
DEFAULT_MARKDOWN = """# 隐私政策

以下内容适用于你通过 Aetrix 绑定自己的谷歌盘的情形。

1. **我们访问什么：**只有在你主动在账号设置里绑定自己的谷歌盘之后，Aetrix 才会通过 Google OAuth 访问你的 Google Drive。drive.readonly 只读读取你盘里媒体文件的名称、元数据和文件内容；drive.file 的写入只限在你自己绑定的盘里建转存目录、存转存副本，绝不删除、分享、改动你盘里的其他文件，也绝不碰别的用户的盘。
2. **用来干什么：**只用来做三件事——（1）把你自己盘里的影片建成你的媒体库；（2）你点播时用你自己的账号额度取流播放；（3）把你还没转存过的片子先转存到你自己的盘里（只在你绑定的盘里建转存目录、存转存副本），转完再播。你的盘只服务你自己，不会给别的用户播，也不会进共享池。
3. **凭据怎么存：**Google 发来的 access_token / refresh_token 在服务器端密封保存，不在页面上显示，不写进日志，不交给任何第三方。
4. **数据去向：**我们不卖你的数据，不用你的文件做广告，不用你的文件训练模型，不把你的 Drive 数据共享给任何第三方。
5. **你随时能停：**在账号设置里解绑，对应凭据即删除；你也可以随时在 Google 账号的“第三方访问权限”里撤销 Aetrix 的授权，撤销后本站立即无法再访问你的盘。
6. **联系开发者：**开发者联系方式见 Google OAuth 同意屏幕上的开发者信息。

## Data Protection and Security Measures

以下为数据保护与安全措施说明（英文版）：

We protect the Google user data we access — in particular your OAuth tokens — with the following measures:

1. **Encryption at rest.** Your Google access token and refresh token are never stored in plaintext. They are stored only as a sealed (encrypted) value, produced with Fernet authenticated encryption; the sealing key is derived from a server-side secret that is kept outside the database. If the sealed value cannot be decrypted, the credential is treated as invalid rather than used.
2. **Tokens stay on the server.** Your tokens are held and used only on our server. They are never sent to your browser, never rendered in any page, and the binding information our API returns is limited to non-secret metadata such as the account label and the names of the scopes you granted.
3. **Encryption in transit.** All communication between Aetrix and Google (authorization, token exchange, refresh, revocation, and Drive API calls) uses HTTPS endpoints.
4. **Hardened authorization flow.** The OAuth flow uses PKCE (S256), a short-lived state value, and a nonce that is stored only as a hash and compared in constant time; a callback whose nonce or session user does not match is rejected and the state is discarded.
5. **Least privilege.** We request only the minimum scopes the features need — read-only access plus access to files the app itself creates — and nothing more.
6. **Per-user isolation.** Each user's credential is stored against that user's own account row and is only ever used for that user. Operations that act on a binding verify it belongs to the requesting user; a binding belonging to someone else is treated as non-existent.
7. **Revocation when you disconnect.** When you unbind your Drive in Aetrix, we (a) revoke the refresh token with Google's revocation endpoint, (b) delete the stored credential, and (c) remove the "anyone with the link can view" sharing that playback had placed on your transfer copies — all as part of the same disconnect action. You can also revoke Aetrix's access at any time from your Google Account's third-party access page.
8. **Time-limited link sharing for playback.** The only sharing we ever set is "anyone with the link can view" on the transfer copies the app itself created in your Drive, so your player can stream them directly from Google. This sharing is automatically removed after 2 hours of inactivity, and immediately when you unbind (see 7). We never change sharing on any other file in your Drive.
"""
