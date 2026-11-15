// @/api 桩（playerPrefs.test.mjs 专用）：记录 get/put 调用，行为可注入。
// playerPrefs.ts 只用默认导出的 axios 形对象（get/put 返回已拆包的响应体，
// 与 src/api/index.ts 拦截器口径一致）。
const api = {
  calls: [],
  _getResponse: { preferences: null },
  _getFails: false,
  _putFails: false,
  __reset() {
    this.calls = []
    this._getResponse = { preferences: null }
    this._getFails = false
    this._putFails = false
  },
  __setGetResponse(res) {
    this._getResponse = res
  },
  __setGetFails(v) {
    this._getFails = v
  },
  __setPutFails(v) {
    this._putFails = v
  },
  async get(url, config) {
    this.calls.push({ method: 'get', url, config })
    if (this._getFails) throw new Error('mock get fail')
    return this._getResponse
  },
  async put(url, body) {
    this.calls.push({ method: 'put', url, body })
    if (this._putFails) throw new Error('mock put fail')
    return { preferences: body }
  },
}

export default api
