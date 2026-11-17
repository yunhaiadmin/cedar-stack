// node 模块定制钩子：把 utils/probe4k.ts 与 utils/softDecode.ts 的
// 扩展名导入重定向到带 .ts 后缀的真实文件，其余模块走默认解析。
// 由 probe4k.test.mjs 在动态 import 前注册（与 softDecode.hooks.mjs 同惯例）。
export async function resolve(specifier, context, nextResolve) {
  const parent = context.parentURL || ''
  if (
    specifier === './decodeSupport'
    && (parent.endsWith('/src/utils/probe4k.ts') || parent.endsWith('/src/utils/softDecode.ts'))
  ) {
    return { url: new URL('../src/utils/decodeSupport.ts', import.meta.url).href, shortCircuit: true }
  }
  if (specifier === './softDecode' && parent.endsWith('/src/utils/probe4k.ts')) {
    return { url: new URL('../src/utils/softDecode.ts', import.meta.url).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
