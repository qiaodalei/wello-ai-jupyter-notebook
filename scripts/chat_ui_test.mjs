// Chat panel behaviour that needs a real model round: folded thinking,
// per-notebook context, and clearing the context.
// Run: node scripts/chat_ui_test.mjs   (dev.sh must be up, model configured)
import { existsSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
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

const browser = await chromium.launch({ executablePath: chromePath() })
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } })
page.on('dialog', (d) => d.accept())
await page.goto(URL)
await page.waitForSelector('.chat textarea')

const idle = () =>
  page.waitForFunction(
    () => !document.querySelector('.chat-head .phase') && !document.querySelector('.file-busy'),
    null,
    { timeout: 300000 }
  )
const tabName = () => page.locator('.tabbar .tab input').inputValue()
const openedOther = (was) => page.waitForFunction((n) => {
  const el = document.querySelector('.tabbar .tab input')
  return el && el.value !== n
}, was, { timeout: 30000 })

// Start from a clean slate: no leftover run from an earlier suite.
await idle()

// A fresh notebook, so the context starts empty.
const before = await tabName()
await page.click('button[title="New notebook"]')
await openedOther(before)
const clearBtn = page.locator('.chat-head .tb.icon').first()
check('没有对话时清空按钮是禁用的', await clearBtn.isDisabled())

// Does the panel show text belonging to a different notebook?
const panelText = () => page.locator('.messages').innerText()

// One real round, small enough to finish quickly.
const firstAsk = '算一下 2**64，并说明它有多少位'
await page.fill('.chat textarea', firstAsk)
await page.keyboard.press('Enter')

await page.waitForSelector('.msg .fold', { timeout: 60000 })
check('思考过程出现了折叠块', (await page.locator('.msg .fold').count()) >= 1)
const firstFold = page.locator('.msg .fold').first()
check('思考默认是折叠的', !(await firstFold.evaluate((el) => el.open)))
const summary = await firstFold.locator('summary').innerText()
check('折叠标题带字数', /思考过程 · \d+ 字/.test(summary), summary)

const hiddenBefore = await firstFold.locator('.reasoning').isVisible()
await firstFold.locator('summary').click()
check('点开之后能看到思考内容', !hiddenBefore && (await firstFold.locator('.reasoning').isVisible()))
// The trace must be the whole round, not the truncated live tail: what the
// summary claims and what the body renders have to agree exactly.
const declared = Number(summary.match(/(\d+) 字/)?.[1] ?? -1)
const rendered = await firstFold.locator('.reasoning').evaluate((el) => el.textContent.length)
check('折叠里的思考没被截断', declared === rendered && rendered > 0, `声明 ${declared} / 渲染 ${rendered}`)

// Wait for the run to settle, then confirm the trace is still there.
await idle()
const foldCount = await page.locator('.msg .fold').count()
check('跑完之后思考没有消失', foldCount >= 1, `${foldCount} 块`)
const answered = await page.locator('.msg.assistant .md').count()
check('正式回答用 markdown 渲染', answered >= 1, `${answered} 条`)

const nbName = await page.locator('.tabbar .tab input').inputValue()
const msgsBefore = await page.locator('.msg').count()
check('面板里有对话', msgsBefore > 0, `${msgsBefore} 条`)

// Switching notebooks must swap the conversation, not carry it over.
const others = page.locator('.file-name').filter({ hasNotText: `${nbName}.ipynb` })
if (await others.count()) {
  const otherName = (await others.first().innerText()).trim()
  await others.first().click()
  await openedOther(nbName)
  await page.waitForTimeout(600)
  // The other notebook may have its own history; it must not have ours.
  check('切走后看不到这本的提问', !(await panelText()).includes(firstAsk), otherName)
  await page.locator('.file-name').filter({ hasText: `${nbName}.ipynb` }).first().click()
  await page.waitForFunction((n) => {
    const el = document.querySelector('.tabbar .tab input')
    return el && el.value === n
  }, nbName, { timeout: 30000 })
  await page.waitForTimeout(600)
  const backCount = await page.locator('.msg').count()
  check('切回来对话还在', backCount > 0, `${backCount} 条`)
} else {
  check('切到别的 notebook 后对话清空', false, '侧栏里没有第二个 notebook')
}

// Clearing wipes this notebook's context.
await page.waitForSelector('.chat-head .tb.icon:not([disabled])')
check('有对话时清空按钮可用', await clearBtn.isEnabled())
await clearBtn.click()
await page.waitForFunction(() => !document.querySelector('.msg'), null, { timeout: 15000 }).catch(() => {})
check('清空后面板为空', (await page.locator('.msg').count()) === 0)
const cells = await page.locator('.cell').count()
check('清空上下文没动单元格', cells > 0, `${cells} 个`)

await page.reload()
await page.waitForSelector('.chat textarea')
await page.waitForTimeout(1200)
check('刷新后依然是空的', (await page.locator('.msg').count()) === 0)

// Mid-run: switching away is free, and the other notebook stays clean and usable.
// The sleep keeps the run alive long enough to be observed reliably.
const runningName = await tabName()
const midRunAsk = '写一个单元格，用 time.sleep(25) 等一会再打印 done，然后运行它'
await page.fill('.chat textarea', midRunAsk)
await page.keyboard.press('Enter')
await page.waitForSelector('.msg .fold', { timeout: 60000 })
check('本本开始有输出', (await page.locator('.msg').count()) > 0)
await page.waitForSelector('.file-busy', { timeout: 60000 })
check('侧栏标出正在跑的那本', (await page.locator('.file-busy').count()) === 1)

let asked = false
page.removeAllListeners('dialog')
page.on('dialog', (d) => {
  asked = true
  d.accept()
})
const target = page.locator('.file-name').filter({ hasNotText: `${runningName}.ipynb` }).first()
const targetName = (await target.innerText()).trim()
await target.click()
await openedOther(runningName)
check('运行中切换不再拦着问', !asked)
check('立刻就切过去了', (await tabName()) !== runningName, targetName)

const after = await panelText()
check('另一本看不到那边的提问', !after.includes(midRunAsk))
check('另一本没有流式残留', !after.includes('思考中'))
check('侧栏仍标着那本在跑', (await page.locator('.file-busy').count()) === 1)

// The notebook on screen works normally while the other one runs.
await page.locator('.toolbar .tb', { hasText: '+ Code' }).first().click()
const cellCount = await page.locator('.cell').count()
check('运行中另一本还能加单元格', cellCount > 0, `${cellCount} 个`)

// And it can start its own run: two notebooks working at once.
await page.fill('.chat textarea', '算一下 3 的 5 次方')
await page.keyboard.press('Enter')
await page.waitForFunction(() => document.querySelectorAll('.file-busy').length === 2, null, {
  timeout: 60000,
})
check('两本可以同时跑', (await page.locator('.file-busy').count()) === 2)

// Switching back finds the first conversation intact and still going.
await page.locator('.file-name').filter({ hasText: `${runningName}.ipynb` }).first().click()
await page.waitForFunction((n) => {
  const el = document.querySelector('.tabbar .tab input')
  return el && el.value === n
}, runningName, { timeout: 30000 })
const backText = await panelText()
check('切回来还是自己的对话', backText.includes(midRunAsk), `${backText.length} 字`)

await idle()
check('两边都跑完了', (await page.locator('.file-busy').count()) === 0)

await browser.close()
const passed = results.filter((r) => r.ok).length
console.log(`\n${passed}/${results.length} passed`)
process.exit(passed === results.length ? 0 : 1)
