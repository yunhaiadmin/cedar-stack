"""易支付 MD5 签名已知向量测试（2026-11-19 签名算法修复）。

生产事故：下单签名把参数值先 urlencode 再拼串算 MD5，网关回
「MD5签名校验失败」，本站易支付从未成功过一笔。易支付标准算法是
原始值拼串：按 key ASCII 升序、跳过空值与 sign/sign_type、k=v 用 &
连接、末尾直接拼商户 key（不加 &）、MD5 小写。

本文件的期望值一律在测试里把拼串明文写出来再 md5，不许调被测
函数自证——向量错一位，网关就拒一位。
"""
import hashlib
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-yipay-sign-key-32chars!!!!")

from backend.api.economy import _verify_yipay_notify, _yipay_sign
from backend.payment_gateway import _epay_sign

KEY = "0123456789abcdef0123456789abcdef"

ORDER_PARAMS = {
    "pid": "1001",
    "type": "alipay",
    "out_trade_no": "WT1795101993859F3D98B",
    "notify_url": "https://www.aetrix.love/api/user/wallet/topup/notify",
    "return_url": "https://www.aetrix.love/wallet?topup=1",
    "name": "钱包充值",
    "money": "7.35",
}

# 按 key ASCII 升序的原始值拼串明文（money < name < notify_url <
# out_trade_no < pid < return_url < type），末尾直接拼 key 不加 &。
ORDER_SIGN_PLAIN = (
    "money=7.35"
    "&name=钱包充值"
    "&notify_url=https://www.aetrix.love/api/user/wallet/topup/notify"
    "&out_trade_no=WT1795101993859F3D98B"
    "&pid=1001"
    "&return_url=https://www.aetrix.love/wallet?topup=1"
    "&type=alipay"
    + KEY
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def test_sign_uses_raw_values_not_urlencoded():
    assert _yipay_sign(ORDER_PARAMS, KEY) == _md5(ORDER_SIGN_PLAIN)


def test_epay_adapter_sign_matches_same_vector():
    # payment_gateway 的适配器实现必须与主实现同一算法、同一结果。
    assert _epay_sign(ORDER_PARAMS, KEY) == _md5(ORDER_SIGN_PLAIN)


def test_sign_skips_empty_and_sign_fields():
    params = dict(ORDER_PARAMS)
    params["attach"] = ""
    params["extra"] = None
    params["sign"] = "should-be-ignored"
    params["sign_type"] = "MD5"
    assert _yipay_sign(params, KEY) == _md5(ORDER_SIGN_PLAIN)


NOTIFY_PARAMS = {
    "pid": "1001",
    "trade_no": "202611192300123456",
    "out_trade_no": "WT1795101993859F3D98B",
    "type": "alipay",
    "name": "钱包充值",
    "money": "7.35",
    "trade_status": "TRADE_SUCCESS",
}

# 回调参数按 key ASCII 升序的原始值拼串明文（money < name <
# out_trade_no < pid < trade_no < trade_status < type）。
NOTIFY_SIGN_PLAIN = (
    "money=7.35"
    "&name=钱包充值"
    "&out_trade_no=WT1795101993859F3D98B"
    "&pid=1001"
    "&trade_no=202611192300123456"
    "&trade_status=TRADE_SUCCESS"
    "&type=alipay"
    + KEY
)


def test_verify_notify_accepts_standard_signed_callback():
    # 按标准算法（原始值拼串）签的回调必须能通过验签——网关就是这么签的。
    raw = dict(NOTIFY_PARAMS)
    raw["sign"] = _md5(NOTIFY_SIGN_PLAIN)
    raw["sign_type"] = "MD5"
    assert _verify_yipay_notify(raw, KEY) is True


def test_verify_notify_rejects_tampered_amount():
    raw = dict(NOTIFY_PARAMS)
    raw["sign"] = _md5(NOTIFY_SIGN_PLAIN)
    raw["sign_type"] = "MD5"
    raw["money"] = "0.01"
    assert _verify_yipay_notify(raw, KEY) is False
