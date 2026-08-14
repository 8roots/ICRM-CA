import { expect, test } from '@playwright/test'

async function login(page: import('@playwright/test').Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/applications$/)
}

async function createApplication(page: import('@playwright/test').Page, borrowerName: string) {
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
}

/** Enter one manual confirmed value through the candidate review UI. */
async function confirmManual(
  page: import('@playwright/test').Page,
  fieldLabel: string,
  value: string,
  reason: string,
) {
  await page.getByRole('button', { name: '人工录入确认值' }).click()
  const combobox = page.getByRole('combobox', { name: '选择字段' })
  await expect(combobox).toBeVisible()
  await combobox.click({ force: true })
  await page.getByRole('option', { name: fieldLabel, exact: true }).click()
  await page.getByRole('textbox', { name: '确认值' }).fill(value)
  await page.getByRole('textbox', { name: '人工录入理由' }).fill(reason)
  await page.getByRole('button', { name: '提交确认' }).click()
  await expect(page.getByRole('button', { name: '人工录入确认值' })).toBeVisible()
}

test('审批人员确认规则上下文与关键输入并生成正式红线报告', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await login(page, username, password)
  const borrowerName = `红线示例企业-${Date.now()}`
  await createApplication(page, borrowerName)

  // 1. open the redline workbench; without a confirmed rule context the
  // selection is explicitly indeterminate
  await page.getByRole('link', { name: '红线评估与正式报告' }).click()
  await expect(page).toHaveURL(/\/redline$/)
  await expect(page.getByText('尚未确认规则上下文，无法确定适用规则')).toBeVisible()

  // 2. confirm the rule context; the demo hard rule is uniquely selected and
  // the missing critical inputs are listed (no “未触发硬规则” without them)
  await page.getByRole('textbox', { name: '规则上下文' }).fill('全国')
  await page.getByRole('button', { name: '确认规则上下文' }).click()
  await expect(page.getByText('DEMO-EFFECTIVE-COST-36')).toBeVisible()
  await expect(page.getByText('loan_amount', { exact: true })).toBeVisible()
  await expect(page.getByText('loan_term', { exact: true })).toBeVisible()
  await expect(page.getByText('interest_rate', { exact: true })).toBeVisible()
  await expect(page.getByText('repayment_method', { exact: true })).toBeVisible()
  await expect(page.getByText('loan_fees', { exact: true })).toBeVisible()
  await expect(page.getByText('overdue_interest_rate', { exact: true })).toBeVisible()

  // 3. confirm the six critical proposed-loan inputs by hand (no materials
  // uploaded, so no candidates exist)
  await page.getByRole('link', { name: '前往字段候选复核与人工确认' }).click()
  await expect(page).toHaveURL(/\/candidates$/)
  await confirmManual(page, '贷款金额', '100000', '演示录入：贷款金额')
  await confirmManual(page, '贷款期限', '12', '演示录入：期限 12 个月')
  await confirmManual(page, '利率', '12%', '演示录入：年利率 12%')
  await confirmManual(page, '还款方式', '等额本息', '演示录入：等额本息')
  await confirmManual(page, '必要费用', '0', '演示录入：无必要费用')
  await confirmManual(page, '罚息利率', '18%', '演示录入：罚息利率 18%')
  await expect(page.getByText('贷款金额', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('罚息利率', { exact: true }).first()).toBeVisible()

  // 4. back to the redline workbench: inputs are ready and the formal run
  // produces a not-triggered snapshot with formula steps and the printable
  // report link
  await page.getByRole('link', { name: '返回申请列表' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
  await page.getByRole('link', { name: '红线评估与正式报告' }).click()
  await expect(page.getByText('关键输入（确认值）')).toBeVisible()
  await page.getByRole('button', { name: '执行正式红线评估' }).click()
  await expect(page.getByText('未触发硬规则')).toBeVisible()
  await expect(page.getByText('DEMO-EFFECTIVE-COST-36')).toBeVisible()
  await expect(page.getByText('正常履约综合年化成本', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('司法风险参考线（仅提示，不构成硬规则结论）')).toBeVisible()
  await expect(page.getByRole('link', { name: '打印版 HTML' }).first()).toBeVisible()
})
