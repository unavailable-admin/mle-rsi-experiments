"""Opt-in real Chromium smoke: MLE_BROWSER_SMOKE=1 pytest this file."""
import os
import threading
import pytest
from mle_rsi.controller import initialize, run
from mle_rsi.web import make_server
from mle_rsi.io import read

pytestmark = pytest.mark.skipif(os.environ.get('MLE_BROWSER_SMOKE') != '1', reason='opt-in Chromium smoke')


@pytest.mark.asyncio
async def test_browser_workspace(tmp_path, spec):
    from playwright.async_api import async_playwright, expect
    root = initialize(tmp_path / 'run', spec)
    await run(root)
    server = make_server(root, port=0, token='browser-smoke-token')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.locator('#token').fill('browser-smoke-token')
            await page.locator('#login-form button').click()
            await page.locator('#workspace').wait_for(state='visible')
            await page.get_by_role('button', name='候选树', exact=True).last.click()
            await page.locator('#tree-nodes .node.valid').first.click()
            await expect(page.locator('#candidate-source')).not_to_be_empty()
            await page.locator('[data-tab="resources"]').click()
            await expect(page.locator('#resource-cards .card')).to_have_count(4)
            await page.locator('[data-tab="budget"]').click()
            form = page.locator('#budget-form')
            await form.locator('[name="id"]').fill('browser-extra')
            await form.locator('[name="amount"]').fill('1')
            await form.locator('[name="reason"]').fill('browser smoke')
            await form.locator('button').click()
            await expect(page.locator('#allowances')).to_contain_text('browser-extra')
            await page.locator('[data-tab="operations"]').click()
            await page.locator('#refresh-form').wait_for(state='visible')
            await page.locator('[data-action="stop"]').click()
            await expect(page.locator('#message')).to_contain_text('stop')
            assert read(root / 'control.json')['action'] == 'stop'
            assert not errors
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
