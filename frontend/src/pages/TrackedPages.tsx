import { useEffect, useRef, useState } from 'react'
import api from '../api/client'
import { PlatformBadge } from '../components/Badge'
import { PageLoading } from '../components/Loading'
import Modal from '../components/Modal'
import { useToast } from '../components/Toast'
import type { ImportResult, PollStatus, PostWithEngagements, SubscribeSettings, TrackedPage } from '../lib/types'
import { getApiErrorMessage } from '../lib/utils'

export default function TrackedPages() {
  const toast = useToast()
  const [pages, setPages] = useState<TrackedPage[]>([])
  const [newUrl, setNewUrl] = useState('')
  const [newName, setNewName] = useState('')
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')

  // Subscribe modal state
  const [subscribeModal, setSubscribeModal] = useState<TrackedPage | null>(null)
  const [subSettings, setSubSettings] = useState<SubscribeSettings>({
    auto_like: true,
    auto_comment: true,
    polling_mode: 'normal',
  })

  // Submit post modal state
  const [submitPostPage, setSubmitPostPage] = useState<TrackedPage | null>(null)
  const [submitPostUrl, setSubmitPostUrl] = useState('')
  const [submittingPost, setSubmittingPost] = useState(false)

  // CSV import state
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Poll Now state
  const [pollingPageId, setPollingPageId] = useState<string | null>(null)

  // Expanded posts panel
  const [expandedPageId, setExpandedPageId] = useState<string | null>(null)
  const [pagePosts, setPagePosts] = useState<PostWithEngagements[]>([])
  const [loadingPosts, setLoadingPosts] = useState(false)

  // Poll status per page
  const [pollStatuses, setPollStatuses] = useState<Record<string, PollStatus>>({})

  const fetchPages = async () => {
    try {
      const { data } = await api.get('/tracked-pages')
      setPages(data)
    } catch {
      console.error('Failed to load tracked pages')
    } finally {
      setLoading(false)
    }
  }

  const fetchPollStatuses = async (pageList: TrackedPage[]) => {
    const statuses: Record<string, PollStatus> = {}
    await Promise.all(
      pageList.map(async (page) => {
        try {
          const { data } = await api.get(`/tracked-pages/${page.id}/poll-status`)
          statuses[page.id] = data
        } catch {
          // ignore — status just won't show
        }
      }),
    )
    setPollStatuses(statuses)
  }

  useEffect(() => {
    const init = async () => {
      const { data } = await api.get('/tracked-pages')
      setPages(data)
      setLoading(false)
      fetchPollStatuses(data)
    }
    init().catch(() => setLoading(false))
  }, [])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setAdding(true)
    try {
      await api.post('/tracked-pages', { url: newUrl, name: newName })
      setNewUrl('')
      setNewName('')
      await fetchPages()
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, 'Failed to add page'))
    } finally {
      setAdding(false)
    }
  }

  const handleToggle = async (page: TrackedPage) => {
    try {
      await api.put(`/tracked-pages/${page.id}`, { active: !page.active })
      setPages((prev) =>
        prev.map((p) => (p.id === page.id ? { ...p, active: !p.active } : p))
      )
    } catch {
      console.error('Failed to toggle page')
    }
  }

  const handleDelete = async (pageId: string) => {
    if (!window.confirm('Remove this tracked page?')) return
    try {
      await api.delete(`/tracked-pages/${pageId}`)
      setPages((prev) => prev.filter((p) => p.id !== pageId))
    } catch {
      console.error('Failed to delete page')
    }
  }

  const openSubscribeModal = (page: TrackedPage) => {
    setSubSettings({ auto_like: true, auto_comment: true, polling_mode: 'normal' })
    setSubscribeModal(page)
  }

  const handleConfirmSubscribe = async () => {
    if (!subscribeModal) return
    try {
      await api.post(`/tracked-pages/${subscribeModal.id}/subscribe`, subSettings)
      setSubscribeModal(null)
      toast.success('Subscribed!')
    } catch (err: unknown) {
      const message = getApiErrorMessage(err, 'Failed to subscribe')
      if (message === 'Already subscribed') {
        toast.info('You are already subscribed to this page.')
      } else {
        toast.error(message)
      }
      setSubscribeModal(null)
    }
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    setImportResult(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const { data } = await api.post('/tracked-pages/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setImportResult(data)
      await fetchPages()
    } catch (err: unknown) {
      const message = getApiErrorMessage(err, 'Import failed')
      setImportResult({ imported: 0, skipped: 0, errors: [message] })
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleSubmitPost = async () => {
    if (!submitPostPage || !submitPostUrl.trim()) return
    setSubmittingPost(true)
    try {
      await api.post(`/tracked-pages/${submitPostPage.id}/submit-post`, { url: submitPostUrl })
      toast.success('Post submitted for auto-engagement!')
      setSubmitPostPage(null)
      setSubmitPostUrl('')
    } catch (err: unknown) {
      toast.error(getApiErrorMessage(err, 'Failed to submit post'))
    } finally {
      setSubmittingPost(false)
    }
  }

  const handlePollNow = async (pageId: string) => {
    setPollingPageId(pageId)
    try {
      await api.post(`/tracked-pages/${pageId}/poll-now`)
      toast.success('Poll triggered! Check back in a few seconds.')
      // Refresh poll status after a short delay
      setTimeout(async () => {
        try {
          const { data } = await api.get(`/tracked-pages/${pageId}/poll-status`)
          setPollStatuses((prev) => ({ ...prev, [pageId]: data }))
        } catch { /* ignore */ }
        // Also refresh posts if this page is expanded
        if (expandedPageId === pageId) {
          try {
            const { data } = await api.get(`/tracked-pages/${pageId}/posts`)
            setPagePosts(data)
          } catch { /* ignore */ }
        }
      }, 5000)
    } catch (err: unknown) {
      toast.error(getApiErrorMessage(err, 'Failed to trigger poll'))
    } finally {
      setPollingPageId(null)
    }
  }

  const handleTogglePosts = async (pageId: string) => {
    if (expandedPageId === pageId) {
      setExpandedPageId(null)
      setPagePosts([])
      return
    }
    setExpandedPageId(pageId)
    setLoadingPosts(true)
    try {
      const { data } = await api.get(`/tracked-pages/${pageId}/posts`)
      setPagePosts(data)
    } catch {
      toast.error('Failed to load posts')
    } finally {
      setLoadingPosts(false)
    }
  }

  const formatTimeAgo = (isoString: string) => {
    const diff = Date.now() - new Date(isoString).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours}h ago`
    return `${Math.floor(hours / 24)}d ago`
  }

  const renderPollStatus = (pageId: string) => {
    const s = pollStatuses[pageId]
    if (!s || s.status === 'never_polled') {
      return <span className="text-xs text-gray-400">Never polled</span>
    }
    const timeAgo = s.last_polled_at ? formatTimeAgo(s.last_polled_at) : ''
    if (s.status === 'no_cookies') {
      return <span className="text-xs text-orange-600">No cookies configured</span>
    }
    if (s.status === 'error') {
      return <span className="text-xs text-red-600" title={s.error || ''}>Poll error {timeAgo}</span>
    }
    return (
      <span className="text-xs text-green-600">
        {timeAgo} &middot; {s.posts_found} posts, {s.new_posts} new
      </span>
    )
  }

  const renderEngagementBadge = (status: string, type: string) => {
    const label = type === 'like' ? 'Liked' : 'Commented'
    const pendingLabel = type === 'like' ? 'Like pending' : 'Comment pending'
    if (status === 'completed') {
      return <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">{label}</span>
    }
    if (status === 'failed') {
      return <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">{type === 'like' ? 'Like failed' : 'Comment failed'}</span>
    }
    return <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-700">{pendingLabel}</span>
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Tracked Pages</h1>
        <p className="text-gray-500 mt-1">Add LinkedIn, Instagram, or Facebook pages to monitor for new posts</p>
      </div>

      {/* Add Page Form */}
      <form onSubmit={handleAdd} className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Add New Page</h2>
          <div className="flex gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx"
              onChange={handleImportFile}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={importing}
              className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50 transition-colors"
            >
              {importing ? 'Importing...' : 'Import CSV/Excel'}
            </button>
          </div>
        </div>
        {error && <div className="bg-red-50 text-red-600 px-4 py-3 rounded-lg text-sm">{error}</div>}
        {importResult && (
          <div className={`px-4 py-3 rounded-lg text-sm ${importResult.errors.length > 0 ? 'bg-yellow-50 text-yellow-700' : 'bg-green-50 text-green-700'}`}>
            Imported {importResult.imported} pages, skipped {importResult.skipped} duplicates.
            {importResult.errors.length > 0 && (
              <ul className="mt-1 list-disc list-inside">
                {importResult.errors.map((err, i) => <li key={i}>{err}</li>)}
              </ul>
            )}
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2">
            <label className="block text-sm font-medium text-gray-700 mb-1">Page URL</label>
            <input
              type="url"
              required
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              placeholder="linkedin.com/in/username, instagram.com/username, facebook.com/pagename"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Label (optional)</label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g., CEO Blog"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={adding}
            className="px-6 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            {adding ? 'Adding...' : 'Add Page'}
          </button>
          <span className="text-xs text-gray-400">Pages are auto-subscribed with auto-like and auto-comment enabled</span>
        </div>
      </form>

      {/* Pages List */}
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        {loading ? (
          <PageLoading />
        ) : pages.length === 0 ? (
          <div className="p-8 text-center text-gray-400">
            No tracked pages yet. Add a LinkedIn, Instagram, or Facebook page above, or import from CSV.
          </div>
        ) : (
          <div className="divide-y">
            {pages.map((page) => (
              <div key={page.id}>
                {/* Page Row */}
                <div className="flex items-center px-6 py-4 hover:bg-gray-50">
                  {/* Name + Poll Status */}
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm">{page.name}</p>
                    <a href={page.url} target="_blank" rel="noreferrer" className="text-xs text-primary-600 hover:underline truncate block max-w-xs">
                      {page.url}
                    </a>
                    <div className="mt-1">{renderPollStatus(page.id)}</div>
                  </div>

                  {/* Platform */}
                  <div className="px-4">
                    <PlatformBadge platform={page.platform} pageType={page.page_type} />
                  </div>

                  {/* Active Toggle */}
                  <div className="px-4">
                    <button
                      onClick={() => handleToggle(page)}
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                        page.active ? 'bg-primary-600' : 'bg-gray-300'
                      }`}
                    >
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        page.active ? 'translate-x-6' : 'translate-x-1'
                      }`} />
                    </button>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 ml-4">
                    <button
                      onClick={() => handlePollNow(page.id)}
                      disabled={pollingPageId === page.id || !page.active}
                      className="px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg text-xs font-medium hover:bg-indigo-100 disabled:opacity-50 transition-colors"
                    >
                      {pollingPageId === page.id ? 'Polling...' : 'Poll Now'}
                    </button>
                    <button
                      onClick={() => handleTogglePosts(page.id)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                        expandedPageId === page.id
                          ? 'bg-gray-200 text-gray-800'
                          : 'bg-gray-50 text-gray-600 hover:bg-gray-100'
                      }`}
                    >
                      {expandedPageId === page.id ? 'Hide Posts' : 'View Posts'}
                    </button>
                    <button
                      onClick={() => { setSubmitPostPage(page); setSubmitPostUrl('') }}
                      className="px-3 py-1.5 bg-emerald-50 text-emerald-700 rounded-lg text-xs font-medium hover:bg-emerald-100 transition-colors"
                    >
                      Submit Post
                    </button>
                    <button
                      onClick={() => openSubscribeModal(page)}
                      className="text-xs text-primary-600 hover:text-primary-800 font-medium"
                    >
                      Subscribe
                    </button>
                    <button
                      onClick={() => handleDelete(page.id)}
                      className="text-xs text-red-600 hover:text-red-800 font-medium"
                    >
                      Remove
                    </button>
                  </div>
                </div>

                {/* Expanded Posts Panel */}
                {expandedPageId === page.id && (
                  <div className="bg-gray-50 border-t px-6 py-4">
                    {loadingPosts ? (
                      <p className="text-sm text-gray-400">Loading posts...</p>
                    ) : pagePosts.length === 0 ? (
                      <p className="text-sm text-gray-400">No posts discovered yet. Click "Poll Now" or wait for the next poll cycle.</p>
                    ) : (
                      <div className="space-y-3">
                        <p className="text-xs font-medium text-gray-500 uppercase">{pagePosts.length} posts discovered</p>
                        {pagePosts.map((post) => (
                          <div key={post.id} className="bg-white rounded-lg p-4 border">
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex-1 min-w-0">
                                <a href={post.url} target="_blank" rel="noreferrer" className="text-sm text-primary-600 hover:underline break-all">
                                  {post.url}
                                </a>
                                {post.content_text && (
                                  <p className="text-xs text-gray-500 mt-1 line-clamp-2">{post.content_text}</p>
                                )}
                                <p className="text-xs text-gray-400 mt-1">Discovered {formatTimeAgo(post.first_seen_at)}</p>
                              </div>
                              <div className="flex flex-wrap gap-1">
                                {post.engagements.length === 0 ? (
                                  <span className="text-xs text-gray-400">No engagements</span>
                                ) : (
                                  post.engagements.map((eng) => (
                                    <span key={eng.id} title={eng.error_message || ''}>
                                      {renderEngagementBadge(eng.status, eng.action_type)}
                                    </span>
                                  ))
                                )}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Subscribe Modal */}
      <Modal
        isOpen={!!subscribeModal}
        onClose={() => setSubscribeModal(null)}
        title={`Subscribe to ${subscribeModal?.name || 'page'}`}
        subtitle={subscribeModal?.url}
        footer={
          <>
            <button
              onClick={handleConfirmSubscribe}
              className="flex-1 px-4 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors"
            >
              Subscribe
            </button>
            <button
              onClick={() => setSubscribeModal(null)}
              className="flex-1 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-gray-50">
            <input
              type="checkbox"
              checked={subSettings.auto_like}
              onChange={(e) => setSubSettings({ ...subSettings, auto_like: e.target.checked })}
              className="h-4 w-4 text-primary-600 rounded"
            />
            <div>
              <p className="text-sm font-medium">Auto-like new posts</p>
              <p className="text-xs text-gray-500">Automatically like new posts from this page</p>
            </div>
          </label>

          <label className="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-gray-50">
            <input
              type="checkbox"
              checked={subSettings.auto_comment}
              onChange={(e) => setSubSettings({ ...subSettings, auto_comment: e.target.checked })}
              className="h-4 w-4 text-primary-600 rounded"
            />
            <div>
              <p className="text-sm font-medium">Auto-comment on new posts</p>
              <p className="text-xs text-gray-500">Generate and post AI comments on new posts</p>
            </div>
          </label>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Polling Mode</label>
            <select
              value={subSettings.polling_mode}
              onChange={(e) => setSubSettings({ ...subSettings, polling_mode: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="normal">Normal (5-15 min intervals)</option>
              <option value="hunt">Hunt First Comment (30-60s intervals)</option>
            </select>
          </div>
        </div>
      </Modal>

      {/* Submit Post Modal */}
      <Modal
        isOpen={!!submitPostPage}
        onClose={() => setSubmitPostPage(null)}
        title={`Submit Post for ${submitPostPage?.name || 'page'}`}
        subtitle="Paste a post URL to trigger auto-engagement (like/comment)"
        footer={
          <>
            <button
              onClick={handleSubmitPost}
              disabled={submittingPost || !submitPostUrl.trim()}
              className="flex-1 px-4 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {submittingPost ? 'Submitting...' : 'Submit Post'}
            </button>
            <button
              onClick={() => setSubmitPostPage(null)}
              className="flex-1 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </>
        }
      >
        <input
          type="url"
          value={submitPostUrl}
          onChange={(e) => setSubmitPostUrl(e.target.value)}
          placeholder="https://linkedin.com/feed/update/..."
          className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          autoFocus
        />
      </Modal>
    </div>
  )
}
