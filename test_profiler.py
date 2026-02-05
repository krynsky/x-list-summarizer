import asyncio
import json
from pathlib import Path
from app.x_list_summarizer import XListFetcher

async def test_memberships():
    fetcher = XListFetcher()
    username = 'elonmusk' # Test with a famous account
    
    print(f"Testing XListFetcher.get_user_memberships for @{username}...")
    
    try:
        memberships = await fetcher.get_user_memberships(username)
        print(f"✅ Success! Received {len(memberships)} memberships.")
        
        if memberships:
            print("\nSample memberships:")
            for m in memberships[:5]:
                print(f" - {m['name']} (Owner: @{m['owner']}, ID: {m['id']})")
                
            # Verify structure
            first = memberships[0]
            if all(k in first for k in ('name', 'owner', 'id')):
                print("\n✅ Data structure is correct.")
            else:
                print("\n❌ Data structure is MISSING keys.")
        else:
            print("❌ No memberships found (ensure cookies are valid).")

    except Exception as e:
        print(f"❌ Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(test_memberships())
