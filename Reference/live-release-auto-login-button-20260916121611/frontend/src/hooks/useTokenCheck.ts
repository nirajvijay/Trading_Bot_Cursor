import { useCallback, useEffect, useState } from 'react'
import { postCheckToken } from '../api/client'
import type { CheckTokenResponse, PreMarketChecklistResponse } from '../api/types'
import { loadTokenCheck, saveTokenCheck } from '../lib/tokenCheckStorage'

export function useTokenCheck(
  sessionDate: string,
  checklistData: PreMarketChecklistResponse | null,
  onAfterCheck?: () => void | Promise<void>,
) {
  const [tokenCheck, setTokenCheck] = useState<CheckTokenResponse | null>(() => {
    const stored = loadTokenCheck(sessionDate)
    return stored?.result ?? null
  })
  const [tokenCheckedAt, setTokenCheckedAt] = useState<string | null>(() => {
    const stored = loadTokenCheck(sessionDate)
    return stored?.checked_at ?? null
  })
  const [tokenChecking, setTokenChecking] = useState(false)

  useEffect(() => {
    const stored = loadTokenCheck(sessionDate)
    if (stored) {
      setTokenCheck(stored.result)
      setTokenCheckedAt(stored.checked_at)
      return
    }
    setTokenCheck(null)
    setTokenCheckedAt(null)
  }, [sessionDate])

  useEffect(() => {
    const kite = checklistData?.areas.kite_auth
    if (!kite?.token_validated_today) return
    setTokenCheck((prev) =>
      prev?.valid
        ? prev
        : {
            valid: true,
            message: kite.message,
            user_id: undefined,
          },
    )
    if (kite.token_checked_at) {
      setTokenCheckedAt(kite.token_checked_at)
    }
  }, [checklistData])

  const checkToken = useCallback(async () => {
    setTokenChecking(true)
    try {
      const result = await postCheckToken()
      setTokenCheck(result)
      const checkedAt = new Date().toISOString()
      setTokenCheckedAt(checkedAt)
      saveTokenCheck(sessionDate, result)
      await onAfterCheck?.()
      return result
    } catch {
      const failed = { valid: false, message: 'Token check failed' }
      setTokenCheck(failed)
      const checkedAt = new Date().toISOString()
      setTokenCheckedAt(checkedAt)
      saveTokenCheck(sessionDate, failed)
      await onAfterCheck?.()
      return failed
    } finally {
      setTokenChecking(false)
    }
  }, [sessionDate, onAfterCheck])

  return { tokenCheck, tokenCheckedAt, tokenChecking, checkToken }
}
