"""BlurHash 编码（服务端）：海报/背景落盘后算占位哈希，随列表下发。

为什么仓库内实现而不引 pip 包：算法是公开规范（Wolt BlurHash），
核心就是一次小尺寸 DCT + base83 打包，约百行；引第三方包多一条
供应链与版本面。实现口径对齐参考实现：

- 分量固定 4×3（海报与背景同口径），哈希长 28 字符；
- 先把图缩到至多 32×32 再做 DCT（基函数定义在归一化坐标上，
  小尺寸采样对占位用途无感知差异，CPU 成本可忽略）；
- sRGB ↔ 线性走标准分段函数；DC 打包平均色、AC 以 signPow 量化。

解码在前端（user_frontend/src/utils/blurhash.ts），EA 客户端自带
解码（ImageBlurHashes 是 Emby/Jellyfin 既有字段）。本模块只负责
编码，外加一个 ``average_color`` 供测试/校验解 DC 分量。
"""
from __future__ import annotations

import math
import os
from typing import Optional, Tuple

_BASE83 = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
           "abcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~")
_X_COMP = 4
_Y_COMP = 3
_WORK_SIZE = 32


def _encode83(value: int, length: int) -> str:
    out = []
    for i in range(1, length + 1):
        digit = (value // (83 ** (length - i))) % 83
        out.append(_BASE83[digit])
    return "".join(out)


def _decode83(text: str) -> int:
    value = 0
    for ch in text:
        value = value * 83 + _BASE83.index(ch)
    return value


def _srgb_to_linear(byte: int) -> float:
    v = byte / 255.0
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value: float) -> int:
    v = max(0.0, min(1.0, value))
    if v <= 0.0031308:
        return int(v * 12.92 * 255 + 0.5)
    return int((1.055 * (v ** (1 / 2.4)) - 0.055) * 255 + 0.5)


def _sign_pow(value: float, exponent: float) -> float:
    return math.copysign(abs(value) ** exponent, value)


def encode_image(image, x_comp: int = _X_COMP, y_comp: int = _Y_COMP) -> str:
    """把一张 PIL.Image 编成 BlurHash 字符串（调用方保证已解码）。"""
    from PIL import Image

    img = image.convert("RGB")
    img.thumbnail((_WORK_SIZE, _WORK_SIZE), Image.LANCZOS)
    width, height = img.size
    pixels = img.load()
    # 线性空间像素阵列（一次性换算，DCT 内层只做乘加）
    linear = [[tuple(_srgb_to_linear(c) for c in pixels[x, y])
               for x in range(width)] for y in range(height)]

    components = []
    for cy in range(y_comp):
        for cx in range(x_comp):
            normalisation = 1.0 if (cx == 0 and cy == 0) else 2.0
            scale = normalisation / (width * height)
            r = g = b = 0.0
            for y in range(height):
                basis_y = math.cos(math.pi * cy * y / height)
                if basis_y == 0.0:
                    continue
                for x in range(width):
                    basis = math.cos(math.pi * cx * x / width) * basis_y
                    pr, pg, pb = linear[y][x]
                    r += basis * pr
                    g += basis * pg
                    b += basis * pb
            components.append((r * scale, g * scale, b * scale))

    dc = components[0]
    ac = components[1:]
    size_flag = (x_comp - 1) + (y_comp - 1) * 9
    out = [_encode83(size_flag, 1)]
    if ac:
        max_ac = max(abs(v) for comp in ac for v in comp)
        quant_max = max(0, min(82, int(max_ac * 166 - 0.5)))
        actual_max = (quant_max + 1) / 166.0
    else:
        quant_max = 0
        actual_max = 1.0
    out.append(_encode83(quant_max, 1))
    dc_packed = ((_linear_to_srgb(dc[0]) << 16)
                 + (_linear_to_srgb(dc[1]) << 8)
                 + _linear_to_srgb(dc[2]))
    out.append(_encode83(dc_packed, 4))
    for comp in ac:
        packed = 0
        for channel in comp:
            quant = int(max(0, min(18, math.floor(
                _sign_pow(channel / actual_max, 0.5) * 9 + 9.5))))
            packed = packed * 19 + quant
        out.append(_encode83(packed, 2))
    return "".join(out)


def encode_from_file(path: str, x_comp: int = _X_COMP,
                     y_comp: int = _Y_COMP) -> Optional[str]:
    """从本地图片文件算 BlurHash；文件缺失/损坏/格式不支持 → None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.load()
            return encode_image(img, x_comp, y_comp)
    except Exception:  # noqa: BLE001 — 占位算不出不许拖累调用方
        return None


def average_color(blur_hash: str) -> Optional[Tuple[int, int, int]]:
    """解出哈希的 DC（平均色）分量 → (r, g, b)；非法哈希 → None。"""
    if not blur_hash or len(blur_hash) < 6:
        return None
    try:
        packed = _decode83(blur_hash[2:6])
    except ValueError:
        return None
    return ((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF)
