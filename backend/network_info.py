# -*- coding: utf-8 -*-
"""测速页「网络信息」的数据推导（纯函数，无外部依赖）。

数据来源口径（与 /api/user/network-info 端点同源）：
- IP：服务端按 backend.ratelimit.client_ip 的可信代理口径取请求来源
  IP，只把打码后的字符串下发前端，原始 IP 不出服务端。
- 地区：只读本机链路已经带上的 ``CF-IPCountry`` 头（请求经
  Cloudflare 到源站时由 CF 写入）；没有这个头就没有可靠的离线
  来源，一律回 None（前端显示「--」）。不接任何第三方 IP 库 /
  在线 API，不许为了填满而猜。
- 运营商：没有可信的离线推导来源，端点恒回 None（前端「--」），
  与友站实测一致（其运营商同样是 --）。
"""
from __future__ import annotations

import ipaddress
from typing import Optional

# 国家/地区代码 → 宏观区域（只收常见代码；查不到时只显示代码本身，
# 连代码都没有就回 None）。标签是展示文案，不是判定依据。
_COUNTRY_AREA = {
    "CN": "中国",
    "HK": "中国香港",
    "MO": "中国澳门",
    "TW": "中国台湾",
    "SG": "东南亚",
    "MY": "东南亚",
    "TH": "东南亚",
    "VN": "东南亚",
    "ID": "东南亚",
    "PH": "东南亚",
    "KH": "东南亚",
    "MM": "东南亚",
    "LA": "东南亚",
    "BN": "东南亚",
    "JP": "日本",
    "KR": "韩国",
    "IN": "南亚",
    "PK": "南亚",
    "BD": "南亚",
    "US": "北美",
    "CA": "北美",
    "MX": "北美",
    "GB": "欧洲",
    "DE": "欧洲",
    "FR": "欧洲",
    "NL": "欧洲",
    "IT": "欧洲",
    "ES": "欧洲",
    "SE": "欧洲",
    "CH": "欧洲",
    "RU": "欧洲",
    "TR": "西亚",
    "AE": "西亚",
    "SA": "西亚",
    "IL": "西亚",
    "AU": "大洋洲",
    "NZ": "大洋洲",
    "BR": "南美",
    "AR": "南美",
    "CL": "南美",
    "ZA": "非洲",
    "EG": "非洲",
    "NG": "非洲",
}


def mask_ip(ip: Optional[str]) -> Optional[str]:
    """把 IP 打码成友站样式：IPv4 保留前两段、后两段星号。

    ``139.162.1.2`` → ``139.162.**.**``；IPv6 保留前两组、其余以
    ``**:**`` 收尾。无法解析的字符串一律回 None（前端显示 --），
    不把未打码的原文漏出去。
    """
    if not ip:
        return None
    text = str(ip).strip()
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv4Address):
        parts = text.split(".")
        return f"{parts[0]}.{parts[1]}.**.**"
    groups = addr.exploded.split(":")
    return f"{groups[0]}:{groups[1]}:**:**"


def region_label(country_code: Optional[str]) -> Optional[str]:
    """CF-IPCountry 代码 → 「SG / 东南亚」式展示串；无代码回 None。"""
    if not country_code:
        return None
    code = str(country_code).strip().upper()
    if len(code) != 2 or not code.isalpha():
        return None
    area = _COUNTRY_AREA.get(code)
    return f"{code} / {area}" if area else code
