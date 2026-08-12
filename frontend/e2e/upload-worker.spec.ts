import { expect, test } from '@playwright/test'

test('申请负责人上传材料并观察 worker 完成验证', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/applications$/)

  const borrowerName = `端到端示例企业-${Date.now()}`
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()

  await page.locator('input[type="file"]').setInputFiles({
    name: 'worker.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.7\npublic sample'),
  })
  await expect(page.getByText('等待处理')).toBeVisible()
  await expect(page.getByText('处理成功')).toBeVisible({ timeout: 15_000 })
})
