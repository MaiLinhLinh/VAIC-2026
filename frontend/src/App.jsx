import { useState } from 'react'
import { sendChat, resetChat } from './api'
import Message from './components/Message'

const SID = 'demo-' + Math.random().toString(36).slice(2)

export default function App() {
  const [messages, setMessages] = useState([
    { role: 'bot', text: 'Dạ em là trợ lý Điện Máy Xanh. Anh/chị cần tư vấn sản phẩm gì ạ?' },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setMessages((m) => [...m, { role: 'user', text }])
    setInput(''); setBusy(true)
    try {
      const res = await sendChat(SID, text)
      setMessages((m) => [...m, { role: 'bot', text: res.reply, recommendation: res.recommendation }])
    } catch {
      setMessages((m) => [...m, { role: 'bot', text: 'Xin lỗi, hệ thống đang bận. Anh/chị thử lại nhé.' }])
    } finally { setBusy(false) }
  }

  async function onReset() {
    await resetChat(SID)
    setMessages([{ role: 'bot', text: 'Đã làm mới. Anh/chị cần tư vấn gì ạ?' }])
  }

  return (
    <div className="app">
      <header><h1>Trợ lý AI Điện Máy Xanh</h1><button onClick={onReset}>Làm mới</button></header>
      <div className="chat">{messages.map((m, i) => <Message key={i} msg={m} />)}</div>
      <form className="composer" onSubmit={submit}>
        <input value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
               placeholder="VD: mua tu lanh duoi 20tr cho nha 4 nguoi, tiet kiem dien" />
        <button disabled={busy}>{busy ? '...' : 'Gửi'}</button>
      </form>
    </div>
  )
}
