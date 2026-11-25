// @/api/emby 桩（videoDiag.test.mjs 专用）：webPlayer.ts 只用这三个。
export function embyAuthConfig() {
  return {}
}
export async function ensureEmbyBase() {
  return 'http://ea.example'
}
export function secondsToTicks(s) {
  return s * 10000000
}
