import { useEffect, useState } from 'react'
import api from '../api/client'

const TONE_QUESTIONS = [
  {
    key: 'formality',
    question: 'How formal is your communication style?',
    options: ['Very casual', 'Casual', 'Professional but friendly', 'Formal'],
  },
  {
    key: 'emoji_usage',
    question: 'How often do you use emojis?',
    options: ['Never', 'Occasionally', 'Frequently', 'In almost every message'],
  },
  {
    key: 'comment_length',
    question: 'How long are your typical LinkedIn comments?',
    options: ['One-liners', '1-2 sentences', '2-3 sentences', 'Full paragraphs'],
  },
  {
    key: 'topics',
    question: 'What topics do you typically engage with?',
    options: ['Tech & SaaS', 'Marketing & Growth', 'Sales & Revenue', 'Leadership & Culture'],
  },
]

export default function Onboarding() {
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [markdownText, setMarkdownText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const { data } = await api.get('/users/profile')
        if (data.markdown_text) {
          setMarkdownText(data.markdown_text)
          setStep(TONE_QUESTIONS.length) // Skip to review screen
        }
        if (data.tone_settings) setAnswers(data.tone_settings)
      } catch {
        // No profile yet
      }
    }
    loadProfile()
  }, [])

  const handleAnswer = (key: string, value: string) => {
    setAnswers((prev) => ({ ...prev, [key]: value }))
    if (step < TONE_QUESTIONS.length - 1) {
      setStep((s) => s + 1)
    } else {
      // Generate markdown from answers
      const profile = generateProfileMarkdown({ ...answers, [key]: value })
      setMarkdownText(profile)
      setStep(TONE_QUESTIONS.length) // Move to review step
    }
  }

  const generateProfileMarkdown = (a: Record<string, string>) => {
    return `# My Engagement Style

## Tone
- Formality: ${a.formality || 'Professional but friendly'}
- Emoji usage: ${a.emoji_usage || 'Occasionally'}
- Comment length: ${a.comment_length || '1-2 sentences'}

## Topics I engage with
- ${a.topics || 'General business'}

## Rules
- Keep comments authentic and specific to the post
- Reference actual content from the post
- Avoid generic phrases like "great post" or "thanks for sharing"
- Add value: share an opinion, ask a question, or relate to experience
`
  }

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      await api.put('/users/profile', {
        markdown_text: markdownText,
        tone_settings: answers,
      })
      setSaved(true)
    } catch (err) {
      console.error('Failed to save profile:', err)
    } finally {
      setSaving(false)
    }
  }

  const isReviewStep = step >= TONE_QUESTIONS.length

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Profile & Engagement Style</h1>
        <p className="text-gray-500 mt-1">
          Tell us about your communication style so we can generate comments that sound like you
        </p>
      </div>

      {/* Progress */}
      <div className="flex gap-2">
        {TONE_QUESTIONS.map((_, i) => (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full transition-colors ${
              i <= step ? 'bg-primary-600' : 'bg-gray-200'
            }`}
          />
        ))}
        <div className={`h-1.5 flex-1 rounded-full transition-colors ${isReviewStep ? 'bg-primary-600' : 'bg-gray-200'}`} />
      </div>

      {!isReviewStep ? (
        /* Question Step */
        <div className="bg-white rounded-xl shadow-sm p-8">
          <h2 className="text-lg font-semibold mb-6">{TONE_QUESTIONS[step]?.question}</h2>
          <div className="space-y-3">
            {TONE_QUESTIONS[step]?.options.map((option) => (
              <button
                key={option}
                onClick={() => handleAnswer(TONE_QUESTIONS[step]!.key, option)}
                className={`w-full text-left px-4 py-3 border rounded-lg transition-colors hover:border-primary-400 hover:bg-primary-50 ${
                  answers[TONE_QUESTIONS[step]!.key] === option
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-200'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
          {step > 0 && (
            <button
              onClick={() => setStep((s) => s - 1)}
              className="mt-4 text-sm text-gray-500 hover:text-gray-700"
            >
              Back
            </button>
          )}
        </div>
      ) : (
        /* Review & Edit Step */
        <div className="bg-white rounded-xl shadow-sm p-8 space-y-6">
          <h2 className="text-lg font-semibold">Review & Edit Your Profile</h2>
          <p className="text-sm text-gray-500">
            This markdown will be used as context when generating comments in your style. Feel free to edit it.
          </p>
          <textarea
            value={markdownText}
            onChange={(e) => setMarkdownText(e.target.value)}
            rows={15}
            className="w-full px-4 py-3 border border-gray-300 rounded-lg font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          />
          <div className="flex items-center gap-4">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-6 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving...' : 'Save Profile'}
            </button>
            <button
              onClick={() => setStep(0)}
              className="px-4 py-2.5 text-gray-500 text-sm hover:text-gray-700"
            >
              Restart wizard
            </button>
            {saved && <span className="text-green-600 text-sm">Profile saved!</span>}
          </div>
        </div>
      )}
    </div>
  )
}
