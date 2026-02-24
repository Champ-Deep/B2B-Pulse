export function Spinner({ className = '' }: { className?: string }) {
  return (
    <div className={`animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 ${className}`} />
  )
}

export function PageLoading() {
  return (
    <div className="flex items-center justify-center py-12">
      <Spinner />
    </div>
  )
}

export function SectionLoading() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-8 bg-gray-200 rounded w-1/3" />
      <div className="h-4 bg-gray-200 rounded w-2/3" />
      <div className="h-4 bg-gray-200 rounded w-1/2" />
    </div>
  )
}
