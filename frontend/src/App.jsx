import { useState, useRef, useEffect } from 'react'
import { sendChat, sendChatStream, resetChat } from './api'
import Message from './components/Message'
import QuickSuggestions from './components/QuickSuggestions'

const SID = (() => {
  let s = sessionStorage.getItem('emx_sid')
  if (!s) { s = 'demo-' + Math.random().toString(36).slice(2); sessionStorage.setItem('emx_sid', s) }
  return s
})()

const GREETING = { role: 'bot', text: 'Dạ em là trợ lý Điện Máy Xanh. Anh/chị cần tư vấn hay hỗ trợ gì ạ?' }

export default function App() {
  const [messages, setMessages] = useState([GREETING])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // Status text shown in the typing indicator while waiting for the stream;
  // null = not waiting (indicator hidden, e.g. once reply text starts arriving).
  const [status, setStatus] = useState(null)
  const chatRef = useRef(null)

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight
  }, [messages, status])

  // Replace the last message in the list (used to grow / finalize the streamed reply).
  function replaceLast(msg) {
    setMessages((m) => [...m.slice(0, -1), msg])
  }

  async function send(text) {
    if (!text || busy) return
    setMessages((m) => [...m, { role: 'user', text }])
    setInput(''); setBusy(true); setStatus('Em đang xử lý…')
    let streamed = false // true once the first delta created the bot bubble
    try {
      const res = await sendChatStream(SID, text, {
        onStatus: setStatus,
        onDelta: (chunk) => {
          if (!streamed) {
            streamed = true
            setStatus(null)
            setMessages((m) => [...m, { role: 'bot', text: chunk }])
          } else {
            setMessages((m) => {
              const last = m[m.length - 1]
              return [...m.slice(0, -1), { ...last, text: last.text + chunk }]
            })
          }
        },
      })
      const finalMsg = { role: 'bot', text: res.reply, recommendation: res.recommendation, trace: res.trace }
      if (streamed) replaceLast(finalMsg)
      else setMessages((m) => [...m, finalMsg])
    } catch (e) {
      if (e.phase === 'connect') {
        // Stream endpoint unreachable — turn not processed yet, safe to retry once via sync API.
        try {
          const res = await sendChat(SID, text)
          setMessages((m) => [...m, { role: 'bot', text: res.reply, recommendation: res.recommendation, trace: res.trace }])
        } catch {
          setMessages((m) => [...m, { role: 'bot', text: 'Xin lỗi, hệ thống đang bận. Anh/chị thử lại nhé.' }])
        }
      } else {
        // Broke mid-stream — turn already processed server-side, do NOT resend.
        const errMsg = { role: 'bot', text: 'Xin lỗi, kết nối bị gián đoạn. Anh/chị hỏi lại giúp em nhé.' }
        if (streamed) replaceLast(errMsg)
        else setMessages((m) => [...m, errMsg])
      }
    } finally { setBusy(false); setStatus(null) }
  }

  function submit(e) {
    e.preventDefault()
    send(input.trim())
  }

  async function onReset() {
    try {
      await resetChat(SID)
    } catch {
      /* ignore network error; still reset the UI */
    }
    setMessages([GREETING])
  }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <div className="avatar" aria-hidden="true">
            {/* chat-bubble icon (Heroicons) */}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M12 20.25c4.97 0 9-3.694 9-8.25s-4.03-8.25-9-8.25S3 7.444 3 12c0 2.104.859 4.023 2.273 5.48.432.447.74 1.04.586 1.641a4.483 4.483 0 0 1-.923 1.785A5.969 5.969 0 0 0 6 21c1.282 0 2.47-.402 3.445-1.087.81.22 1.668.337 2.555.337Z" />
            </svg>
          </div>
          <div>
            <h1>Trợ lý AI Điện Máy Xanh</h1>
            <div className="tagline"><span className="online-dot" />Luôn sẵn sàng tư vấn cho bạn</div>
          </div>
        </div>
        <button onClick={onReset}>Làm mới</button>
      </header>
      <div className="chat" ref={chatRef}>
        {messages.map((m, i) => (
          <Message key={i} msg={m} isLast={i === messages.length - 1} onSuggest={send} disabled={busy} />
        ))}
        {messages.length === 1 && <QuickSuggestions onPick={send} disabled={busy} />}
        {busy && status !== null && (
          <div className="msg bot">
            <div className="bubble typing" aria-label="Đang trả lời">
              <span className="status-text">{status}</span>
              <span className="dots"><span /><span /><span /></span>
            </div>
          </div>
        )}
      </div>
      <form className="composer" onSubmit={submit}>
        {/* Input stays enabled while the bot replies: the user can pre-type the next
            question (send() guards against double-send) and focus is never kicked out. */}
        <input value={input} onChange={(e) => setInput(e.target.value)} autoFocus
               aria-label="Nội dung tư vấn"
               placeholder="Ví dụ: Nhà mình có 4 người, cần tủ lạnh tiết kiệm điện dưới 20 triệu" />
        <button disabled={busy} aria-label={busy ? 'Đang trả lời…' : 'Gửi tin nhắn'}>
          {busy ? (
            /* spinner: partial circle that rotates while the bot is replying */
            <svg className="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
            </svg>
          ) : (
            /* paper-plane icon (Heroicons) */
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
            </svg>
          )}
        </button>
      </form>
    </div>
  )
}
