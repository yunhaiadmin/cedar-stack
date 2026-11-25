// @/api 桩（videoDiag.test.mjs 专用）：只覆盖 fetchStreamRanges 用到的 get。
// 真实 @/api 的 get 经拦截器直接回 data（见 @/api/danmaku 的用法），
// 这里同样直接回 data 形态；每次调用记下 url 与 config 供断言。
export const getCalls = []
const apiStub = {
  __reset() {
    getCalls.length = 0
  },
  async get(url, config) {
    getCalls.push({ url, config })
    return {
      ranges: [
        {
          range: 'bytes=-1219380021',
          start: 1141964800,
          end: 2361344820,
          suffix: true,
          outcome: 'miss',
          content_range: 'bytes 1141964800-2361344820/2361344821',
          ts: 1,
        },
      ],
    }
  },
}
export default apiStub
