import { expect, test } from '@playwright/test'

// Synthetic individual (个人) material with classifier + extraction keywords.
function individualMaterial(name: string): { name: string; mimeType: string; buffer: Buffer } {
  return {
    name: '个人借款申请.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(
      [
        '# 个人贷款申请',
        '',
        '借款申请书 贷款申请 授信申请',
        '',
        `姓名：${name}`,
        '身份证号：330102199001011234',
        '贷款金额：30万元',
        '贷款期限：36个月',
        '年利率：4.2%',
        '还款方式：等额本息',
        '必要费用：0',
      ].join('\n'),
    ),
  }
}

async function login(page: import('@playwright/test').Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/applications$/)
}

async function adoptCandidate(page: import('@playwright/test').Page, fieldLabel: string) {
  await page.getByRole('button', { name: `采用候选-${fieldLabel}` }).first().click()
  await page.getByRole('button', { name: '提交确认' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

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
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

test('个人演示流程：登录 → 上传 → 候选确认 → 完备性 → 红线 → 辅助审查完成', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await login(page, username, password)
  const borrowerName = `个人演示-${Date.now()}`
  await page.getByLabel('主借款人类型').selectOption('individual')
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()

  // upload and wait for machine processing
  await page.locator('input[type="file"]').setInputFiles(individualMaterial(borrowerName))
  await expect(page.getByText('处理成功')).toBeVisible({ timeout: 60_000 })

  // candidate review: adopt extracted candidates; 罚息利率 is manual-only
  await page.getByRole('link', { name: '字段候选复核与人工确认' }).click()
  await expect(page).toHaveURL(/\/candidates$/)
  await adoptCandidate(page, '贷款金额')
  await adoptCandidate(page, '贷款期限')
  await adoptCandidate(page, '利率')
  await adoptCandidate(page, '还款方式')
  await adoptCandidate(page, '必要费用')
  await confirmManual(page, '罚息利率', '18%', '演示录入：罚息利率 18%')
  await expect(page.getByText('贷款金额', { exact: true }).first()).toBeVisible()

  // completeness against the individual demo template
  await page.getByRole('link', { name: '返回申请列表' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
  await page.getByRole('link', { name: '材料完备性与正式报告' }).click()
  await expect(page).toHaveURL(/\/completeness$/)
  await expect(page.getByText('DEMO-INDIVIDUAL-OPERATING')).toBeVisible()
  await page.getByRole('combobox', { name: '选择类别-个人借款申请.md' }).focus()
  await page.keyboard.press('ArrowDown')
  // clicking the option through the pointer is unreliable here (el-select
  // placeholder overlay + dropdown re-rendering), so confirm via its handler
  await page
    .locator('.el-select-dropdown__item', { hasText: '贷款申请' })
    .first()
    .evaluate((el) => el.click())
  await page.getByRole('button', { name: '确认分类-个人借款申请.md' }).click()
  await page.getByRole('button', { name: '确认映射-个人借款申请.md-借款申请书' }).click()
  await page.getByRole('button', { name: '执行正式完备性检查' }).click()
  await expect(page.getByText('有效')).toBeVisible({ timeout: 30_000 })

  // redline: confirm rule context and run the formal evaluation
  await page.getByRole('link', { name: '返回申请列表' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
  await page.getByRole('link', { name: '红线评估与正式报告' }).click()
  await expect(page).toHaveURL(/\/redline$/)
  await page.getByRole('textbox', { name: '规则上下文' }).fill('全国')
  await page.getByRole('button', { name: '确认规则上下文' }).click()
  await expect(page.getByText('关键输入（确认值）')).toBeVisible()
  await page.getByRole('button', { name: '执行正式红线评估' }).click()
  await expect(page.getByText('未触发硬规则')).toBeVisible()
  await expect(page.getByText('DEMO-EFFECTIVE-COST-36')).toBeVisible()

  // auxiliary review completion
  await page.getByRole('link', { name: '返回申请列表' }).click()
  await page.getByRole('link', { name: borrowerName }).click()
  await page.getByRole('button', { name: '标记辅助审查完成' }).click()
  await page.getByRole('button', { name: '确认完成' }).click()
  await expect(page.getByText('已标记辅助审查完成')).toBeVisible()
  // lifecycle state is rendered as text in the application descriptions
  await expect(
    page.locator('.el-descriptions').getByText('辅助审查完成', { exact: true }),
  ).toBeVisible()
})
