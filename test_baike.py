import json
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import html2text


HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
	"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
	"Accept-Language": "zh-CN,zh;q=0.9",
	"Referer": "https://baike.baidu.com/",
	"Connection": "keep-alive",
}


def make_session() -> requests.Session:
	s = requests.Session()
	s.headers.update(HEADERS)
	# 预热获取必要 Cookie，降低 403 概率
	s.get("https://baike.baidu.com/", timeout=10, allow_redirects=True)
	return s


SESSION = make_session()


def fetch(url: str) -> requests.Response:
	resp = SESSION.get(url, timeout=10, allow_redirects=True)
	resp.raise_for_status()
	return resp


def fetch_baike_api(name: str) -> dict:
	params = {
		"scope": "103",
		"format": "json",
		"appid": "379020",
		"bk_key": name,
		"bk_length": "1200",
	}
	resp = SESSION.get("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi", params=params, timeout=10)
	resp.raise_for_status()
	data = resp.json()
	url = data.get("url") or ""
	title = data.get("title") or data.get("name") or ""
	description = data.get("abstract") or data.get("desc") or ""
	content_text = data.get("abstract") or ""
	return {
		"url": url,
		"title": title,
		"description": description,
		"content_html": "",
		"content_text": content_text[:3000],
	}


def resolve_first_entry(soup: BeautifulSoup, base_url: str) -> str | None:
	first_poly_link = soup.select_one(".polysemantList-wrapper li a, .polysemantList-ul li a")
	if first_poly_link and first_poly_link.get("href"):
		return urljoin(base_url, first_poly_link.get("href"))
	return None


def extract_result(soup: BeautifulSoup, final_url: str) -> dict:
	title_el = soup.select_one(".lemmaWgt-lemmaTitle h1") or soup.find("h1")
	title_text = title_el.get_text(strip=True) if title_el else (soup.title.string.strip() if soup.title and soup.title.string else "")
	if title_text.endswith("_百度百科") or title_text.endswith(" - 百度百科"):
		title_text = title_text.replace("_百度百科", "").replace(" - 百度百科", "")

	desc_meta = soup.find("meta", attrs={"property": "og:description"})
	description = (desc_meta.get("content") or "").strip() if desc_meta else ""
	if not description:
		summary = soup.select_one(".lemma-summary")
		if summary:
			description = " ".join(summary.stripped_strings)

	content_el = (
		soup.select_one(".J-lemma-content")
		or soup.select_one(".lemma-main-content")
		or soup.select_one("#lemmaContent-0")
		or soup.select_one("#content")
	)

	if content_el:
		try:
			content_html = content_el.decode_contents()
		except Exception:
			content_html = str(content_el)
		content_text = " \n".join([s.strip() for s in content_el.stripped_strings if s.strip()])
	else:
		paras = soup.select(".lemma-summary .para, .para")
		content_text = "\n".join([p.get_text(strip=True) for p in paras]) if paras else "未找到内容"
		content_html = ""

	def convert_html_to_markdown(html: str, title: str, description: str) -> str:
		if not html:
			# 仅文本兜底：标题 + 描述 + 正文
			parts = []
			if title:
				parts.append(f"# {title}")
			if description:
				parts.append(f"> {description}")
			if content_text:
				parts.append(content_text)
			return "\n\n".join(parts)
		# 先尝试 markdownify（标题、列表、代码等更美观）
		md_text = md(html, heading_style="ATX")
		# 如果包含表格但没有生成表格风格，改用 html2text 的表格渲染
		if "<table" in html.lower() and "|" not in md_text:
			h = html2text.HTML2Text()
			h.body_width = 0
			h.ignore_images = True
			h.ignore_emphasis = False
			h.ignore_links = False
			h.single_line_break = True
			h.unicode_snob = True
			h.protect_links = True
			h.ignore_tables = False
			h.tables = True
			md_text = h.handle(html)
		# 前置标题与描述
		prefix = []
		if title:
			prefix.append(f"# {title}")
		if description:
			prefix.append(f"> {description}")
		if prefix:
			md_text = "\n\n".join(prefix) + "\n\n" + md_text
		return md_text

	content_markdown = convert_html_to_markdown(content_html, title_text, description)

	return {
		"url": final_url,
		"title": title_text,
		"description": description,
		"content_html": content_html[:3000],
		"content_text": content_text[:3000],
		"content_markdown": content_markdown[:3000],
	}


def fetch_baike(name: str) -> dict:
	encoded_name = quote(str(name).strip())
	url = f"https://baike.baidu.com/item/{encoded_name}"
	try:
		resp = fetch(url)
		soup = BeautifulSoup(resp.text, "html.parser")
		next_url = resolve_first_entry(soup, resp.url)
		if next_url:
			resp = fetch(next_url)
			soup = BeautifulSoup(resp.text, "html.parser")
		return extract_result(soup, resp.url)
	except requests.HTTPError as e:
		status = getattr(e.response, "status_code", None)
		if status in (403, 429):
			return fetch_baike_api(name)
		raise
	except Exception:
		# 兜底：尝试 API
		return fetch_baike_api(name)


if __name__ == "__main__":
	names = ["甄嬛传", "苹果", "电脑"]
	for n in names:
		try:
			result = fetch_baike(n)
			print(json.dumps({"name": n, "result": result}, ensure_ascii=False))
		except Exception as e:
			print(json.dumps({"name": n, "error": str(e)}, ensure_ascii=False))
