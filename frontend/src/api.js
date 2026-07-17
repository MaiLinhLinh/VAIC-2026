export async function sendChat(sessionId, message) {
  const r = await fetch('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!r.ok) throw new Error('API error')
  return r.json()
}
export async function resetChat(sessionId) {
  await fetch('/api/reset', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
}
