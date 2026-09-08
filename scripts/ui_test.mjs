// UI smoke test: layout integrity + core notebook interactions.
// Run: node scripts/ui_test.mjs   (frontend dev server + backend must be up)
import { existsSync, readdirSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const { chromium } = await import(
  pathToFileURL(join(root, 'frontend/node_modules/playwright-core/index.mjs')).href
)

function chromePath() {
  const cache = join(homedir(), 'Library/Caches/ms-playwright')
  if (existsSync(cache)) {
    const dir = readdirSync(cache).find((d) => d.startsWith('chromium-'))
    if (dir) {
      const p = join(cache, dir, 'chrome-mac/Chromium.app/Contents/MacOS/Chromium')
      if (existsSync(p)) return p
    }
  }
  return '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
}

const URL = process.env.UI_URL || 'http://127.0.0.1:5173/'
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}

const noOverflow = (page) =>
  page.evaluate(() => ({
    docOverflow: document.documentElement.scrollWidth - window.innerWidth,
    bodyOverflow: document.body.scrollHeight - window.innerHeight,
  }))

const browser = await chromium.launch({ executablePath: chromePath() })
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } })
page.on('pageerror', (e) => check('no runtime error', false, e.message))
await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForSelector('.cell', { timeout: 15000 })

for (const w of [1440, 1280, 1024, 900, 820]) {
  await page.setViewportSize({ width: w, height: 800 })
  await page.waitForTimeout(150)
  const { docOverflow, bodyOverflow } = await noOverflow(page)
  check(`no page overflow @${w}px`, docOverflow <= 0 && bodyOverflow <= 0, `x=${docOverflow} y=${bodyOverflow}`)
  const bars = await page.evaluate(() => {
    const tb = document.querySelector('.toolbar')
    return { h: tb.getBoundingClientRect().height, wrapped: tb.scrollHeight > tb.clientHeight + 1 }
  })
  check(`toolbar stays one row @${w}px`, bars.h <= 36 && !bars.wrapped, `h=${bars.h}`)
}

await page.setViewportSize({ width: 1280, height: 860 })

// panels can be closed and reopened from the status bar
await page.click('.toolbar .tb:text-is("Chat")')
await page.waitForTimeout(120)
check('chat hides', (await page.locator('.chat').count()) === 0)
await page.click('.statusbar .st:has-text("Chat")')
await page.waitForTimeout(120)
check('chat comes back from status bar', (await page.locator('.chat').count()) === 1)

await page.click('.statusbar .st:has-text("Explorer")')
await page.waitForTimeout(120)
check('explorer hides', (await page.locator('.side').count()) === 0)
await page.click('.statusbar .st:has-text("Explorer")')
await page.waitForTimeout(120)
check('explorer comes back', (await page.locator('.side').count()) === 1)

// panel state survives reload
await page.click('.toolbar .tb:text-is("Chat")')
await page.waitForTimeout(120)
await page.reload({ waitUntil: 'networkidle' })
await page.waitForSelector('.cell')
check('closed chat persists across reload', (await page.locator('.chat').count()) === 0)
await page.click('.statusbar .st:has-text("Chat")')
await page.waitForTimeout(150)
check('chat restored after reload', (await page.locator('.chat').count()) === 1)

// nothing overlaps the notebook scroll area
const overlap = await page.evaluate(() => {
  const nb = document.querySelector('.notebook').getBoundingClientRect()
  const bad = []
  for (const sel of ['.toolbar', '.statusbar', '.chat', '.side']) {
    const el = document.querySelector(sel)
    if (!el) continue
    const r = el.getBoundingClientRect()
    const hit = r.left < nb.right && r.right > nb.left && r.top < nb.bottom && r.bottom > nb.top
    if (hit) bad.push(sel)
  }
  return bad
})
check('no panel overlaps the notebook', overlap.length === 0, overlap.join(','))

