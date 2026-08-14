import { expect, test } from '@playwright/test'

// Minimal valid text PDF generated with PyMuPDF: "Public synthetic statement page".
const PUBLIC_PDF = Buffer.from(
  'JVBERi0xLjcKJcK1wrYKJSBXcml0dGVuIGJ5IE11UERGIDEuMjguMgoKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFIvSW5mbzw8L1Byb2R1Y2VyKE11UERGIDEuMjguMik+Pj4+CmVuZG9iagoKMiAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1s0IDAgUl0+PgplbmRvYmoKCjMgMCBvYmoKPDwvRm9udDw8L2hlbHYgNSAwIFI+Pj4+CmVuZG9iagoKNCAwIG9iago8PC9UeXBlL1BhZ2UvTWVkaWFCb3hbMCAwIDU5NSA4NDJdL1JvdGF0ZSAwL1Jlc291cmNlcyAzIDAgUi9QYXJlbnQgMiAwIFIvQ29udGVudHNbNiAwIFJdPj4KZW5kb2JqCgo1IDAgb2JqCjw8L1R5cGUvRm9udC9TdWJ0eXBlL1R5cGUxL0Jhc2VGb250L0hlbHZldGljYS9FbmNvZGluZy9XaW5BbnNpRW5jb2Rpbmc+PgplbmRvYmoKCjYgMCBvYmoKPDwvTGVuZ3RoIDEwMS9GaWx0ZXIvRmxhdGVEZWNvZGU+PgpzdHJlYW0KeNo1iaEOg1AMRX2/on+wtrzeQrJMkGBwJHUEBSwTm5jZ96+G3JwrzqEvjUnKUlMO42jG+aHb63z/WMH55PXuEg7DjgGdSXQx4IyGHl5/uQYtHEdRtZxAEfDHljNNSQv9ATUfGD8KZW5kc3RyZWFtCmVuZG9iagoKeHJlZgowIDcKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDQyIDAwMDAwIG4gCjAwMDAwMDAxMjAgMDAwMDAgbiAKMDAwMDAwMDE3MiAwMDAwMCBuIAowMDAwMDAwMjEzIDAwMDAwIG4gCjAwMDAwMDAzMjAgMDAwMDAgbiAKMDAwMDAwMDQwOSAwMDAwMCBuIAoKdHJhaWxlcgo8PC9TaXplIDcvUm9vdCAxIDAgUi9JRFs8NzgwMjI0QzJBMzdBNTQyM0MzQTAwNDM2QzJBMkMyOEQ+PDBENDM2MkE3N0M5QjYzMkYwNDdGMzRBNkNENEIwNTEwPl0+PgpzdGFydHhyZWYKNTc5CiUlRU9GCg==',
  'base64',
)

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
    buffer: PUBLIC_PDF,
  })
  await expect(page.getByText('等待处理')).toBeVisible()
  await expect(page.getByText('处理成功')).toBeVisible({ timeout: 60_000 })
})

test('负责人查看证据预览、高亮原页区域并人工确认与重跑', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/applications$/)

  const borrowerName = `证据预览企业-${Date.now()}`
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()

  await page.locator('input[type="file"]').setInputFiles({
    name: 'evidence.pdf',
    mimeType: 'application/pdf',
    buffer: PUBLIC_PDF,
  })
  await expect(page.getByText('处理成功')).toBeVisible({ timeout: 60_000 })

  await page.getByRole('link', { name: '证据预览-evidence.pdf' }).click()
  await expect(page).toHaveURL(/\/documents\/.+\/evidence$/)
  await expect(page.getByText('输出版本')).toBeVisible()
  await expect(page.getByText('v1')).toBeVisible()
  await expect(page.getByText('Public synthetic statement page')).toBeVisible()
  await expect(page.getByText('印章候选（未经人工确认）')).toBeVisible()
  await expect(page.getByText('不判断印章真实性、归属或法律效力')).toBeVisible()
  await expect(page.getByRole('img', { name: '第 1 页原页' })).toBeVisible()

  // select the text block and confirm it highlights on the original page
  await page.getByRole('button', { name: '定位文字块-Public synthetic statement page' }).click()
  const highlighted = page.locator(
    '[aria-label="文字块-Public synthetic statement page"].selected',
  )
  await expect(highlighted).toHaveCount(1)

  // manual signature confirmation
  await page.getByLabel('签字确认理由').fill('审批人员查看原页后确认未见签字')
  await page.getByRole('button', { name: '提交签字确认' }).click()
  await expect(page.getByText('审批人员查看原页后确认未见签字')).toBeVisible()

  // explicit parser rerun with reason produces a new output version
  await page.getByLabel('解析重跑原因').fill('使用固定新模型重新解析')
  await page.getByRole('button', { name: '重跑解析' }).click()
  await expect(page.getByText('最近一次重跑原因：使用固定新模型重新解析')).toBeVisible()
  await expect(page.getByText('v2')).toBeVisible({ timeout: 60_000 })
})
