export async function sendChat(sessionId, message) {
  const r = await fetch('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!r.ok) throw new Error('API error')
  return r.json()
}

// Streaming variant of sendChat (SSE over fetch). Events from the server:
//   {type:'status', text} → onStatus(text)   — pipeline progress
//   {type:'delta', text}  → onDelta(text)    — a slice of the verified reply
//   {type:'done', ...}    → resolved value   — same shape as sendChat()
// Thrown errors carry err.phase:
//   'connect' — request never started processing → caller MAY retry via sendChat
//   'stream'  — broke mid-stream, turn already processed server-side → do NOT resend
export async function sendChatStream(sessionId, message, { onStatus, onDelta }) {
  let r
  try {
    r = await fetch('/api/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message }),
    })
  } catch {
    throw streamError('connect')
  }
  if (!r.ok || !r.body) throw streamError('connect')

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let donePayload = null
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const line = block.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        const ev = JSON.parse(line.slice('data: '.length))
        if (ev.type === 'status') onStatus?.(ev.text)
        else if (ev.type === 'delta') onDelta?.(ev.text)
        else if (ev.type === 'done') donePayload = ev
        else if (ev.type === 'error') throw streamError('stream')
      }
    }
  } catch (e) {
    throw e.phase ? e : streamError('stream')
  }
  if (!donePayload) throw streamError('stream')
  return donePayload
}

function streamError(phase) {
  const err = new Error(`chat stream failed (${phase})`)
  err.phase = phase
  return err
}

export async function resetChat(sessionId) {
  await fetch('/api/reset', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
}
