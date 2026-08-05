from typing import Any, List, Dict
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from config.config import TIMEOUT_MS

DEFAULT_KINGDOM = "1259"

async def perform_giftcode_redeem(player_id: str, kingdom: str, gift_code: str, page: Any) -> Dict[str, Any]:
    print(f"Trying to redeem [{gift_code}] for player: {player_id} (kingdom {kingdom})")

    await page.fill("input[placeholder='Player ID']", player_id)
    await page.fill("input[placeholder='Kingdom']", kingdom)
    await page.fill("input[placeholder='Enter Gift Code']", gift_code)
    await page.click("div.btn.exchange_btn")
    await page.wait_for_timeout(TIMEOUT_MS)

    try:
        await page.wait_for_selector("div.message_modal", timeout=TIMEOUT_MS*5)
        modal_text = await page.inner_text("div.modal_content .msg", timeout=TIMEOUT_MS)
        print("Redemption result:", modal_text)

        await page.click("div.confirm_btn")

        return {
            "success": "Redeemed, please claim the rewards in your mail!".lower() in modal_text.lower(),
            "message": modal_text,
        }
    except (PlaywrightTimeoutError, TimeoutError):
        return {"success": False, "message": "No confirmation modal appeared."}
    finally:
        try:
            await page.click("div.close_btn")
        except Exception:
            pass

async def redeem_giftcode_for_all_players(players: List[Dict[str, str]], gift_code: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto("https://ks-giftcode.centurygame.com/")

        for player in players:
            player_id = player.get("player_id", "")
            kingdom = player.get("kingdom") or DEFAULT_KINGDOM
            stored_nick = player.get("player_nick")

            result = await perform_giftcode_redeem(player_id, kingdom, gift_code, page)
            result_message = result.get("message")

            if result_message == "Gift Code not found, this is case-sensitive!":
                results.append({
                    "success": False,
                    "errorCode": "INVALID_CODE",
                    "message": "Invalid gift code.",
                })
                return results

            elif result_message == "Expired, unable to claim.":
                results.append({
                    "success": False,
                    "errorCode": "EXPIRED",
                    "message": "Gift code has expired.",
                })
                return results

            elif result_message in (
                "The same Gift Code type can only be redeemed once!",
                "Already claimed, unable to claim again.",
                "Claim limit reached, unable to claim.",
            ):
                results.append({
                    "player_id": player_id,
                    "stored_player_nick": stored_nick,
                    "result": result,
                    "success": True,
                })
            else:
                results.append({
                    "player_id": player_id,
                    "stored_player_nick": stored_nick,
                    "result": result,
                    "success": result.get("success", False),
                })

        await browser.close()
        return results
