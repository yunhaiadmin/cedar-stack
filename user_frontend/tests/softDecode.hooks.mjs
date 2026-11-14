// node 模块定制钩子：只把 utils/softDecode.ts 对 './decodeSupport' 的
// 导入重定向到带 .ts 后缀的真实文件，其余模块走默认解析。
// 由 softDecode.test.mjs 在动态 import 前注册。
export async function resolve(specifier, context, nextResolve) {
  if (
    specifier === './decodeSupport'
    && (context.parentURL || '').endsWith('/src/utils/softDecode.ts')
  ) {
    return { url: new URL('../src/utils/decodeSupport.ts', import.meta.url).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
