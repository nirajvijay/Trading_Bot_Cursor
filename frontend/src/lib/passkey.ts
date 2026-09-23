type JsonObject = Record<string, unknown>

function base64urlToBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((value.length + 3) % 4)
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes.buffer
}

function bufferToBase64url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function isSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential !== 'undefined'
}

/** True when this machine has a built-in verifying authenticator (Mac Touch ID).
 *
 * Checked before showing the Touch ID button so a machine that cannot satisfy
 * the ceremony never offers it -- otherwise the button is present and always
 * fails, which reads as a broken site rather than an unsupported device. */
export async function isPlatformAuthenticatorAvailable(): Promise<boolean> {
  if (!isSupported()) return false
  try {
    return await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
  } catch {
    return false
  }
}

/** Turn a WebAuthn DOMException into something worth reading.
 *
 * The browser reports a deliberate cancel, a timeout and a genuine failure
 * through the same NotAllowedError, so the default message ("Touch ID sign-in
 * failed") makes a routine cancel look like a broken login. */
export function describePasskeyError(err: unknown, fallback: string): string {
  if (err instanceof DOMException) {
    if (err.name === 'NotAllowedError') return 'Touch ID was cancelled or timed out.'
    if (err.name === 'InvalidStateError') return 'This Mac is already registered as a passkey.'
    if (err.name === 'SecurityError') return 'Touch ID is not allowed on this address.'
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}

function credentialToJson(credential: PublicKeyCredential): JsonObject {
  const response = credential.response as AuthenticatorAssertionResponse | AuthenticatorAttestationResponse
  const result: JsonObject = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
    },
  }
  if ('authenticatorData' in response) {
    result.response = {
      ...result.response as JsonObject,
      authenticatorData: bufferToBase64url(response.authenticatorData),
      signature: bufferToBase64url(response.signature),
      userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : null,
    }
  } else {
    result.response = {
      ...result.response as JsonObject,
      attestationObject: bufferToBase64url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    }
  }
  return result
}

export async function createPasskey(options: Record<string, unknown>): Promise<JsonObject> {
  if (!isSupported()) throw new Error('Passkeys are not supported by this browser')
  const user = options.user as JsonObject
  const credential = await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: base64urlToBuffer(String(options.challenge)),
      user: {
        ...user,
        id: base64urlToBuffer(String(user.id)),
      },
      excludeCredentials: (options.excludeCredentials as JsonObject[] | undefined)?.map((item) => ({
        ...item,
        id: base64urlToBuffer(String(item.id)),
      })),
    } as PublicKeyCredentialCreationOptions,
  })
  if (!(credential instanceof PublicKeyCredential)) throw new Error('Touch ID setup was cancelled')
  return credentialToJson(credential)
}

export async function getPasskey(options: Record<string, unknown>): Promise<JsonObject> {
  if (!isSupported()) throw new Error('Passkeys are not supported by this browser')
  const credential = await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: base64urlToBuffer(String(options.challenge)),
      allowCredentials: (options.allowCredentials as JsonObject[] | undefined)?.map((item) => ({
        ...item,
        id: base64urlToBuffer(String(item.id)),
      })),
    } as PublicKeyCredentialRequestOptions,
  })
  if (!(credential instanceof PublicKeyCredential)) throw new Error('Touch ID sign-in was cancelled')
  return credentialToJson(credential)
}
