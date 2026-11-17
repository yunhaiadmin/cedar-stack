/**
 * 字幕/音轨语言标签（唯一实现）：ISO 语言码 → 中文名。
 * 原在 PlayerSchemeSheet 内，详情页字幕清单也要用同一套口径，抽到此处
 * 共用，不许再复制第二份语言表。
 */

export const SUB_LANG_LABELS: Record<string, string> = {
  eng: '英文', en: '英文',
  jpn: '日文', ja: '日文',
  kor: '韩文', ko: '韩文',
  fra: '法文', fre: '法文', fr: '法文',
  deu: '德文', ger: '德文', de: '德文',
  spa: '西班牙文', es: '西班牙文',
  rus: '俄文', ru: '俄文',
  ita: '意大利文', it: '意大利文',
  por: '葡萄牙文', pt: '葡萄牙文',
  tha: '泰文', th: '泰文',
  vie: '越南文', vi: '越南文',
  ind: '印尼文', id: '印尼文',
  ara: '阿拉伯文', ar: '阿拉伯文',
}

/** 字幕语言名：中文再按文件名里的简/繁标记细分（标记来自文件名本身，不猜） */
export function subLangLabel(lang?: string | null, file = ''): string {
  const code = (lang || '').trim().toLowerCase()
  if (code === 'chi' || code === 'zho' || code === 'zh') {
    if (/cht|zh-tw|zh-hk|zh-hant|big5|繁/.test(file)) return '繁中'
    if (/chs|zh-cn|zh-hans|简/.test(file)) return '简中'
    return '中文'
  }
  if (SUB_LANG_LABELS[code]) return SUB_LANG_LABELS[code]
  return code ? code.toUpperCase() : '外挂'
}

/** 音轨语言标注：与字幕共用语言表；轨没标语言就直说未标注，不猜 */
export function audioLangLabel(lang?: string | null): string {
  const code = (lang || '').trim().toLowerCase()
  if (!code) return '未标注语言'
  if (code === 'chi' || code === 'zho' || code === 'zh') return '中文'
  return SUB_LANG_LABELS[code] || code.toUpperCase()
}

/** 内嵌字幕标签：DisplayTitle 优先；后端 DisplayTitle 缺 display_title
    时回填了语言码（chi/eng…），先翻译回中文名再显示；都没有才用
    Title / 语言兜底，仍为空时只写「字幕」 */
export function embeddedSubLabel(s: { DisplayTitle?: string; Title?: string | null; Language?: string }): string {
  const title = (s.DisplayTitle || '').trim()
  if (title) {
    const code = title.toLowerCase()
    if (code === 'chi' || code === 'zho' || code === 'zh' || SUB_LANG_LABELS[code]) {
      return subLangLabel(title, s.Title || '')
    }
    return title
  }
  const t = (s.Title || '').trim()
  if (t) return t
  const lang = (s.Language || '').trim()
  if (!lang) return '字幕'
  return subLangLabel(lang, '')
}
