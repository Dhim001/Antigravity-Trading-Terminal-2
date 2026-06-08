const puppeteer = require('puppeteer');
const fs = require('fs');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  
  console.log('Navigating to http://localhost:5174...');
  await page.goto('http://localhost:5174', { waitUntil: 'networkidle2' });
  
  // Wait for chart to render
  await new Promise(r => setTimeout(r, 2000));
  
  await page.screenshot({ path: 'screenshot1_1m.png' });
  console.log('Saved screenshot1_1m.png');
  
  // Click on '15m' timeframe
  await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('.tf-btn'));
    const tf15 = btns.find(b => b.innerText.includes('15m'));
    if (tf15) tf15.click();
  });
  
  // Wait for timeframe change
  await new Promise(r => setTimeout(r, 2000));
  
  await page.screenshot({ path: 'screenshot2_15m.png' });
  console.log('Saved screenshot2_15m.png');
  
  await browser.close();
})();
