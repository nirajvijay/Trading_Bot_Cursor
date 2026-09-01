import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  fetchAdminConfig,
  patchAdminConfig,
  postAdminPause,
  postAdminResume,
} from '../api/client'
import type { AdminConfigResponse, AdminConfigValues } from '../api/types'
import { percentToRatio, ratioToPercent } from '../lib/adminVwapPercent'

const POLL_MS = 3000

export interface AdminFormState {
  per_trade_risk_cap_inr: string
  limited_per_trade_risk_cap_inr: string
  daily_loss_cap_inr: string
  vwap_accept_gap_percent: string
  vwap_limited_gap_percent: string
  comment: string
}

function configToForm(config: AdminConfigResponse): AdminFormState {
  return {
    per_trade_risk_cap_inr: String(config.values.per_trade_risk_cap_inr),
    limited_per_trade_risk_cap_inr: String(config.values.limited_per_trade_risk_cap_inr),
    daily_loss_cap_inr: String(config.values.daily_loss_cap_inr),
    vwap_accept_gap_percent: String(config.vwap_accept_gap_percent),
    vwap_limited_gap_percent: String(config.vwap_limited_gap_percent),
    comment: '',
  }
}

export function isFormDirty(form: AdminFormState, config: AdminConfigResponse): boolean {
  const v = config.values
  if (form.comment.trim() !== '') return true
  if (Number(form.per_trade_risk_cap_inr) !== v.per_trade_risk_cap_inr) return true
  if (Number(form.limited_per_trade_risk_cap_inr) !== v.limited_per_trade_risk_cap_inr) return true
  if (Number(form.daily_loss_cap_inr) !== v.daily_loss_cap_inr) return true
  const acceptRatio = percentToRatio(Number(form.vwap_accept_gap_percent))
  const limitedRatio = percentToRatio(Number(form.vwap_limited_gap_percent))
  if (Math.abs(acceptRatio - v.vwap_accept_gap_exclusive_max) > 1e-9) return true
  if (Math.abs(limitedRatio - v.vwap_limited_gap_inclusive_max) > 1e-9) return true
  return false
}

function parseForm(form: AdminFormState): AdminConfigValues {
  const acceptPct = Number(form.vwap_accept_gap_percent)
  const limitedPct = Number(form.vwap_limited_gap_percent)
  if (acceptPct < 0 || acceptPct > 1) {
    throw new Error('ACCEPT threshold must be between 0 and 1 (percent)')
  }
  if (limitedPct <= 0 || limitedPct > 1) {
    throw new Error('LIMITED threshold must be between 0 and 1 (percent)')
  }
  return {
    per_trade_risk_cap_inr: Number(form.per_trade_risk_cap_inr),
    limited_per_trade_risk_cap_inr: Number(form.limited_per_trade_risk_cap_inr),
    daily_loss_cap_inr: Number(form.daily_loss_cap_inr),
    vwap_accept_gap_exclusive_max: percentToRatio(acceptPct),
    vwap_limited_gap_inclusive_max: percentToRatio(limitedPct),
  }
}

function isStepUpRequired(err: unknown): boolean {
  if (err instanceof ApiError) {
    return err.status === 403 && err.message.toLowerCase().includes('step-up')
  }
  return false
}

interface RefreshOptions {
  silent?: boolean
  syncForm?: boolean
}

export function useAdminConfig(enabled: boolean) {
  const [config, setConfig] = useState<AdminConfigResponse | null>(null)
  const [form, setForm] = useState<AdminFormState | null>(null)
  const [initialLoading, setInitialLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [pausing, setPausing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(
    async (options: RefreshOptions = {}) => {
      if (!enabled) return
      const { silent = false, syncForm = false } = options
      if (!silent) setInitialLoading(true)
      try {
        const data = await fetchAdminConfig()
        setConfig(data)
        setForm((prev) => {
          if (syncForm || prev === null) return configToForm(data)
          return prev
        })
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load admin config')
      } finally {
        if (!silent) setInitialLoading(false)
      }
    },
    [enabled],
  )

  useEffect(() => {
    if (!enabled) return
    void refresh()
  }, [enabled, refresh])

  useEffect(() => {
    if (!enabled) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      void refresh({ silent: true })
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [enabled, refresh])

  const dirty = useMemo(() => {
    if (!form || !config) return false
    return isFormDirty(form, config)
  }, [form, config])

  const resetForm = useCallback(() => {
    if (config) setForm(configToForm(config))
  }, [config])

  const updateField = useCallback((field: keyof AdminFormState, value: string) => {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev))
  }, [])

  const save = useCallback(async () => {
    if (!config || !form) return
    setSaving(true)
    setError(null)
    try {
      const values = parseForm(form)
      const data = await patchAdminConfig({
        values,
        expected_version_id: config.version_id,
        comment: form.comment.trim() || null,
      })
      setConfig(data)
      setForm(configToForm(data))
    } catch (err) {
      if (isStepUpRequired(err)) throw err
      setError(err instanceof Error ? err.message : 'Failed to save config')
      throw err
    } finally {
      setSaving(false)
    }
  }, [config, form])

  const pause = useCallback(async () => {
    setPausing(true)
    setError(null)
    try {
      await postAdminPause()
      await refresh({ silent: true })
    } catch (err) {
      if (isStepUpRequired(err)) throw err
      setError(err instanceof Error ? err.message : 'Failed to pause entries')
      throw err
    } finally {
      setPausing(false)
    }
  }, [refresh])

  const resume = useCallback(async () => {
    setPausing(true)
    setError(null)
    try {
      await postAdminResume()
      await refresh({ silent: true })
    } catch (err) {
      if (isStepUpRequired(err)) throw err
      setError(err instanceof Error ? err.message : 'Failed to resume entries')
      throw err
    } finally {
      setPausing(false)
    }
  }, [refresh])

  const displayVwap = config
    ? {
        accept: ratioToPercent(config.values.vwap_accept_gap_exclusive_max),
        limited: ratioToPercent(config.values.vwap_limited_gap_inclusive_max),
      }
    : null

  return {
    config,
    form,
    initialLoading,
    saving,
    pausing,
    dirty,
    error,
    displayVwap,
    refresh,
    resetForm,
    updateField,
    save,
    pause,
    resume,
  }
}
