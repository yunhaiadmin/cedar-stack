/**
 * WebAuthn 最小封装（手写，不引第三方包）：只做 base64url ↔ ArrayBuffer
 * 转换与 credentials.create/get 两条流程的 JSON 适配。
 * 后端（webauthn 库）下发的 options 是可直接 JSON 序列化的字典，
 * challenge / 凭据 id 在 JSON 里是 base64url 字符串，浏览器 API 要 ArrayBuffer，
 * 本文件负责这层翻译；返回的凭据同样翻回 JSON 再交后端校验。
 */

export function base64UrlToBuffer(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

export function bufferToBase64Url(buffer: ArrayBuffer | ArrayBufferView): string {
  const bytes = buffer instanceof ArrayBuffer
    ? new Uint8Array(buffer)
    : new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength)
  let binary = ''
  bytes.forEach((b) => { binary += String.fromCharCode(b) })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** 当前浏览器/设备是否支持 Passkey（WebAuthn + 平台认证器探测） */
export function passkeySupported(): boolean {
  return typeof window !== 'undefined'
    && typeof window.PublicKeyCredential !== 'undefined'
    && !!navigator.credentials
}

/** 注册：options（后端 JSON）→ navigator.credentials.create → 凭据 JSON */
export async function createPasskeyCredential(
  options: Record<string, any>,
): Promise<Record<string, any>> {
  const publicKey = {
    ...options,
    challenge: base64UrlToBuffer(options.challenge),
    user: { ...options.user, id: base64UrlToBuffer(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map((c: any) => ({
      ...c, id: base64UrlToBuffer(c.id),
    })),
  } as PublicKeyCredentialCreationOptions
  const cred = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null
  if (!cred) throw new Error('已取消 Passkey 注册')
  const resp = cred.response as AuthenticatorAttestationResponse
  return {
    id: cred.id,
    rawId: bufferToBase64Url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: (cred as any).authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    response: {
      attestationObject: bufferToBase64Url(resp.attestationObject),
      clientDataJSON: bufferToBase64Url(resp.clientDataJSON),
      transports: typeof (resp as any).getTransports === 'function'
        ? (resp as any).getTransports() : [],
    },
  }
}

/** 登录断言：options（后端 JSON）→ navigator.credentials.get → 凭据 JSON */
export async function getPasskeyAssertion(
  options: Record<string, any>,
): Promise<Record<string, any>> {
  const publicKey = {
    ...options,
    challenge: base64UrlToBuffer(options.challenge),
    allowCredentials: (options.allowCredentials || []).map((c: any) => ({
      ...c, id: base64UrlToBuffer(c.id),
    })),
  } as PublicKeyCredentialRequestOptions
  const cred = await navigator.credentials.get({ publicKey }) as PublicKeyCredential | null
  if (!cred) throw new Error('已取消 Passkey 登录')
  const resp = cred.response as AuthenticatorAssertionResponse
  return {
    id: cred.id,
    rawId: bufferToBase64Url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: (cred as any).authenticatorAttachment ?? null,
    clientExtensionResults: cred.getClientExtensionResults?.() ?? {},
    response: {
      authenticatorData: bufferToBase64Url(resp.authenticatorData),
      clientDataJSON: bufferToBase64Url(resp.clientDataJSON),
      signature: bufferToBase64Url(resp.signature),
      userHandle: resp.userHandle ? bufferToBase64Url(resp.userHandle) : null,
    },
  }
}
