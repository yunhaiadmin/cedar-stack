// node 模块定制钩子：把 playerPrefs.ts 的 @/api 导入重定向到桩；
// 两个 .vue 的 import type 在类型剥离后本不参与运行时解析，一并兜底。
const REDIRECTS = {
  '@/api': './stubs/playerPrefsApi.mjs',
  '@/components/player/PlayerDanmakuSheet.vue': './stubs/empty.mjs',
  '@/components/player/PlayerSubtitleSheet.vue': './stubs/empty.mjs',
}

export async function resolve(specifier, context, nextResolve) {
  if (Object.prototype.hasOwnProperty.call(REDIRECTS, specifier)) {
    return {
      url: new URL(REDIRECTS[specifier], import.meta.url).href,
      shortCircuit: true,
    }
  }
  return nextResolve(specifier, context)
}
