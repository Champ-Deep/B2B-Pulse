import { useEffect, useState } from 'react'
import api from '../api/client'
import { ActionStatusBadge } from '../components/Badge'
import { PageLoading } from '../components/Loading'
import type { AuditLogEntry } from '../lib/types'

export default function AuditLog() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [actionFilter, setActionFilter] = useState('')

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const params: Record<string, string> = {}
        if (actionFilter) params.action = actionFilter
        const { data } = await api.get('/audit', { params })
        setLogs(data)
      } catch (err) {
        console.error('Failed to load audit logs:', err)
      } finally {
        setLoading(false)
      }
    }
    fetchLogs()
  }, [actionFilter])

  const handleExport = async () => {
    try {
      const response = await api.get('/audit/export', { responseType: 'blob' })
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', 'audit_log.csv')
      document.body.appendChild(link)
      link.click()
      link.remove()
    } catch (err) {
      console.error('Failed to export audit log:', err)
    }
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleString()
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Log</h1>
          <p className="text-gray-500 mt-1">Complete record of all automated actions</p>
        </div>
        <button
          onClick={handleExport}
          className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
        >
          Export CSV
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          <option value="">All actions</option>
          <option value="like_completed">Likes (completed)</option>
          <option value="comment_completed">Comments (completed)</option>
          <option value="like_failed">Likes (failed)</option>
          <option value="comment_failed">Comments (failed)</option>
        </select>
      </div>

      {/* Log Table */}
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        {loading ? (
          <PageLoading />
        ) : logs.length === 0 ? (
          <div className="p-8 text-center text-gray-400">
            No audit log entries yet. Actions will appear here once automation runs.
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-6 py-3 text-xs font-medium text-gray-500 uppercase">Timestamp</th>
                <th className="text-left px-6 py-3 text-xs font-medium text-gray-500 uppercase">Action</th>
                <th className="text-left px-6 py-3 text-xs font-medium text-gray-500 uppercase">Target</th>
                <th className="text-left px-6 py-3 text-xs font-medium text-gray-500 uppercase">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 text-sm text-gray-500 whitespace-nowrap">
                    {formatDate(log.created_at)}
                  </td>
                  <td className="px-6 py-4">
                    <ActionStatusBadge action={log.action} />
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-600">
                    {log.target_type}: {log.target_id?.slice(0, 8)}...
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 max-w-xs truncate">
                    {typeof log.metadata_?.post_url === 'string' && (
                      <a href={log.metadata_.post_url} target="_blank" rel="noreferrer" className="text-primary-600 hover:underline">
                        View post
                      </a>
                    )}
                    {typeof log.metadata_?.comment_text === 'string' && (
                      <span className="ml-2 text-gray-400" title={log.metadata_.comment_text}>
                        &quot;{log.metadata_.comment_text.slice(0, 50)}...&quot;
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
