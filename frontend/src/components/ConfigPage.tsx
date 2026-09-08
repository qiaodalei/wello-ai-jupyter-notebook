import { useEffect, useState } from 'react'
import { api } from '../api'
import { useApp } from '../store'

export function ConfigPage() {
  const settings = useApp((s) => s.settings)
  const [form, setForm] = useState({
    api_base: '',
    api_key: '',
    model: '',
    auto_execute: true,
    max_repair_rounds: 5,
  })
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    void useApp.getState().loadAll()
  }, [])

  useEffect(() => {
    if (!settings) return
    setForm({
      api_base: settings.api_base,
      api_key: '',
      model: settings.model,
      auto_execute: settings.auto_execute,
      max_repair_rounds: settings.max_repair_rounds,
    })
  }, [settings])

  const save = async () => {
    const body: Record<string, unknown> = {
      api_base: form.api_base,
      model: form.model,
      auto_execute: form.auto_execute,
      max_repair_rounds: form.max_repair_rounds,
    }
    if (form.api_key.trim()) body.api_key = form.api_key.trim()
    const next = await api.saveSettings(body)
    useApp.getState().setSettings(next as import('../types').Settings)
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1500)
  }

  return (
    <div className="config-page">
      <div className="config-card">
        <a className="back" href="#notebook">
          ← 返回 Notebook
        </a>
        <h1>Wello AI Jupyter 模型配置</h1>
        <p>
          这里单独存放 API 与模型，不会出现在 Notebook 页面里，也不会写入 .ipynb。也可以改项目根目录的{' '}
          <code>.env</code>。
        </p>
        <div className="field">
          <label>API Base URL</label>
          <input
            value={form.api_base}
            onChange={(e) => setForm({ ...form, api_base: e.target.value })}
            placeholder="https://api.openai.com/v1"
          />
        </div>
        <div className="field">
          <label>API Key {settings?.has_api_key ? '（已保存，留空则不修改）' : ''}</label>
          <input
            type="password"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            placeholder={settings?.has_api_key ? '••••••••' : 'sk-...'}
            autoComplete="off"
          />
        </div>
        <div className="field">
          <label>模型名</label>
          <input
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="gpt-4o"
          />
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={form.auto_execute}
            onChange={(e) => setForm({ ...form, auto_execute: e.target.checked })}
          />
          生成代码后自动执行
        </label>
        <div className="field">
          <label>最大自动修复轮数</label>
          <input
            type="number"
            min={1}
            max={20}
            value={form.max_repair_rounds}
            onChange={(e) => setForm({ ...form, max_repair_rounds: Number(e.target.value) })}
          />
        </div>
        <div className="modal-actions">
          {saved && <span className="hint">已保存</span>}
          <button className="btn primary" onClick={() => void save()}>
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
