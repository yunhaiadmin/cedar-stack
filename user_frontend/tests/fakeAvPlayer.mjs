// libmedia AVPlayer 的最小假实现，供 softSeekResume.test.mjs 驱动
// createSoftPlayer 的 seek 看门狗接线。只实现接线用到的那几个方法与
// 事件；不触 DOM、不拉流、不读 wasm。
// 行为由测试用例直接操控：
//   - seek(ts)：定位器立刻落到目标（与真机 demux 定位一致），但不发
//     'seeked'、不发 'time'——模拟「到了目标但解码再也不恢复」的卡死；
//   - tick(seconds)：手动推进播放头并补发 'time' 事件——模拟正常播放；
//   - emit(event, arg)：测试可随时补发任意事件（如 'seeked'、'paused'）。
export const instances = []

export default class FakeAVPlayer {
  // 测试预置：下一实例的 useMSE。true = 平台走了 MSE 管线；
  // false = libmedia 内部否回解码管线（否决看门狗须判失败）；
  // undefined（缺省）= 旧行为（wasm 路，useMSE 不定）。
  static nextUseMSE = undefined
  constructor(options) {
    this.options = options
    this.handlers = new Map()
    this.currentTime = 0n
    this.durationMs = 5757000n
    this.seekCalls = []
    this.destroyed = false
    this.useMSE = FakeAVPlayer.nextUseMSE
    instances.push(this)
  }

  on(event, handler) {
    if (!this.handlers.has(event)) this.handlers.set(event, [])
    this.handlers.get(event).push(handler)
  }

  emit(event, arg) {
    for (const h of this.handlers.get(event) || []) h(arg)
  }

  async load() {}

  async play() {
    this.emit('playing')
  }

  async pause() {
    this.emit('paused')
  }

  async seek(timestamp) {
    this.seekCalls.push(timestamp)
    this.currentTime = BigInt(timestamp)
  }

  setPlaybackRate() {}

  getDuration() {
    return this.durationMs
  }

  getVolume() {
    return 1
  }

  hasAudio() {
    return true
  }

  hasVideo() {
    return true
  }

  async destroy() {
    this.destroyed = true
  }

  tick(seconds) {
    this.currentTime += BigInt(Math.round(seconds * 1000))
    this.emit('time', this.currentTime)
  }
}
