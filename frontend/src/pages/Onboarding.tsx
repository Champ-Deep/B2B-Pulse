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

const PRESET_RULES = [
  { id: 'no_em_dash', label: 'Never use em dashes or en dashes', default: true },
  { id: 'no_hashtags', label: 'Never use hashtags in comments', default: true },
  { id: 'short_comments', label: 'Keep comments under 2 sentences', default: false },
  { id: 'reference_post', label: 'Always reference specific content from the post', default: true },
  { id: 'first_person', label: 'Use first person (I, we) naturally', default: false },
  { id: 'ask_question', label: 'Try to end with a question when appropriate', default: false },
]

export default function Onboarding() {
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, unknown>>({})
  const [markdownText, setMarkdownText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  // Custom rules state
  const [enabledRules, setEnabledRules] = useState<Set<string>>(
    new Set(PRESET_RULES.filter((r) => r.default).map((r) => r.id))
  )
  const [customRuleText, setCustomRuleText] = useState('')
  const [exampleComments, setExampleComments] = useState('')

  // Total steps: tone questions + rules step + review step
  const TOTAL_STEPS = TONE_QUESTIONS.length + 1
  const isRulesStep = step === TONE_QUESTIONS.length
  const isReviewStep = step > TONE_QUESTIONS.length

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const { data } = await api.get('/users/profile')
        if (data.markdown_text) {
          setMarkdownText(data.markdown_text)
          setStep(TOTAL_STEPS) // Skip to review screen
        }
        if (data.tone_settings) {
          setAnswers(data.tone_settings)
          // Restore custom rules
          if (data.tone_settings.custom_rules) {
            const rules = data.tone_settings.custom_rules as string[]
            const presetIds = new Set<string>()
            const customLines: string[] = []
            for (const rule of rules) {
              const preset = PRESET_RULES.find((p) => p.label === rule)
              if (preset) {
                presetIds.add(preset.id)
              } else {
                customLines.push(rule)
              }
            }
            if (presetIds.size > 0) setEnabledRules(presetIds)
            if (customLines.length > 0) setCustomRuleText(customLines.join('\n'))
          }
          if (data.tone_settings.example_comments) {
            setExampleComments(data.tone_settings.example_comments as string)
          }
        }
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
      setStep(TONE_QUESTIONS.length) // Move to rules step
    }
  }

  const handleRuleToggle = (ruleId: string) => {
    setEnabledRules((prev) => {
      const next = new Set(prev)
      if (next.has(ruleId)) next.delete(ruleId)
      else next.add(ruleId)
      return next
    })
  }

  const handleFinishRules = () => {
    // Build custom_rules array
    const rules: string[] = PRESET_RULES.filter((r) => enabledRules.has(r.id)).map((r) => r.label)
    if (customRuleText.trim()) {
      rules.push(...customRuleText.trim().split('\n').filter(Boolean))
    }

    const updatedAnswers = { ...answers, custom_rules: rules, example_comments: exampleComments }
    setAnswers(updatedAnswers)

    // Generate markdown
    const profile = generateProfileMarkdown(updatedAnswers as unknown as Record<string, string>)
    setMarkdownText(profile)
    setStep(TOTAL_STEPS) // Move to review step
  }

  const generateProfileMarkdown = (a: Record<string, string>) => {
    const customRules = (a.custom_rules as unknown as string[]) || []
    const rulesSection = customRules.length > 0
      ? customRules.map((r) => `- ${r}`).join('\n')
      : '- Keep comments authentic and specific to the post'

    return `# My Engagement Style

## Tone
- Formality: ${a.formality || 'Professional but friendly'}
- Emoji usage: ${a.emoji_usage || 'Occasionally'}
- Comment length: ${a.comment_length || '1-2 sentences'}

## Topics I engage with
- ${a.topics || 'General business'}

## Writing Rules
${rulesSection}
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
        {[...Array(TOTAL_STEPS + 1)].map((_, i) => (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full transition-colors ${
              i <= step ? 'bg-primary-600' : 'bg-gray-200'
            }`}
          />
        ))}
      </div>

      {!isRulesStep && !isReviewStep ? (
        /* Tone Question Steps */
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
      ) : isRulesStep ? (
        /* Writing Rules Step */
        <div className="bg-white rounded-xl shadow-sm p-8 space-y-6">
          <h2 className="text-lg font-semibold">Writing Rules</h2>
          <p className="text-sm text-gray-500">
            Select rules for the AI to follow when writing comments in your style.
          </p>

          <div className="space-y-3">
            {PRESET_RULES.map((rule) => (
              <label
                key={rule.id}
                className={`flex items-center gap-3 px-4 py-3 border rounded-lg cursor-pointer transition-colors ${
                  enabledRules.has(rule.id) ? 'border-primary-500 bg-primary-50' : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <input
                  type="checkbox"
                  checked={enabledRules.has(rule.id)}
                  onChange={() => handleRuleToggle(rule.id)}
                  className="rounded text-primary-600 focus:ring-primary-500"
                />
                <span className="text-sm">{rule.label}</span>
              </label>
            ))}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Custom rules (one per line)
            </label>
            <textarea
              value={customRuleText}
              onChange={(e) => setCustomRuleText(e.target.value)}
              rows={3}
              placeholder={'e.g.\nNever mention competitors by name\nAlways be encouraging'}
              className="w-full px-4 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Example comments you have written (optional)
            </label>
            <p className="text-xs text-gray-500 mb-2">
              Paste 3-5 real comments so the AI can match your voice and style.
            </p>
            <textarea
              value={exampleComments}
              onChange={(e) => setExampleComments(e.target.value)}
              rows={5}
              placeholder={'e.g.\n"This is a great point about scaling teams. We ran into the same challenge last year."\n"Love the framework here. How did you handle the edge cases around auth?"'}
              className="w-full px-4 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div className="flex gap-3">
            <button
              onClick={() => setStep((s) => s - 1)}
              className="px-4 py-2.5 text-gray-500 text-sm hover:text-gray-700"
            >
              Back
            </button>
            <button
              onClick={handleFinishRules}
              className="px-6 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors"
            >
              Continue to Review
            </button>
          </div>
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
            rows={18}
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
