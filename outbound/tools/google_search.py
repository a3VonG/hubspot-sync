"""Google Custom Search API tool.

Requires environment variables:
    GOOGLE_API_KEY  - Google API key with Custom Search enabled
    GOOGLE_CX       - Custom Search Engine ID (create at https://cse.google.com/)
"""

import os
import requests

SCHEMA = {
    "name": "google_search",
    "description": (
        "Search Google for companies or websites. Use specific queries like "
        "'dental laboratory Italy', 'orthodontic lab directory Europe', etc. "
        "Returns up to 10 results with title, URL, and snippet."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query. Be specific with industry terms, "
                    "location, language as needed."
                ),
            }
        },
        "required": ["query"],
    },
}


def execute(query: str) -> str:
    """Run a Google Custom Search and return formatted results."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_CX")

    if not api_key or not cx:
        return (
            "Error: GOOGLE_API_KEY and GOOGLE_CX environment variables required. "
            "Set up a Custom Search Engine at https://cse.google.com/"
        )

    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": query, "num": 10},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"Search error: {e}"

    data = resp.json()
    items = data.get("items", [])

    if not items:
        return f"No results found for: {query}"

    results = []
    for i, item in enumerate(items, 1):
        results.append(
            f"{i}. {item['title']}\n"
            f"   URL: {item['link']}\n"
            f"   {item.get('snippet', '')}"
        )

    total = data.get("searchInformation", {}).get("totalResults", "?")
    return f"Results for '{query}' ({total} total):\n\n" + "\n\n".join(results)
