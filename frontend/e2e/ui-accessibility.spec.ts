import { expect, test } from '@playwright/test'

// Supported desktop browser/layout/accessibility checks from the release
// ticket: Simplified-Chinese desktop support at >= 1366 px, keyboard
// reachability, labelled inputs, and non-colour-only status presentation.

test.use({ viewport: { width: 1366, height: 768 } })

test('简体中文桌面布局：语言属性、标题与无横向溢出（1366px）', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
  await expect(page).toHaveTitle(/智能信贷风控合规助手/)
  await expect(page.getByLabel('用户名')).toBeVisible()
  await expect(page.getByLabel('密码')).toBeVisible()

  // no horizontal overflow at the minimum supported desktop width
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
})

test('宽屏桌面布局无横向溢出（1920px）', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.goto('/login')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
})

test('键盘可达：Tab 聚焦登录输入框，回车提交登录', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await page.goto('/login')
  const usernameInput = page.getByLabel('用户名')
  // advance until the username input receives focus (no mouse interaction)
  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab')
    if (await usernameInput.evaluate((el) => el === document.activeElement)) break
  }
  await expect(usernameInput).toBeFocused()
  await page.keyboard.type(username)
  await page.keyboard.press('Tab')
  const passwordInput = page.getByLabel('密码')
  await expect(passwordInput).toBeFocused()
  await page.keyboard.type(password)
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/applications$/)
})

test('非纯颜色状态：生命周期状态与标签以文本呈现', async ({ page }) => {
  const username = process.env.E2E_USERNAME
  const password = process.env.E2E_PASSWORD
  if (!username || !password) throw new Error('E2E_USERNAME and E2E_PASSWORD are required')

  await page.goto('/login')
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/applications$/)

  const borrowerName = `可达性示例-${Date.now()}`
  await page.getByLabel('主借款人名称').fill(borrowerName)
  await page.getByLabel('产品').fill('经营贷')
  await page.getByRole('button', { name: '创建' }).click()
  await page.getByRole('link', { name: borrowerName }).click()

  // lifecycle state is conveyed as text, not colour alone
  await expect(page.getByText('草稿', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('待复核', { exact: true })).toHaveCount(0)
})
