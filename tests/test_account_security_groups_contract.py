"""账号安全分组与 Passkey 空状态静态契约（2026-11-18 UHDNow 对齐批）。

背景：友站账号安全页是四组结构——「账户」「登录与恢复」「Passkeys」
「活跃会话」；我方原先把安全相关内容全挤在一个「安全」组里（改密码行
＋设备＋登录记录＋三件套面板顺序堆叠），且 Passkeys 只是面板内子标题、
空状态只有一行灰字。本契约把本批的呈现对齐钉住（差异清单 G-47~G-52、
G-55~G-59、G-61）：

1. 设置页出现四组标题，旧「安全」组标题与其副文不得回归。
2. 「账户」组：复制账户信息钮、用户名行、用户 ID 行齐备。
3. 「登录与恢复」组：组副文、密码行（名＋副文＋「修改」胶囊）、
   Telegram 行（副文＋落点接既有 TgBindModal）齐备。
4. 「登录与恢复」动态验证码行带副文（TotpPanel）。
5. Passkeys 独立组：组副文、组头「添加 Passkey」胶囊、空状态四件套
   （指纹图标块／「尚未添加 Passkey」／说明文／「添加第一枚 Passkey」）。
6. 「活跃会话」组内有「创建 API Key」胶囊钮（ApiKeysPanel）。

只钉呈现（分组/文案/空状态），不钉数据模型：改密规则（G-53）、恢复
邮箱（G-54）、活跃会话数据模型（G-60/G-62~G-66）不在本批范围。
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
VIEW = REPO / "user_frontend/src/views/game/AccountSettingsView.vue"
PROFILE = REPO / "user_frontend/src/components/profile"
TOTP_PANEL = PROFILE / "TotpPanel.vue"
PASSKEYS_PANEL = PROFILE / "PasskeysPanel.vue"
API_KEYS_PANEL = PROFILE / "ApiKeysPanel.vue"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"契约文件不存在（守卫失去扫描面）：{path}"
    return path.read_text(encoding="utf-8")


def test_settings_view_has_four_security_groups():
    text = _read(VIEW)
    for title in ("账户", "登录与恢复", "Passkeys", "活跃会话"):
        assert f">{title}<" in text, f"设置页缺分组标题「{title}」"
    assert "发现不是自己登的，先改密码再清设备" not in text, (
        "旧「安全」组副文回归——安全内容应已按四组重组"
    )


def test_account_group_rows():
    text = _read(VIEW)
    assert "复制账户信息" in text, "账户组缺「复制账户信息」钮"
    assert "用户名" in text and "用户 ID" in text, "账户组缺用户名/用户 ID 行"
    assert "public_id" in text, "用户 ID 行必须取 public_id（不许拿数字 id 兜底）"


def test_login_recovery_group_rows():
    text = _read(VIEW)
    assert "管理登录验证与找回渠道。" in text, "登录与恢复组缺组副文"
    assert "8-64 字符，修改时验证恢复渠道" in text, "密码行缺副文"
    assert "修改" in text, "密码行缺「修改」胶囊钮"
    assert "辅助恢复渠道，由 Bot 发送验证码" in text, "Telegram 行缺副文"
    assert "TgBindModal" in text, "Telegram 行落点必须接既有 TgBindModal 绑定流程"


def test_totp_row_has_subtitle():
    text = _read(TOTP_PANEL)
    assert "密码登录时使用的 6 位动态码" in text, "动态验证码行缺副文"
    assert "动态验证码" in text


def test_passkeys_group_and_empty_state():
    text = _read(PASSKEYS_PANEL)
    assert "使用设备生物识别或安全密钥登录，最多可保存" in text, "Passkeys 组缺组副文"
    assert "添加 Passkey" in text, "Passkeys 组缺组头添加胶囊钮"
    assert "尚未添加 Passkey" in text, "Passkey 空状态缺粗体标题"
    assert "添加后可使用 Touch ID、Face ID 或安全密钥登录。" in text, "Passkey 空状态缺说明文"
    assert "添加第一枚 Passkey" in text, "Passkey 空状态缺专属钮"
    assert "还没有 Passkey。用设备指纹/面容或安全密钥就能免密登录。" not in text, (
        "旧一行式 Passkey 空状态文案回归——应为四件套空状态"
    )


def test_api_key_capsule_in_sessions_group():
    view = _read(VIEW)
    panel = _read(API_KEYS_PANEL)
    assert "活跃会话" in view, "设置页缺「活跃会话」组"
    assert "创建 API Key" in panel, "活跃会话组缺「创建 API Key」胶囊钮"
