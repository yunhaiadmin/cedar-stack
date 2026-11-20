/**
 * BlurHash 解码（前端）：把服务端下发的占位哈希解成一张小位图
 * data URL，海报真图懒加载完成前先渲染它，不留空白块。
 *
 * 哈希由服务端落盘时算出（backend/emby_server/blurhash_util.py，
 * 4×3 分量），两个下发口径：
 * - EA 条目：ImageBlurHashes.Primary["1"]（Emby/Jellyfin 既有字段）；
 * - 门户列表：poster_blur_hash 字段。
 * 解码结果按哈希缓存（同一哈希全站只解一次）；解码失败/环境无
 * canvas 时返回 null，卡片退回纯底色占位，不报错。
 */

const BASE83 =
  '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~'

function decode83(text: string): number {
  let value = 0
  for (const ch of text) {
    const digit = BASE83.indexOf(ch)
    if (digit < 0) throw new Error('blurhash: bad char')
    value = value * 83 + digit
  }
  return value
}

function srgbToLinear(byte: number): number {
  const v = byte / 255
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
}

function linearToSrgb(value: number): number {
  const v = Math.max(0, Math.min(1, value))
  if (v <= 0.0031308) return Math.round(v * 12.92 * 255)
  return Math.round((1.055 * Math.pow(v, 1 / 2.4) - 0.055) * 255)
}

function signPow(value: number, exponent: number): number {
  return Math.sign(value) * Math.pow(Math.abs(value), exponent)
}

const cache = new Map<string, string>()
const CACHE_MAX = 500

/** BlurHash → PNG data URL；非法哈希/无 canvas 环境返回 null。 */
export function blurHashDataUrl(hash: string | null | undefined): string | null {
  if (!hash || hash.length < 6) return null
  const hit = cache.get(hash)
  if (hit) return hit
  try {
    const sizeFlag = decode83(hash[0])
    const numX = (sizeFlag % 9) + 1
    const numY = Math.floor(sizeFlag / 9) + 1
    if (hash.length !== 4 + 2 * numX * numY) return null
    const quantMax = decode83(hash[1])
    const actualMax = (quantMax + 1) / 166
    const dcPacked = decode83(hash.slice(2, 6))
    const components: Array<[number, number, number]> = [
      [
        srgbToLinear((dcPacked >> 16) & 0xff),
        srgbToLinear((dcPacked >> 8) & 0xff),
        srgbToLinear(dcPacked & 0xff),
      ],
    ]
    for (let i = 0; i < numX * numY - 1; i++) {
      const packed = decode83(hash.slice(6 + i * 2, 8 + i * 2))
      const qr = Math.floor(packed / 361)
      const qg = Math.floor((packed % 361) / 19)
      const qb = packed % 19
      components.push([
        signPow((qr - 9) / 9, 2) * actualMax,
        signPow((qg - 9) / 9, 2) * actualMax,
        signPow((qb - 9) / 9, 2) * actualMax,
      ])
    }
    const width = numX * 8
    const height = numY * 8
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    const imageData = ctx.createImageData(width, height)
    const data = imageData.data
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        let r = 0
        let g = 0
        let b = 0
        for (let cy = 0; cy < numY; cy++) {
          const basisY = Math.cos((Math.PI * cy * y) / height)
          for (let cx = 0; cx < numX; cx++) {
            const basis = Math.cos((Math.PI * cx * x) / width) * basisY
            const comp = components[cy * numX + cx]
            r += basis * comp[0]
            g += basis * comp[1]
            b += basis * comp[2]
          }
        }
        const offset = (y * width + x) * 4
        data[offset] = linearToSrgb(r)
        data[offset + 1] = linearToSrgb(g)
        data[offset + 2] = linearToSrgb(b)
        data[offset + 3] = 255
      }
    }
    ctx.putImageData(imageData, 0, 0)
    const url = canvas.toDataURL('image/png')
    if (cache.size >= CACHE_MAX) cache.clear()
    cache.set(hash, url)
    return url
  } catch {
    return null
  }
}

/** EA 条目的海报 BlurHash（ImageBlurHashes.Primary 的 tag 恒为 "1"）。 */
export function posterBlurHashOf(item: {
  ImageBlurHashes?: { Primary?: Record<string, string> } | null
}): string | null {
  return item?.ImageBlurHashes?.Primary?.['1'] ?? null
}
