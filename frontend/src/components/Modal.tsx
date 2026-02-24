import type { ReactNode } from 'react'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}

export default function Modal({ isOpen, onClose, title, subtitle, children, footer }: ModalProps) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-lg p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-lg font-semibold mb-1">{title}</h3>
        {subtitle && <p className="text-sm text-gray-500 mb-4 truncate">{subtitle}</p>}
        {children}
        {footer && <div className="flex gap-3 mt-6">{footer}</div>}
      </div>
    </div>
  )
}
