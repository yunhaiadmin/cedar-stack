// node 模块定制钩子：softSeekResume.test.mjs 专用。
//   - './decodeSupport' → 真实文件（与 softDecode.hooks.mjs 同口径）；
//   - '/libmedia/1.3.1/avplayer.js'（createSoftPlayer 动态 import 的
//     站内托管地址）→ 假播放器 fakeAvPlayer.mjs，测试直接 import
//     同一文件即可拿到实例（同一 URL = 同一模块实例）。
export async function resolve(specifier, context, nextResolve) {
  if (
    specifier === './decodeSupport'
    && (context.parentURL || '').endsWith('/src/utils/softDecode.ts')
  ) {
    return { url: new URL('../src/utils/decodeSupport.ts', import.meta.url).href, shortCircuit: true }
  }
  if (
    specifier === '/libmedia/1.3.1/avplayer.js'
    && (context.parentURL || '').endsWith('/src/utils/softDecode.ts')
  ) {
    return { url: new URL('./fakeAvPlayer.mjs', import.meta.url).href, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
