// node 模块定制钩子：videoDiag.test.mjs 专用。
//   - '@/api'（webPlayer.ts 的 JWT 客户端）→ 桩 axios（记录 get 调用、回 canned 数据）；
//   - '@/api/emby' → 桩（webPlayer.ts 只用 embyAuthConfig/ensureEmbyBase/secondsToTicks）。
// 其余走默认解析；'@/utils/videoDiag' 是纯类型 import，类型剥离后不解析。
export async function resolve(specifier, context, nextResolve) {
  if (specifier === '@/api') {
    return { url: new URL('./stubs/api-diag.mjs', import.meta.url).href, shortCircuit: true }
  }
  if (specifier === '@/api/emby') {
    return { url: new URL('./stubs/emby-diag.mjs', import.meta.url).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
