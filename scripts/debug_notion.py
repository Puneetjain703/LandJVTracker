import os
from notion_client import Client
from backend.app.config import get_settings

def debug():
    settings = get_settings()
    if not settings.notion_api_key:
        print("Error: NOTION_API_KEY not found in settings!")
        return
    
    client = Client(auth=settings.notion_api_key)
    page_id = settings.notion_pearl_projects_page_id
    print(f"Retrieving page {page_id}...")
    try:
        page = client.pages.retrieve(page_id=page_id)
        print("Page retrieved successfully!")
        
        for prop_name in ["Tasks", "Notes"]:
            prop = page.get("properties", {}).get(prop_name)
            if not prop:
                print(f"Property {prop_name} not found!")
                continue
            
            print(f"\nRetrieving property item for {prop_name} (ID: {prop['id']})...")
            try:
                response = client.pages.properties.retrieve(
                    page_id=page_id,
                    property_id=prop["id"]
                )
                print(f"Type of response: {type(response)}")
                print(f"Response keys: {response.keys() if isinstance(response, dict) else 'Not a dict'}")
                if isinstance(response, dict):
                    print(f"Object: {response.get('object')} | Type: {response.get('type')}")
                    if "results" in response:
                        print(f"Results len: {len(response['results'])}")
                        for r in response["results"][:3]:
                            print(f"  Result: {r}")
                    if "relation" in response:
                        print(f"Relation value: {response['relation']}")
                else:
                    print(f"Response: {response}")
            except Exception as e:
                print(f"Error retrieving property item: {e}")
    except Exception as e:
        print(f"Error retrieving page: {e}")

if __name__ == "__main__":
    debug()
