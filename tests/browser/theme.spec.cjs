const {test,expect}=require('@playwright/test');
test('neutral workbench has keyboard access and responsive layouts',async({page})=>{
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toHaveText('Skip to content');
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
  await expect(page.locator('body')).toHaveCSS('background-color','rgb(255, 255, 255)');
  await expect(page.locator('.sidebar')).toHaveCSS('background-color','rgb(247, 247, 245)');
  for(const width of [320,768,1440]){
    await page.setViewportSize({width,height:1000});
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(page.getByRole('button',{name:'+ New investigation'})).toBeVisible();
  }
});
