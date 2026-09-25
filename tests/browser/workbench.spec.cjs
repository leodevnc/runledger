const { test, expect } = require('@playwright/test');

test('approve, lose worker after effect, recover once, and compare replay', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: '+ New investigation' }).click();
  await expect(page.locator('#run-state')).toHaveText('queued');
  await page.getByRole('button', { name: 'Advance one step →' }).click();
  await expect(page.locator('#checkpoint-count')).toHaveText('1 / 3');
  await page.getByRole('button', { name: 'Advance one step →' }).click();
  await expect(page.locator('#checkpoint-count')).toHaveText('2 / 3');
  await page.getByRole('button', { name: 'Advance one step →' }).click();
  await expect(page.getByRole('button', { name: 'Approve report' })).toBeVisible();
  await expect(page.locator('#receipt-count')).toHaveText('0');
  await page.getByRole('button', { name: 'Approve report' }).click();
  await page.getByRole('button', { name: 'Crash after report write' }).click();
  await expect(page.locator('#notification')).toContainText('Worker lost after report commit');
  await expect(page.locator('#receipt-count')).toHaveText('1');
  await expect(page.locator('#checkpoint-count')).toHaveText('2 / 3');
  await expect(page.locator('#steps')).toContainText('lease expired', {timeout: 8000});
  await page.getByRole('button', { name: 'Advance one step →' }).click();
  await expect(page.locator('#run-state')).toHaveText('succeeded');
  await expect(page.locator('#receipt-count')).toHaveText('1');
  await expect(page.locator('#attempt-count')).toHaveText('4');
  await expect(page.locator('#events')).toContainText('lease_reclaimed');
  await page.getByRole('button', { name: 'Changed policy ↗' }).click();
  await expect(page.locator('#replay-result')).toContainText('1 divergences · 0 side effects executed');
  await expect(page.locator('#replay-result')).toContainText('monitor recovery');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path:'docs/workbench.png', fullPage:true});
  await page.setViewportSize({width:390, height:844});
  await expect(page.locator('#run-state')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({path:'docs/workbench-mobile.png', fullPage:true});
  expect(errors).toEqual([]);
});

test('reject an action without publishing a report', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ New investigation' }).click();
  for (const expected of ['1 / 3', '2 / 3']) {
    await page.getByRole('button', { name: 'Advance one step →' }).click();
    await expect(page.locator('#checkpoint-count')).toHaveText(expected);
  }
  await page.getByRole('button', { name: 'Advance one step →' }).click();
  await page.getByRole('button', { name: 'Reject', exact:true }).click();
  await expect(page.locator('#run-state')).toHaveText('failed');
  await expect(page.locator('#receipt-count')).toHaveText('0');
});
