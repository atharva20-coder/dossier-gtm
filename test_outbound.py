import asyncio
import os
import json
import httpx
import time

async def run():
    # Read from the environment, not baked in: this file is committed and a
    # key pasted into it is a key published.
    headers = {"Authorization": f"Bearer {os.environ['APP_ACCESS_KEY']}"}
    async with httpx.AsyncClient(base_url="http://localhost:8000", headers=headers, timeout=120) as client:
        # Create run
        print("1. Creating outbound run for ramp.com...")
        res = await client.post("/api/outbound/runs", json={"target_company": "ramp.com", "config": {}})
        run_id = res.json().get("run_id")
        if not run_id:
            print(f"Error creating run: {res.text}")
            return
            
        print(f"Run ID: {run_id}")
        
        # Execute run (background)
        print("2. Executing pipeline (discovering competitors, scraping contacts, enriching, drafting)...")
        await client.post(f"/api/outbound/runs/{run_id}/execute")
        
        # Poll for completion
        while True:
            res = await client.get(f"/api/outbound/runs/{run_id}")
            status = res.json()["run"]["status"]
            stages = res.json().get("stages", [])
            last_stage = stages[-1]["stage"] if stages else "starting"
            last_status = stages[-1]["status"] if stages else "starting"
            print(f"  Status: {status} (Stage: {last_stage} - {last_status})")
            if status in ["completed", "error"]:
                break
            await asyncio.sleep(2)
            
        print("\n--- PIPELINE COMPLETED ---")
        
        # Fetch results
        res = await client.get(f"/api/outbound/runs/{run_id}")
        data = res.json()
        print(f"\nCompetitors Found:")
        for c in data["run"].get("competitors", []):
            print(f" - {c.get('name')} ({c.get('domain')})")
            
        res = await client.get(f"/api/outbound/runs/{run_id}/contacts")
        contacts = res.json()["contacts"]
        print(f"\nContacts Found ({len(contacts)}):")
        for c in contacts:
            print(f" - {c.get('name')} | {c.get('company')} | {c.get('role')}")
            if c.get("email"):
                print(f"   Email: {c.get('email')} ({c.get('email_source')})")
            if c.get("opener_line"):
                print(f"   Hook: {c.get('opener_line')}")
                
        res = await client.get(f"/api/outbound/runs/{run_id}/campaigns")
        campaigns = res.json()["campaigns"]
        print(f"\nCampaigns Created:")
        for c in campaigns:
            print(f" - {c.get('name')} (Contacts: {c.get('contact_count')})")
            
asyncio.run(run())
