import { useState } from 'react'

interface Props {
  command: string
  label?: string
  variant?: 'primary' | 'secondary'
}

export function CopyCommandButton({ command, label = 'Copy Command', variant = 'secondary' }: Props) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  const base =
    variant === 'primary'
      ? 'bg-primary text-white border-primary hover:bg-primary/90'
      : 'bg-white text-on-surface border-outline-variant hover:bg-surface-container-low'

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      className={`flex items-center gap-1 px-2.5 py-1 rounded label-caps text-[10px] font-bold border transition-colors ${base}`}
    >
      <span className="material-symbols-outlined text-[14px]">content_copy</span>
      {copied ? 'Copied' : label}
    </button>
  )
}
