"""Fetch account-specific catalogs locally; never commit the resulting data."""
import asyncio, json
import gateway_gpt as g

async def main():
    try:
        if not g.KEY:
            raise RuntimeError("Anthropic key missing from launcher process")
        auth = g.read_json(g.AUTH_FILE, {})
        tokens = auth.get("tokens") or {}
        if not tokens.get("access_token") or not tokens.get("account_id"):
            raise RuntimeError("Sign in to local Codex with ChatGPT first")
        await g.refresh_gpt({"Authorization": "Bearer " + tokens["access_token"],
                             "ChatGPT-Account-ID": tokens["account_id"]}, "")
        if not g.gateway.gpt_models:
            raise RuntimeError("GPT catalog bootstrap failed")
        await g.refresh_anthropic()
        print("Catalog ready: GPT=" + str(len(g.gateway.gpt_models)) +
              " Claude lines=" + str(len(g.gateway.claude_models)))
        print("Catalog path: " + str(g.COMBINED_CATALOG))
    finally:
        await g.gateway.client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
