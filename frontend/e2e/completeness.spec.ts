import { expect, test } from '@playwright/test'

// Markdown material with deterministic Chinese keywords for the content
// classifier; parsed locally without OCR.
function loanApplicationMaterial(name: string): { name: string; mimeType: string; buffer: Buffer } {
  return {
    name: '申请材料.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(
      `# 借款申请书\n\n借款申请书 贷款申请 授信申请\n\n企业名称：${name}\n贷款金额：800万元\n`,
    ),
  }
}

async function login(
  page: import('@playwright/test').Page,
  username: string,
  password: string,
  expected: RegExp = /\/applications$/,
) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(expected)
}

async function createApplication(page: import('@playwright/test').Page, borrowerName: string) {
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
}

test('审批人员确认材料分类与映射并生成正式完备性报告', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await login(page, username, password)
  const borrowerName = `完备性示例企业-${Date.now()}`
  await createApplication(page, borrowerName)

  await page.locator('input[type="file"]').setInputFiles(loanApplicationMaterial(borrowerName))
  await expect(page.getByText('处理成功')).toBeVisible({ timeout: 60_000 })

  await page.getByRole('link', { name: '材料完备性与正式报告' }).click()
  await expect(page).toHaveURL(/\/completeness$/)
  await expect(page.getByText('DEMO-CORP-OPERATING')).toBeVisible()
  await expect(page.getByText('借款申请书')).toBeVisible()

  // content classification produces a loan_application candidate
  await expect(page.getByText('贷款申请（1.00）')).toBeVisible()
  await expect(page.getByText('印章未确认')).toBeVisible()

  // confirm the classification for the uploaded material (combobox role
  // avoids the dropdown listbox which shares the aria-label; ArrowDown opens
  // el-select reliably, unlike pointer clicks through its placeholder overlay)
  await page.getByRole('combobox', { name: '选择类别-申请材料.md' }).focus()
  await page.keyboard.press('ArrowDown')
  // clicking the option through the pointer is unreliable here (el-select
  // placeholder overlay + dropdown re-rendering), so confirm via its handler
  await page
    .locator('.el-select-dropdown__item', { hasText: '贷款申请' })
    .first()
    .evaluate((el) => el.click())
  await page.getByRole('button', { name: '确认分类-申请材料.md' }).click()
  await expect(page.getByLabel('材料分类').getByText('贷款申请', { exact: true })).toBeVisible()

  // confirm the suggested mapping for the loan application item
  await page.getByRole('button', { name: '确认映射-申请材料.md-借款申请书' }).click()
  await expect(page.getByLabel('材料分类').getByText('申请材料.md', { exact: true })).toBeVisible()

  // the seal requirement keeps the item pending until confirmed; the formal
  // run still succeeds and produces an immutable report
  await page.getByRole('button', { name: '执行正式完备性检查' }).click()
  await expect(page.getByText('有效')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('打印版 HTML')).toBeVisible()

  const [printable] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByText('打印版 HTML', { exact: true }).first().click(),
  ])
  await printable.waitForLoadState()
  await expect(printable.getByText('仅供审批辅助，需人工复核')).toBeVisible()
  await expect(printable.getByText('DEMO-CORP-OPERATING')).toBeVisible()
})

test('管理员查看并发布完备性模板草稿', async ({ page }) => {
  const username = process.env.E2E_ADMIN_USERNAME
  const password = process.env.E2E_ADMIN_PASSWORD
  test.skip(!username || !password, 'E2E_ADMIN_USERNAME and E2E_ADMIN_PASSWORD are required')

  await login(page, username!, password!, /\/admin\/users$/)
  await page.getByRole('link', { name: '模板管理' }).click()
  await expect(page).toHaveURL(/\/admin\/templates$/)
  await expect(page.getByText('DEMO-CORP-OPERATING')).toBeVisible()
  await expect(page.getByText('DEMO-INDIVIDUAL-OPERATING')).toBeVisible()

  const code = `E2E-${Date.now()}`
  await page.getByRole('button', { name: '新建模板' }).click()
  await page.getByLabel('模板编码').fill(code)
  await page.getByLabel('模板名称').fill('端到端测试模板')
  // a unique product avoids colliding with the published demo template
  // (DEMO-CORP-OPERATING is already published for 经营贷 × 企业)
  await page.getByLabel('模板产品').fill(`测试产品-${Date.now()}`)
  await page.getByLabel('清单项编号-0').fill('license')
  await page.getByLabel('清单项名称-0').fill('营业执照')
  await page.getByRole('button', { name: '提交新建模板' }).click()
  await expect(page.getByText('模板已创建（草稿）')).toBeVisible()

  await page.getByRole('button', { name: `发布-${code}-v1` }).click()
  await expect(page.getByText('已发布', { exact: true }).last()).toBeVisible()
})
