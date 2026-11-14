/**
 * 模态背景隔离与 Tab 循环。
 * 从模态根向上，把每一层的兄弟节点标成 inert，并记住原来的 inert。
 * 模态根本身及其内部（抽屉、遮罩、菜单）不会被标 inert。
 * release 按原值恢复，关闭、断点退出和卸载都必须调用。
 */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

const inertCount = new WeakMap<HTMLElement, number>()
const inertOriginal = new WeakMap<HTMLElement, boolean>()

function keepAlive(el: HTMLElement, keep: HTMLElement[]) {
  return keep.some((node) => node === el || el.contains(node) || node.contains(el))
}

export function engageModalIsolation(modalRoot: HTMLElement, keep: HTMLElement[] = []): () => void {
  const touched: HTMLElement[] = []
  const seen = new Set<HTMLElement>()
  let node: HTMLElement | null = modalRoot
  while (node?.parentElement) {
    const parent = node.parentElement
    for (const child of parent.children) {
      if (!(child instanceof HTMLElement) || child === node || seen.has(child)) continue
      if (child === modalRoot || modalRoot.contains(child) || child.hasAttribute('data-modal-root')) continue
      if (keepAlive(child, keep)) continue
      seen.add(child)
      const count = inertCount.get(child) ?? 0
      if (count === 0) inertOriginal.set(child, child.inert)
      inertCount.set(child, count + 1)
      child.inert = true
      touched.push(child)
    }
    if (parent === document.body) break
    node = parent
  }
  return () => {
    for (const el of touched) {
      const next = (inertCount.get(el) ?? 1) - 1
      if (next <= 0) {
        inertCount.delete(el)
        el.inert = inertOriginal.get(el) ?? false
        inertOriginal.delete(el)
      } else {
        inertCount.set(el, next)
        el.inert = true
      }
    }
  }
}

let overflowDepth = 0
let overflowOriginal = ''

/** 多层模态共用一把锁：只有最外层记住并恢复原来的 overflow，包括非空值。 */
export function lockModalOverflow() {
  if (overflowDepth === 0) overflowOriginal = document.documentElement.style.overflow
  overflowDepth += 1
  document.documentElement.style.overflow = 'hidden'
}

export function unlockModalOverflow() {
  if (overflowDepth === 0) return
  overflowDepth -= 1
  if (overflowDepth === 0) document.documentElement.style.overflow = overflowOriginal
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
