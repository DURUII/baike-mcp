from fastmcp import FastMCP
from typing import Any, Dict

from utils.baike import fetch_baike


mcp = FastMCP("baike-mcp")

@mcp.tool
def baike_markdown(name: str) -> Dict[str, Any]:
    """General Baike query: return markdown content.

    Args:
        name: Lemma name, e.g., "苹果"
    Returns:
        { title, url, content_markdown }
    """
    result = fetch_baike(name) or {}
    return {
        "title": result.get("title"),
        "url": result.get("url"),
        "content_markdown": result.get("content_markdown"),
    }

@mcp.tool
def baike_episode_summary(name: str) -> Dict[str, Any]:
    """Fetch Baidu Baike and return episode summaries.

    Args:
        name: Lemma name, e.g., "甄嬛传"
    Returns:
        { title, url, description, count, episode_summary }
    """
    result = fetch_baike(name) or {}
    episodes = result.get("episode_summary") or []
    out = {
        "title": result.get("title"),
        "url": result.get("url"),
        "description": result.get("description"),
        "count": len(episodes),
        "episode_summary": episodes,
    }
    return out


