/**
 * 模态背景隔离与 Tab 循环。
 * 从模态根向上，把每一层的兄弟节点标成 inert，并记住原来的 inert。
 * 模态根本身及其内部（抽屉、遮罩、菜单）不会被标 inert。
 * release 按原值恢复，关闭、断点退出和卸载都必须调用。
 */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

type Snap = { el: HTMLElement; prev: boolean }

export function engageModalIsolation(modalRoot: HTMLElement): () => void {
  const touched: Snap[] = []
  const seen = new Set<HTMLElement>()
  let node: HTMLElement | null = modalRoot
  while (node?.parentElement) {
    const parent = node.parentElement
    for (const child of parent.children) {
      if (!(child instanceof HTMLElement) || child === node || seen.has(child)) continue
      if (child === modalRoot || modalRoot.contains(child)) continue
      seen.add(child)
      touched.push({ el: child, prev: child.inert })
      child.inert = true
    }
    if (parent === document.body) break
    node = parent
  }
  return () => {
    for (const { el, prev } of touched) el.inert = prev
  }
}

export function trapTabKey(event: KeyboardEvent, root: HTMLElement | null) {
  if (event.key !== 'Tab' || !root) return
  const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => {
    const style = getComputedStyle(el)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  if (!(active instanceof Node) || !root.contains(active)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
    return
  }
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}