// the insert row shows up after every cell (code included) and never overlaps them
const gaps = await page.locator('.cell-gap').count()
const kinds = await page.evaluate(() =>
  [...document.querySelectorAll('.cell')].map((c) => (c.querySelector('.md-view, .md-edit') ? 'markdown' : 'code'))
)
let gapOk = true
let gapDetail = ''
for (let i = 0; i < gaps; i++) {
  const gap = page.locator('.cell-gap').nth(i)
  await gap.hover()
  await page.waitForTimeout(100)
  const r = await gap.evaluate((el) => {
    const rule = el.querySelector('.rule')
    const btn = rule.querySelector('button').getBoundingClientRect()
    const box = el.getBoundingClientRect()
    return {
      shown: getComputedStyle(rule).opacity === '1',
      spills: btn.top < box.top - 0.5 || btn.bottom > box.bottom + 0.5,
      hit: document.elementFromPoint(btn.left + 10, btn.top + btn.height / 2)?.tagName,
    }
  })
  if (!r.shown || r.spills || r.hit !== 'BUTTON') {
    gapOk = false
    gapDetail += ` gap#${i}(after ${i === 0 ? 'top' : kinds[i - 1]})=${JSON.stringify(r)}`
  }
}
check(`insert row works on all ${gaps} gaps`, gapOk, gapDetail)

// insert a cell, type code, run it
const before = await page.locator('.cell').count()
await page.click('.toolbar .tb:text-is("+ Code")')
await page.waitForTimeout(400)
check('insert code cell', (await page.locator('.cell').count()) === before + 1)

const selectedIndex = () =>
  page.evaluate(() => [...document.querySelectorAll('.cell')].findIndex((c) => c.classList.contains('selected')))

const idx = await selectedIndex()
check('new cell is selected', idx >= 0)
const wasLast = idx === (await page.locator('.cell').count()) - 1
const target = page.locator('.cell').nth(idx)
await target.locator('.cm-content').click()
await page.keyboard.type('print(6 * 7)')
await page.keyboard.press('Shift+Enter')
await page.waitForTimeout(2500)
const outText = (await target.locator('.outputs').innerText().catch(() => '')).trim()
check('run cell shows output', outText.includes('42'), outText.slice(0, 40))
check('shift+enter moves to the next cell', (await selectedIndex()) === idx + 1)
if (wasLast) {
  check('running the last cell appends one', (await page.locator('.cell').count()) === before + 2)
  await page.locator('.cell').last().hover()
  await page.locator('.cell').last().locator('.cell-actions button.rm').click()
  await page.waitForTimeout(400)
}

// language switcher and per-cell delete
await target.locator('.lang').selectOption('markdown')
await page.waitForTimeout(600)
check('switch cell to markdown', (await target.locator('.md-edit, .md-view').count()) > 0)

const beforeDel = await page.locator('.cell').count()
await target.hover()
await target.locator('.cell-actions button.rm').click()
await page.waitForTimeout(500)
check('delete cell', (await page.locator('.cell').count()) === beforeDel - 1)

// hover insert between cells
const beforeGap = await page.locator('.cell').count()
await page.locator('.cell-gap').first().hover()
await page.locator('.cell-gap').first().locator('button:text-is("+ Code")').click()
await page.waitForTimeout(500)
check('insert between cells', (await page.locator('.cell').count()) === beforeGap + 1)
await page.locator('.cell').first().hover()
await page.locator('.cell').first().locator('.cell-actions button.rm').click()
await page.waitForTimeout(400)

// sidebar: import an .ipynb, then delete it again
// keep the source outside notebooks/ so it isn't listed before we import it
const scratch = join(tmpdir(), 'ui-import-probe.ipynb')
writeFileSync(
  scratch,
  JSON.stringify({
    cells: [{ cell_type: 'markdown', metadata: {}, source: '# ui import probe' }],
    metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
    nbformat: 4,
    nbformat_minor: 5,
  })
)
const filesBefore = await page.locator('.side .file').count()
await page.setInputFiles('.side input[type=file]', scratch)
await page.waitForTimeout(900)
check('import adds a file to the sidebar', (await page.locator('.side .file').count()) === filesBefore + 1)
check('imported notebook is opened', (await page.locator('.tab input').inputValue()) === 'ui-import-probe')
check(
  'imported content is rendered',
  (await page.locator('.notebook').innerText()).includes('ui import probe')
)

