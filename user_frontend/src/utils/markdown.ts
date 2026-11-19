/**
 * 极简 Markdown → HTML 渲染（文档面专用，如隐私政策正文）。
 *
 * 只支持政策类文档用得到的子集：# / ## / ### 标题、段落、有序 /
 * 无序列表、**加粗**、`代码`、[文字](链接)。安全性：先整段转义
 * HTML 再套记号规则，原文里的任何标签都不会被当成 HTML 执行；
 * 链接只放行 http(s) / 站内 / 路径 / 锚点，其余协议降级为纯文字。
 * 正文由管理员在后台维护（可信来源），这里仍按不可信输入处理。
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(escaped: string): string {
  let out = escaped.replace(/`([^`]+)`/g, '<code>$1</code>')
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    const href = String(url)
    if (/^(https?:\/\/|\/|#)/i.test(href)) {
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
    }
    return label
  })
  return out
}

export function renderMarkdown(md: string): string {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let listType: 'ol' | 'ul' | null = null

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${renderInline(escapeHtml(paragraph.join(' ')))}</p>`)
      paragraph = []
    }
  }
  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`)
      listType = null
    }
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      closeList()
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed)
    if (heading) {
      flushParagraph()
      closeList()
      const level = heading[1].length
      html.push(`<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`)
      continue
    }
    const ordered = /^\d+[.、)]\s+(.*)$/.exec(trimmed)
    const unordered = /^[-*]\s+(.*)$/.exec(trimmed)
    if (ordered || unordered) {
      flushParagraph()
      const type = ordered ? 'ol' : 'ul'
      if (listType !== type) {
        closeList()
        html.push(`<${type}>`)
        listType = type
      }
      html.push(`<li>${renderInline(escapeHtml((ordered || unordered)![1]))}</li>`)
      continue
    }
    closeList()
    paragraph.push(trimmed)
  }
  flushParagraph()
  closeList()
  return html.join('\n')
}
