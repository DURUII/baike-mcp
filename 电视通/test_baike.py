import json
from urllib.parse import quote, urljoin
from typing import Optional
import re

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import html2text


# def fetch_baike_api(name: str) -> dict:
# 	params = {
# 		"scope": "103",
# 		"format": "json",
# 		"appid": "379020",
# 		"bk_key": name,
# 		"bk_length": "1200",
# 	}
# 	resp = SESSION.get("https://baike.baidu.com/api/openapi/BaikeLemmaCardApi", params=params, timeout=10)
# 	resp.raise_for_status()
# 	data = resp.json()
# 	url = data.get("url") or ""
# 	title = data.get("title") or data.get("name") or ""
# 	description = data.get("abstract") or data.get("desc") or ""
# 	content_text = data.get("abstract") or ""
# 	return {
# 		"url": url,
# 		"title": title,
# 		"description": description,
# 		"content_html": "",
# 		"content_text": content_text,
# 	}

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

def resolve_first_entry(soup: BeautifulSoup, base_url: str) -> Optional[str]:
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

	def _extract_text_from_plot_detail(plot_detail: dict) -> str:
		if not isinstance(plot_detail, dict):
			return ""
		texts = plot_detail.get("text")
		if not isinstance(texts, list):
			# 兜底：可能直接是 value 长文本
			val = plot_detail.get("value")
			return str(val).strip() if isinstance(val, str) else ""
		parts = []
		for block in texts:
			if not isinstance(block, dict):
				continue
			if block.get("tag") == "paragraph":
				content_list = block.get("content", [])
				for item in content_list:
					if isinstance(item, dict) and item.get("tag") == "text":
						parts.append(str(item.get("text", "")))
			elif block.get("tag") == "text":
				parts.append(str(block.get("text", "")))
		return "".join(parts).strip()

	def _extract_title_text(plot_title: dict) -> str:
		if not isinstance(plot_title, dict):
			return ""
		texts = plot_title.get("text")
		if isinstance(texts, list):
			for t in texts:
				if isinstance(t, dict) and t.get("tag") == "text":
					val = str(t.get("text", "")).strip()
					if val:
						return val
		# 兜底
		candidate = plot_title.get("title") or plot_title.get("text")
		return str(candidate).strip() if isinstance(candidate, str) else ""

	def _parse_episode_number(title: str) -> Optional[int]:
		if not title:
			return None
		m = re.search(r"第\s*([一二三四五六七八九十百零〇两\d]+)\s*集", title)
		if not m:
			return None
		raw = m.group(1)
		if re.search(r"\d", raw):
			try:
				return int(re.sub(r"\D", "", raw))
			except Exception:
				return None
		# 简单中文数字转阿拉伯数字（<= 99）
		mapping = {"零":0, "〇":0, "一":1, "二":2, "两":2, "三":3, "四":4, "五":5, "六":6, "七":7, "八":8, "九":9}
		if "百" in raw:
			# 不太可能用到，简单处理，如 一百二 -> 102
			parts = raw.split("百")
			h = mapping.get(parts[0], 0) if parts[0] else 1
			rest = parts[1] if len(parts) > 1 else ""
			tens = 0
			if rest:
				if "十" in rest:
					sub = rest.split("十")
					tens = (mapping.get(sub[0], 0) if sub[0] else 1) * 10
					units = mapping.get(sub[1], 0) if len(sub) > 1 else 0
					return h * 100 + tens + units
				else:
					units = mapping.get(rest, 0)
					return h * 100 + units
			return h * 100
		if "十" in raw:
			parts = raw.split("十")
			left = parts[0]
			right = parts[1] if len(parts) > 1 else ""
			left_val = mapping.get(left, 0) if left else 1
			right_val = mapping.get(right, 0) if right else 0
			return left_val * 10 + right_val
		# 个位数
		return mapping.get(raw, None)

	def _collect_episodes_from_payload(payload) -> list:
		items = []
		def handle_item(obj: dict):
			if not isinstance(obj, dict):
				return
			pt = obj.get("plotTitle")
			pd = obj.get("plotDetail")
			if not pt or not pd:
				return
			title_text = _extract_title_text(pt)
			episode_no = _parse_episode_number(title_text)
			summary_text = _extract_text_from_plot_detail(pd)
			if episode_no is not None and summary_text:
				items.append({"episode": episode_no, "summary": summary_text})
		if isinstance(payload, dict):
			data_list = payload.get("data")
			if isinstance(data_list, list):
				for it in data_list:
					handle_item(it)
			# 兜底：直接就是一个分集对象
			handle_item(payload)
		elif isinstance(payload, list):
			for it in payload:
				handle_item(it)
		return items

	def _try_json_loads(raw: str):
		try:
			return json.loads(raw)
		except Exception:
			return None

	# 搜索候选 JSON：属性 data-module-value、隐藏元素文本、script JSON，以及包含关键词的 span/div 文本
	candidate_payloads = []
	for el in soup.select('[data-module-value]'):
		raw = (el.get('data-module-value') or '').strip()
		if not raw:
			continue
		obj = _try_json_loads(raw)
		if obj is not None:
			candidate_payloads.append(obj)
	# 隐藏/模块数据与 script JSON
	for el in soup.select('[style*="display:none"], .module-data, script[type="application/json"]'):
		# 属性优先
		raw_attr = (el.get('data-module-value') or '').strip() if hasattr(el, 'get') else ''
		if raw_attr:
			obj = _try_json_loads(raw_attr)
			if obj is not None:
				candidate_payloads.append(obj)
		# 文本
		raw_text = el.string if el and el.string else el.get_text(strip=True)
		if raw_text and 'plotDetail' in raw_text and 'plotTitle' in raw_text:
			obj = _try_json_loads(raw_text)
			if obj is not None:
				candidate_payloads.append(obj)
	# 少部分直接放到 span/div 文本里
	for el in soup.select('span, div'):
		raw_text = el.string if el and el.string else None
		if not raw_text:
			continue
		st = raw_text.strip()
		if len(st) < 50:
			continue
		if 'plotDetail' in st and 'plotTitle' in st and st.count('{') >= 1 and st.count('}') >= 1:
			obj = _try_json_loads(st)
			if obj is not None:
				candidate_payloads.append(obj)

	# 汇总去重与排序
	episodes = []
	for payload in candidate_payloads:
		items = _collect_episodes_from_payload(payload)
		if items:
			episodes.extend(items)
	# 去重，按 episode 首次出现保留
	seen = set()
	unique = []
	for ep in sorted(episodes, key=lambda x: x.get('episode', 0)):
		no = ep.get('episode')
		if no in seen:
			continue
		seen.add(no)
		unique.append(ep)

	return {
		"url": final_url,
		"title": title_text,
		"description": description,
		# "content_html": content_html,
		# "content_text": content_text,
		"content_markdown": content_markdown,
		"episode_summary": unique,
	}

def fetch_baike(name: str) -> dict:
	encoded_name = quote(str(name).strip())
	url = f"https://baike.baidu.com/item/{encoded_name}"
	resp = fetch(url)
	soup = BeautifulSoup(resp.text, "html.parser")
	next_url = resolve_first_entry(soup, resp.url)
	if next_url:
		resp = fetch(next_url)
		soup = BeautifulSoup(resp.text, "html.parser")
	return extract_result(soup, resp.url)
	# except requests.HTTPError as e:
	# 	print("b")
	# 	status = getattr(e.response, "status_code", None)
	# 	if status in (403, 429):
	# 		return fetch_baike_api(name)
	# 	raise
	# except Exception:
	# 	# 兜底：尝试 API
	# 	print("C")
	# 	return fetch_baike_api(name)


if __name__ == "__main__":
	names = ["甄嬛传"]
	for n in names:
		try:
			result = fetch_baike(n)
			with open("result.json", "a", encoding="utf-8") as f:
				f.write(
					json.dumps(
						{"name": n, "result": result},
						ensure_ascii=False,
						indent=2
					) + "\n"
				)
		except Exception as e:
			print(json.dumps({"name": n, "error": str(e)}, ensure_ascii=False))
