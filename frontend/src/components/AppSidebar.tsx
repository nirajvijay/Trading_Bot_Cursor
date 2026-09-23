import type { AppTab } from './TopAppBar'

interface NavItem {
  id: AppTab
  icon: string
  label: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'checklist', icon: 'fact_check', label: 'Checklist' },
  { id: 'radar', icon: 'grid_view', label: 'Radar' },
  { id: 'trading', icon: 'monitoring', label: 'Desk' },
  { id: 'admin', icon: 'tune', label: 'Admin' },
]

interface Props {
  activeTab: AppTab
  onTabChange: (tab: AppTab) => void
}

export function AppSidebar({ activeTab, onTabChange }: Props) {
  return (
    <aside className="w-24 shrink-0 bg-[#0f172a] flex flex-col items-center py-3 border-r border-[#1e293b] z-50">
      <div className="flex flex-col items-center gap-2 flex-1">
        {NAV_ITEMS.map((item) => {
          const isActive = item.id === activeTab
          return (
            <button
              key={item.id}
              type="button"
              title={item.label}
              onClick={() => onTabChange(item.id)}
              aria-current={isActive ? 'page' : undefined}
              className={`w-20 min-h-16 flex flex-col gap-1 items-center justify-center rounded transition-colors ${
                isActive
                  ? 'bg-primary text-white'
                  : 'text-slate-400 hover:bg-[#1e293b] hover:text-slate-200'
              }`}
            >
              <span className="material-symbols-outlined text-[22px]">{item.icon}</span>
              <span className="text-sm">{item.label}</span>
            </button>
          )
        })}
      </div>
    </aside>
  )
}
