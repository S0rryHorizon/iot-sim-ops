"""Real Chromium + Uvicorn + opt-in synthetic MySQL checks for the static console."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from playwright.async_api import async_playwright, expect

from test_db_integration import db_case  # reuse its isolated DB setup and cleanup


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def web_server():
    name = os.environ.get("IOT_TEST_DB_NAME")
    if os.environ.get("IOT_TEST_DB_ISOLATED") != "1" or not name:
        pytest.fail("browser tests require IOT_TEST_DB_ISOLATED=1 and IOT_TEST_DB_NAME")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({
        "DB_HOST": env.get("IOT_TEST_DB_HOST", "127.0.0.1"),
        "DB_PORT": env.get("IOT_TEST_DB_PORT", "3306"),
        "DB_USER": env.get("IOT_TEST_DB_USER", "root"),
        "DB_PASS": env.get("IOT_TEST_DB_PASS", ""),
        "DB_NAME": name,
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT / "api" / "mock-fastapi", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    origin = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail(f"Uvicorn exited with {process.returncode}")
            try:
                with urlopen(f"{origin}/alive", timeout=0.2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Uvicorn did not become ready")
        yield origin
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def login(page, origin, username="alice", password="user1pass"):
    await page.goto(f"{origin}/web/login.html")
    await page.locator("#username").fill(username)
    await page.locator("#password").fill(password)
    await page.locator("#f button").click()
    await page.wait_for_url("**/web/index.html")


async def select_card(page, iccid):
    await page.locator("#iccid").fill(iccid)
    await page.locator("#searchBtn").click()
    await expect(page.locator("#simInfo")).to_contain_text(iccid)


def test_lost_response_refresh_same_key_double_click_and_receipt(db_case, web_server):
    case = db_case
    iccid = case.make_card(seed_usage=True)

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await login(page, web_server)
                await select_card(page, iccid)
                await page.locator("#month").fill("2031-07")
                await page.locator("#month").dispatch_event("change")
                await page.locator("#pkgMb").fill("17")
                posts = []

                async def lose_after_commit(route):
                    posts.append((route.request.headers["x-transid"], route.request.post_data))
                    response = await route.fetch()
                    assert response.status == 200
                    await route.abort("failed")

                await page.route(f"**/sims/{iccid}/purchase", lose_after_commit)
                # Two clicks in the same browser turn; only the first may send POST.
                await page.evaluate("document.querySelector('#btnBuy').click(); document.querySelector('#btnBuy').click()")
                await expect(page.locator("#purchaseMsg")).to_contain_text("结果未确认")
                assert len(posts) == 1
                pending = await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')")
                browser_token = await page.evaluate("localStorage.getItem('token')")
                assert posts[0][0] in pending and browser_token not in pending and '"token"' not in pending
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (posts[0][0],)) == 1

                await page.reload()
                await expect(page.locator("#purchaseMsg")).to_contain_text("刷新页面不会自动重试")
                assert len(posts) == 1
                await page.locator("#month").fill("2031-08")
                await page.locator("#pkgMb").fill("99")
                await page.unroute(f"**/sims/{iccid}/purchase", lose_after_commit)
                replay = []

                async def capture_replay(route):
                    replay.append((route.request.headers["x-transid"], route.request.post_data))
                    await route.continue_()

                await page.route(f"**/sims/{iccid}/purchase", capture_replay)

                async def fail_list(route):
                    await route.abort("failed")

                await page.route(f"**/sims/{iccid}/purchases?**", fail_list)
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("下单成功：PO")
                assert replay == posts
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')") is None
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE transid=%s", (posts[0][0],)) == 1
                assert case.scalar("SELECT package_mb FROM sim_usage WHERE iccid=%s AND month='2031-07'", (iccid,)) == 1041

                # The receipt remains even if subsequent list/usage GETs fail.
                await page.locator("#iccid").fill(iccid)
                await page.locator("#searchBtn").click()
                await expect(page.locator("#ordersMsg")).to_contain_text("订单刷新失败")
                await expect(page.locator("#purchaseMsg")).to_contain_text("下单成功：PO")
            finally:
                await browser.close()

    asyncio.run(run())


def test_stale_search_order_usage_and_month_context(db_case, web_server):
    case = db_case
    first = case.make_card(seed_usage=True)
    second = case.make_card(seed_usage=True)

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await login(page, web_server)
                first_search_seen = asyncio.Event()
                release_search = asyncio.Event()

                async def old_search(route):
                    response = await route.fetch()
                    first_search_seen.set()
                    await release_search.wait()
                    await route.fulfill(response=response)

                await page.route(f"**/sims/search?iccid={first}", old_search)
                await page.locator("#iccid").fill(first)
                await page.locator("#searchBtn").click()
                await asyncio.wait_for(first_search_seen.wait(), 5)
                await select_card(page, second)
                async with page.expect_event(
                    "requestfinished", predicate=lambda request: f"iccid={first}" in request.url
                ) as old_search_finished:
                    release_search.set()
                await old_search_finished.value
                await page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")
                await expect(page.locator("#simInfo")).to_contain_text(second)
                await page.unroute(f"**/sims/search?iccid={first}", old_search)

                held = {"usage": asyncio.Event(), "orders": asyncio.Event()}
                release = asyncio.Event()

                async def hold_old_get(route):
                    kind = "usage" if "/usage?" in route.request.url else "orders"
                    response = await route.fetch()
                    held[kind].set()
                    await release.wait()
                    await route.fulfill(response=response)

                usage_url = f"**/sims/{second}/usage?month=2031-07"
                orders_url = f"**/sims/{second}/purchases?month=2031-07&**"
                await page.route(usage_url, hold_old_get, times=1)
                await page.route(orders_url, hold_old_get, times=1)
                await page.locator("#month").fill("2031-07")
                await page.locator("#month").dispatch_event("change")
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in held.values())), 5)

                # New month wins over two already completed but delayed old GETs.
                await page.locator("#month").fill("2031-08")
                await page.locator("#month").dispatch_event("change")
                await expect(page.locator("#orders")).to_contain_text("暂无订单")
                await expect(page.locator("#usageBox")).to_contain_text("no usage for month")
                async with page.expect_event(
                    "requestfinished", predicate=lambda request: "/usage?month=2031-07" in request.url
                ) as old_usage_finished:
                    async with page.expect_event(
                        "requestfinished", predicate=lambda request: "/purchases?month=2031-07" in request.url
                    ) as old_orders_finished:
                        release.set()
                await asyncio.gather(old_usage_finished.value, old_orders_finished.value)
                await page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")
                await expect(page.locator("#orders")).to_contain_text("暂无订单")
                assert "1024" not in await page.locator("#usageBox").inner_text()

                # A newer request for the same card/month also wins over older GETs.
                stale = asyncio.Event()
                release_stale = asyncio.Event()

                async def hold_list(route):
                    response = await route.fetch()
                    stale.set()
                    await release_stale.wait()
                    await route.fulfill(response=response)

                await page.route(orders_url, hold_list, times=1)
                await page.locator("#month").fill("2031-07")
                await page.locator("#month").dispatch_event("change")
                await asyncio.wait_for(stale.wait(), 5)
                await page.locator("#pkgMb").fill("7")
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("下单成功：PO")
                await expect(page.locator("#orders")).to_contain_text("2031-07")
                await expect(page.locator("#usageBox")).to_contain_text("1031 MB")
                async with page.expect_event(
                    "requestfinished", predicate=lambda request: "/purchases?month=2031-07" in request.url
                ) as stale_finished:
                    release_stale.set()
                await stale_finished.value
                await page.evaluate("new Promise(resolve => setTimeout(resolve, 0))")
                await expect(page.locator("#orders")).to_contain_text("2031-07")
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE iccid=%s AND month='2031-07'", (second,)) == 1
            finally:
                await browser.close()

    asyncio.run(run())


def test_pending_isolated_by_user_and_damaged_record_blocks_purchase(db_case, web_server):
    case = db_case
    iccid = case.make_card()
    bob_card = case.make_card(owner="bob")

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await login(page, web_server)
                await select_card(page, iccid)

                async def lose_response(route):
                    response = await route.fetch()
                    assert response.status == 200
                    await route.abort("failed")

                await page.route(f"**/sims/{iccid}/purchase", lose_response)
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("结果未确认")
                original = await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')")
                browser_token = await page.evaluate("localStorage.getItem('token')")
                assert original and browser_token not in original and '"token"' not in original
                await page.locator("#logoutBtn").click()
                await login(page, web_server, "bob", "user2pass")
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:bob')") is None
                assert await page.locator("#btnBuy").inner_text() == "购买"
                await select_card(page, bob_card)
                await page.locator("#logoutBtn").click()
                await login(page, web_server)
                await expect(page.locator("#purchaseMsg")).to_contain_text("待确认购买")
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')") == original

                # Unknown or corrupt pending state must fail closed instead of minting a new key.
                await page.evaluate("sessionStorage.setItem('iot-sim-ops:pending:alice', '{broken')")
                await page.reload()
                await expect(page.locator("#purchaseMsg")).to_contain_text("无法读取待确认记录")
                assert await page.locator("#btnBuy").is_disabled()
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')") == "{broken"
            finally:
                await browser.close()

    asyncio.run(run())


def test_capacity_rejection_releases_pending_and_cross_tab_login_discards_old_result(db_case, web_server):
    case = db_case
    iccid = case.make_card()
    with case.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sim_usage(iccid, month, used_mb, package_mb) VALUES(%s, '2031-07', 0, 2147483646)",
            (iccid,),
        )
        conn.commit()

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                context = await browser.new_context()
                await context.add_init_script("Object.defineProperty(Crypto.prototype, 'randomUUID', {value: undefined, configurable: true})")
                page = await context.new_page()
                await login(page, web_server)
                assert await page.evaluate("typeof crypto.randomUUID") == "undefined"
                await select_card(page, iccid)
                await page.locator("#month").fill("2031-07")
                await page.locator("#month").dispatch_event("change")
                await page.locator("#pkgMb").fill("2")
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("购买被明确拒绝：package_mb capacity exceeded")
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')") is None
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE iccid=%s", (iccid,)) == 0
                await page.locator("#month").fill("2031-08")
                await page.locator("#month").dispatch_event("change")
                await page.locator("#pkgMb").fill("1")
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("下单成功：PO")
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE iccid=%s AND month='2031-08'", (iccid,)) == 1
                await page.locator("#month").fill("2031-07")
                await page.locator("#month").dispatch_event("change")

                reached = asyncio.Event()
                release = asyncio.Event()

                async def late_usage(route):
                    response = await route.fetch()
                    reached.set()
                    await release.wait()
                    try:
                        await route.fulfill(response=response)
                    except Exception:
                        # A redirect may cancel this in-flight route before fulfillment.
                        pass

                await page.route(f"**/sims/{iccid}/usage?month=2031-07", late_usage, times=1)
                await page.locator("#btnUsage").click()
                await asyncio.wait_for(reached.wait(), 5)
                other_tab = await context.new_page()
                await login(other_tab, web_server, "bob", "user2pass")
                await page.wait_for_url("**/web/login.html")
                release.set()
                assert "2147483646" not in await page.locator("body").inner_text()
                assert await other_tab.locator("#tokenPreview").inner_text() != ""
            finally:
                await browser.close()

    asyncio.run(run())


def test_success_receipt_survives_storage_cleanup_failure(db_case, web_server):
    case = db_case
    iccid = case.make_card()

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await login(page, web_server)
                await select_card(page, iccid)
                await page.evaluate("""() => {
                    const original = Storage.prototype.removeItem;
                    Storage.prototype.removeItem = function(key) {
                        if (key.startsWith('iot-sim-ops:pending:')) throw new Error('synthetic storage failure');
                        return original.call(this, key);
                    };
                }""")
                await page.locator("#btnBuy").click()
                await expect(page.locator("#purchaseMsg")).to_contain_text("下单成功：PO")
                await expect(page.locator("#purchaseMsg")).to_contain_text("待确认记录清理失败")
                assert await page.locator("#btnBuy").is_disabled()
                assert await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')")
                assert case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE iccid=%s", (iccid,)) == 1
            finally:
                await browser.close()

    asyncio.run(run())


def test_purchase_timeout_keeps_original_snapshot_retriable(db_case, web_server):
    case = db_case
    iccid = case.make_card()

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await login(page, web_server)
                await select_card(page, iccid)
                await page.clock.install()
                blocked = asyncio.Event()
                release = asyncio.Event()

                async def hang(route):
                    blocked.set()
                    await release.wait()
                    try:
                        await route.abort("failed")
                    except Exception:
                        pass

                await page.route(f"**/sims/{iccid}/purchase", hang)
                await page.locator("#btnBuy").click()
                await asyncio.wait_for(blocked.wait(), 5)
                await page.clock.fast_forward(15001)
                await expect(page.locator("#purchaseMsg")).to_contain_text("请求超时")
                assert not await page.locator("#btnBuy").is_disabled()
                pending = await page.evaluate("sessionStorage.getItem('iot-sim-ops:pending:alice')")
                assert pending and case.scalar("SELECT COUNT(*) FROM sim_purchase WHERE iccid=%s", (iccid,)) == 0
                release.set()
            finally:
                await browser.close()

    asyncio.run(run())