page.once('dialog', (d) => d.accept())
const row = page.locator('.side .file', { hasText: 'ui-import-probe.ipynb' })
await row.hover()
await row.locator('.file-rm').click()
await page
  .waitForFunction(
    (n) => ![...document.querySelectorAll('.side .file-name')].some((el) => el.textContent.trim() === n),
    'ui-import-probe.ipynb',
    { timeout: 15000 }
  )
  .catch(() => {})
const namesAfter = await page.locator('.side .file-name').allInnerTexts()
check('delete removes it from the sidebar', namesAfter.length === filesBefore, `${namesAfter.length} rows`)
check('deleted file is gone from disk', !existsSync(join(root, 'notebooks', 'ui-import-probe.ipynb')))
check('app switched to another notebook', (await page.locator('.notebook').count()) === 1)

// each notebook has its own kernel, and the status bar follows the one on screen
const kernelText = () => page.locator('.toolbar .kernel').innerText()
const tabValue = () => page.locator('.tabbar .tab input').inputValue()
const newNotebook = async () => {
  const was = await tabValue()
  await page.click('button[title="New notebook"]')
  await page.waitForFunction((n) => document.querySelector('.tabbar .tab input')?.value !== n, was, {
    timeout: 30000,
  })
  return tabValue()
}
const busyNb = await newNotebook()
await page.locator('.cell .cm-content').first().click()
await page.keyboard.type('import time\ntime.sleep(20)')
await page.keyboard.press('Meta+Enter')
await page
  .waitForFunction(() => /Busy/.test(document.querySelector('.toolbar .kernel')?.innerText || ''), null, {
    timeout: 30000,
  })
  .catch(() => {})
check('running a cell shows Busy', /Busy/.test(await kernelText()), await kernelText())

const idleNb = await newNotebook()
await page.waitForTimeout(2000)
check('another notebook has its own idle kernel', !/Busy/.test(await kernelText()), await kernelText())
const probe = page.locator('.cell .cm-content').first()
await probe.click()
await page.keyboard.type('print(6 * 7)')
await page.keyboard.press('Meta+Enter')
await page.waitForFunction(() => /42/.test(document.querySelector('.notebook')?.innerText || ''), null, {
  timeout: 30000,
})
check('it runs while the other one is still busy', true, idleNb)

await page.locator('.file-name').filter({ hasText: `${busyNb}.ipynb` }).first().click()
await page.waitForFunction((n) => document.querySelector('.tabbar .tab input')?.value === n, busyNb, {
  timeout: 30000,
})
await page.waitForTimeout(1200)
check('switching back shows that kernel is still busy', /Busy/.test(await kernelText()), await kernelText())

for (const name of [busyNb, idleNb]) {
  page.once('dialog', (d) => d.accept())
  const doomed = page.locator('.side .file', { hasText: `${name}.ipynb` })
  await doomed.hover()
  await doomed.locator('.file-rm').click()
  await page
    .waitForFunction(
      (n) => ![...document.querySelectorAll('.side .file-name')].some((el) => el.textContent.trim() === n),
      `${name}.ipynb`,
      { timeout: 20000 }
    )
    .catch(() => {})
}

// deleting can be cancelled
page.once('dialog', (d) => d.dismiss())
const keep = page.locator('.side .file').first()
const keepName = (await keep.locator('.file-name').innerText()).trim()
await keep.hover()
await keep.locator('.file-rm').click()
await page.waitForTimeout(600)
check('cancelling the confirm keeps the file', (await page.locator('.side .file').count()) === filesBefore, keepName)

// config page is reachable and notebook page never shows the key
await page.click('.statusbar a:has-text("Model")')
await page.waitForTimeout(300)
check('model config page opens', (await page.locator('.config-card').count()) === 1)
await page.click('.config-card .back')
await page.waitForTimeout(400)
check('back to notebook', (await page.locator('.notebook').count()) === 1)
const nbText = await page.locator('.shell').innerText()
check('no api key on notebook page', !/api[\s_-]*key/i.test(nbText))

await page.screenshot({ path: '/tmp/ui_final.png', fullPage: false })
await browser.close()

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
if (failed.length) process.exit(1)
