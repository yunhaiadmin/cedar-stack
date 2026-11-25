// node 模块定制钩子：lateRecovery.test.mjs 专用。
//   - './softDecode'（utils/lateRecovery.ts 的导入）→ 真实 softDecode.ts；
//   - './decodeSupport'（utils/softDecode.ts 的导入）→ 真实 decodeSupport.ts。
// 与 nativeSeekGuard.hooks.mjs 同口径：只重定向无后缀的相对导入，其余走默认解析。
export async function resolve(specifier, context, nextResolve) {
  const parent = context.parentURL || ''
  if (specifier === './softDecode' && parent.endsWith('/src/utils/lateRecovery.ts')) {
    return { url: new URL('../src/utils/softDecode.ts', import.meta.url).href, shortCircuit: true }
  }
  if (specifier === './decodeSupport' && parent.endsWith('/src/utils/softDecode.ts')) {
    return { url: new URL('../src/utils/decodeSupport.ts', import.meta.url).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
