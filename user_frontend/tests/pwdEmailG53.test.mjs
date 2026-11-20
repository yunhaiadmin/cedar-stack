// G-53/G-54 契约测试（2026-11-18 参考站 差异定案）：改密规则＋恢复邮箱。
// 运行：cd user_frontend && node tests/pwdEmailG53.test.mjs
// 定案（reports/参考站-gap-decisions-20261118.md G-53/G-54）：
//  G-53 新密码 8–64 位；改密＝当前密码＋恢复邮箱 6 位验证码；无邮箱只验
//       当前密码、不锁死，前端按模式决定渲不渲染验证码字段；
//  G-54 恢复邮箱＝注册邮箱、脱敏展示；更换＝验当前密码→新邮箱验证码→
//       确认更换（后端同时向旧邮箱发通知）。
// 惯例同 tests/trashRecordFamily.test.mjs：node 源码契约检查。
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(ROOT, p), 'utf8')

const apiIndex = read('src/api/index.ts')
const pwdModal = read('src/components/profile/ChangePasswordModal.vue')
const settings = read('src/views/game/AccountSettingsView.vue')
const emailModalPath = 'src/components/profile/ChangeEmailModal.vue'

let passed = 0
function check(name, cond) {
  assert.ok(cond, name)
  passed += 1
  console.log(`PASS ${name}`)
}

/* ---------- API 层 ---------- */
check('A1 改密发码端点已接', apiIndex.includes('/api/user/auth/change-password/send-code'))
check('A2 换邮箱发码端点已接', apiIndex.includes('/api/user/auth/change-email/send-code'))
check('A3 换邮箱确认端点已接', apiIndex.includes("'/api/user/auth/change-email'") || apiIndex.includes('"/api/user/auth/change-email"') || apiIndex.includes("api.post('/api/user/auth/change-email'"))
check('A4 changePassword 入参带 email_code', /changePassword: \(data: \{[^}]*email_code/.test(apiIndex))

/* ---------- G-53 改密弹窗 ---------- */
check('P1 新密码下限改 8 位（旧 6 位校验已删）', !pwdModal.includes('newPassword.length < 6') && !pwdModal.includes('新密码至少 6 位'))
check('P2 8–64 校验文案在位', pwdModal.includes('8-64') || pwdModal.includes('8–64'))
check('P3 验证码字段只在邮箱模式渲染', /v-if="[^"]*email/i.test(pwdModal) && pwdModal.includes('邮箱验证码'))
check('P4 发码动作接 sendChangePasswordCode', pwdModal.includes('sendChangePasswordCode'))
check('P5 提交带 email_code', pwdModal.includes('email_code'))
check('P6 无邮箱模式不渲染验证码（模式来自账号邮箱）', pwdModal.includes('user?.email') || pwdModal.includes('has_recovery_email') || pwdModal.includes('recovery_email'))

/* ---------- G-54 换邮箱弹窗与设置行 ---------- */
check('E1 换邮箱弹窗存在', existsSync(join(ROOT, emailModalPath)))
const emailModal = existsSync(join(ROOT, emailModalPath)) ? read(emailModalPath) : ''
check('E2 弹窗含当前密码/新邮箱/验证码三字段', emailModal.includes('当前密码') && emailModal.includes('新邮箱') && emailModal.includes('验证码'))
check('E3 发码接 sendChangeEmailCode', emailModal.includes('sendChangeEmailCode'))
check('E4 确认接 changeEmail', emailModal.includes('changeEmail'))
check('E5 成功后同步本地用户邮箱', emailModal.includes('updateUser'))
check('S1 设置页有恢复邮箱行', settings.includes('恢复邮箱'))
check('S2 恢复邮箱脱敏展示（mask）', /mask/i.test(settings) && settings.includes('邮箱'))
check('S3 恢复邮箱行挂换邮箱弹窗', settings.includes('ChangeEmailModal'))

console.log(`\n${passed} passed`)
