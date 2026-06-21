# -*- coding: utf-8 -*-
"""PaperLens core pipeline: search, read, summarize, and render reports."""
import datetime
import html as html_lib
import json
import os
import re
import time
from collections import OrderedDict

TAKEAWAY_FIELD = "核心结论"
FIELDS = ["研究问题", "方法", "创新点", "实验与结果", "局限", "对我们需求的相关性"]
DISPLAY_FIELDS = [TAKEAWAY_FIELD] + FIELDS

PROVIDER_BASE = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "kimi": "https://api.moonshot.cn/v1",
}


def _call_anthropic(spec, prompt, max_tokens):
    from anthropic import Anthropic

    kwargs = {"api_key": spec["api_key"]}
    if spec.get("base_url"):
        kwargs["base_url"] = spec["base_url"]
    client = Anthropic(**kwargs)
    resp = client.messages.create(
        model=spec["model"],
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_openai_compatible(spec, prompt, max_tokens):
    from openai import OpenAI

    base_url = spec.get("base_url") or PROVIDER_BASE.get(spec["provider"], PROVIDER_BASE["openai"])
    client = OpenAI(api_key=spec["api_key"], base_url=base_url)
    resp = client.chat.completions.create(
        model=spec["model"],
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def _ask(spec, prompt, max_tokens=2000):
    if not spec or not spec.get("api_key"):
        model = spec.get("model", "?") if spec else "?"
        raise RuntimeError(f"模型档位 [{model}] 缺少 API Key。")
    if spec.get("provider", "anthropic") == "anthropic":
        return _call_anthropic(spec, prompt, max_tokens)
    return _call_openai_compatible(spec, prompt, max_tokens)


def norm_title(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def clean_json_text(raw):
    return raw.strip().replace("```json", "").replace("```", "").strip()


def build_query(spec, query):
    prompt = (
        "把下面的中文研究需求改写成适合学术检索的英文关键词查询。\n"
        "要求：只输出检索字符串；使用空格分隔；包含 3-6 个核心术语；不要解释，不要加引号。\n\n"
        f"研究需求：{query}"
    )
    return _ask(spec, prompt, 200).strip()


def search_arxiv(query, pool, years_back):
    import arxiv

    search = arxiv.Search(query=query, max_results=pool, sort_by=arxiv.SortCriterion.Relevance)
    cutoff_year = datetime.datetime.now(datetime.timezone.utc).year - years_back if years_back else None
    papers = []
    for result in arxiv.Client().results(search):
        if cutoff_year and result.published.year < cutoff_year:
            continue
        papers.append({
            "title": result.title.strip().replace("\n", " "),
            "authors": [author.name for author in result.authors][:5],
            "year": result.published.year,
            "abstract": result.summary.strip().replace("\n", " "),
            "pdf_url": result.pdf_url,
            "arxiv_id": result.get_short_id(),
            "url": result.entry_id,
            "citations": None,
            "venue": "arXiv",
            "source": "arXiv",
        })
    return papers


def search_s2(query, pool, years_back, s2_key=""):
    import requests

    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    headers = {"x-api-key": s2_key} if s2_key else {}
    year = datetime.datetime.now().year
    params = {
        "query": query,
        "limit": pool,
        "fields": "title,year,authors,citationCount,abstract,externalIds,publicationVenue",
    }
    if years_back:
        params["year"] = f"{year - years_back}-{year}"

    papers = []
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        for paper in resp.json().get("data", []):
            external_ids = paper.get("externalIds") or {}
            arxiv_id = external_ids.get("ArXiv")
            venue = (paper.get("publicationVenue") or {}).get("name") or ""
            papers.append({
                "title": (paper.get("title") or "").strip(),
                "authors": [a["name"] for a in (paper.get("authors") or [])][:5],
                "year": paper.get("year"),
                "abstract": (paper.get("abstract") or "").strip(),
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                "citations": paper.get("citationCount"),
                "venue": venue,
                "source": "S2",
            })
    except Exception as exc:
        print("Semantic Scholar search failed; ignored:", exc)
    return papers


def merge_dedup(arxiv_papers, s2_papers):
    by_title = {}
    for paper in arxiv_papers + s2_papers:
        if not paper["title"]:
            continue
        key = norm_title(paper["title"])
        if key not in by_title:
            by_title[key] = paper
            continue
        current = by_title[key]
        if current.get("citations") is None and paper.get("citations") is not None:
            current["citations"] = paper["citations"]
        if not current.get("pdf_url") and paper.get("pdf_url"):
            current["pdf_url"] = paper["pdf_url"]
            current["arxiv_id"] = paper.get("arxiv_id")
            current["url"] = paper.get("url") or current["url"]
        if (not current.get("venue") or current["venue"] == "arXiv") and paper.get("venue"):
            current["venue"] = paper["venue"]
    return list(by_title.values())


def rank(spec, query, candidates, n):
    listing = "\n".join(
        f"{i}. [{paper.get('citations', '?')} cites] {paper['title']} -- {paper['abstract'][:220]}"
        for i, paper in enumerate(candidates)
    )
    prompt = (
        f"研究需求：{query}\n\n"
        f"候选论文：\n{listing}\n\n"
        f"请按与研究需求的相关性选择最相关的 {n} 篇。相关性优先于引用量。\n"
        "严格只输出 JSON 数组，例如 [3,0,5]，不要输出其他文字。"
    )
    raw = clean_json_text(_ask(spec, prompt, 300))
    try:
        indexes = json.loads(raw)
    except Exception:
        indexes = sorted(range(len(candidates)), key=lambda i: (candidates[i].get("citations") or 0), reverse=True)
    return [candidates[i] for i in indexes if isinstance(i, int) and 0 <= i < len(candidates)][:n]


def dl_extract(paper, out_dir, mirror=None):
    import fitz
    import requests

    if not paper.get("pdf_url"):
        paper["fulltext"] = paper["abstract"]
        return False

    pdf_url = paper["pdf_url"]
    if mirror:
        pdf_url = pdf_url.replace("https://arxiv.org", mirror).replace("http://arxiv.org", mirror)

    sid = (paper.get("arxiv_id") or norm_title(paper["title"])[:20]).replace("/", "_")
    path = os.path.join(out_dir, "pdfs", f"{sid}.pdf")
    try:
        if not os.path.exists(path):
            resp = requests.get(pdf_url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            with open(path, "wb") as f:
                f.write(resp.content)
        doc = fitz.open(path)
        paper["fulltext"] = "\n".join(page.get_text() for page in doc)
        doc.close()
        return True
    except Exception as exc:
        if mirror:
            try:
                resp = requests.get(paper["pdf_url"], timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(resp.content)
                doc = fitz.open(path)
                paper["fulltext"] = "\n".join(page.get_text() for page in doc)
                doc.close()
                return True
            except Exception as fallback_exc:
                exc = fallback_exc
        print("PDF download/extract failed:", exc)
        paper["fulltext"] = paper["abstract"]
        return False


def extract_key_sections(fulltext, limit=12000):
    if len(fulltext) <= limit:
        return fulltext
    low = fulltext.lower()
    head = fulltext[: int(limit * 0.55)]
    tail = ""
    for keyword in ["conclusion", "concluding", "summary", "discussion", "future work"]:
        idx = low.rfind(keyword)
        if idx > len(fulltext) * 0.5:
            tail = fulltext[idx: idx + int(limit * 0.4)]
            break
    if not tail:
        tail = fulltext[-int(limit * 0.4):]
    return head + "\n\n[...中间实验细节已省略...]\n\n" + tail


def normalize_summary(summary):
    normalized = {}
    takeaway = summary.get(TAKEAWAY_FIELD, "") if isinstance(summary, dict) else ""
    normalized[TAKEAWAY_FIELD] = str(takeaway or "").strip()
    for field in FIELDS:
        value = summary.get(field, "") if isinstance(summary, dict) else ""
        if isinstance(value, list):
            value = "；".join(str(item).strip() for item in value if str(item).strip())
        normalized[field] = str(value or "").strip()
    return normalized


def summarize(spec, paper, query):
    text = extract_key_sections(paper["fulltext"], 12000)
    field_text = "、".join(FIELDS)
    prompt = (
        "你是严谨的科研助理。下面是一篇论文全文或关键截断内容。\n"
        f"研究需求：{query}\n\n"
        "请用中文严格输出 JSON，格式如下：\n"
        "{\n"
        '  "路线": "一句短语，概括技术路线，同类论文用一致短语",\n'
        '  "摘要": {\n'
        f'    "{TAKEAWAY_FIELD}": "用一句话说明这篇论文最值得记住的结论，以及它为什么重要",\n'
        f'    "{FIELDS[0]}": "1-3 个要点，可用分号分隔",\n'
        f'    "{FIELDS[1]}": "1-3 个要点，可用分号分隔",\n'
        f'    "{FIELDS[2]}": "1-3 个要点，可用分号分隔",\n'
        f'    "{FIELDS[3]}": "1-3 个要点，可用分号分隔",\n'
        f'    "{FIELDS[4]}": "1-3 个要点，可用分号分隔",\n'
        f'    "{FIELDS[5]}": "1-3 个要点，可用分号分隔"\n'
        "  }\n"
        "}\n"
        f"字段必须包含：{field_text}。基于原文，不要编造；未明确提到就写“未明确说明”。只输出 JSON，不要代码块。\n\n"
        f"标题：{paper['title']}\n\n全文：{text}"
    )
    raw = clean_json_text(_ask(spec, prompt, 1800))
    try:
        obj = json.loads(raw)
        route = str(obj.get("路线", "未分类")).strip() or "未分类"
        return route, normalize_summary(obj.get("摘要", {}))
    except Exception:
        return "未分类", {field: (raw if field == TAKEAWAY_FIELD else "解析失败") for field in DISPLAY_FIELDS}


def summarize_abstract_batch(spec, papers, query):
    if not papers:
        return
    listing = "\n\n".join(
        f"[{i}]\n标题：{paper['title']}\n年份：{paper.get('year', '')}\n摘要：{paper.get('abstract', '')[:1800]}"
        for i, paper in enumerate(papers)
    )
    prompt = (
        "你是科研情报分析师。下面这些论文只做摘要级快速研判，不要假装读过全文。\n"
        f"研究需求：{query}\n\n"
        f"论文列表：\n{listing}\n\n"
        "请严格输出 JSON 数组，每个元素格式如下：\n"
        "{\n"
        '  "index": 0,\n'
        '  "路线": "技术路线短语",\n'
        '  "摘要": {\n'
        f'    "{TAKEAWAY_FIELD}": "一句话说明这篇论文从摘要看最可能有用的点",\n'
        f'    "{FIELDS[0]}": "它试图解决的问题",\n'
        f'    "{FIELDS[1]}": "摘要中能确认的方法",\n'
        f'    "{FIELDS[2]}": "摘要中能确认的贡献",\n'
        f'    "{FIELDS[3]}": "摘要中能确认的结果；不明确就写未明确说明",\n'
        f'    "{FIELDS[4]}": "摘要中能确认的局限；不明确就写未明确说明",\n'
        f'    "{FIELDS[5]}": "与研究需求的关系"\n'
        "  }\n"
        "}\n"
        "只能基于标题和摘要判断。不要输出代码块。"
    )
    raw = clean_json_text(_ask(spec, prompt, min(3800, 900 + 520 * len(papers))))
    try:
        items = json.loads(raw)
        by_index = {item.get("index"): item for item in items if isinstance(item, dict)}
    except Exception:
        by_index = {}
    for i, paper in enumerate(papers):
        item = by_index.get(i, {})
        paper["route"] = str(item.get("路线", "摘要级候选")).strip() or "摘要级候选"
        paper["summary"] = normalize_summary(item.get("摘要", {}))
        if not paper["summary"].get(TAKEAWAY_FIELD):
            paper["summary"][TAKEAWAY_FIELD] = "摘要级候选：需要进一步阅读全文确认价值。"
        paper["read_level"] = "摘要研判"


def overall(spec, selected, query):
    bullets = "\n".join(
        f"- {paper['title']}（{paper.get('year', '')}，{paper['route']}）：{paper['summary'].get('创新点', '')}"
        for paper in selected
    )
    prompt = (
        f"研究需求：{query}\n\n论文要点：\n{bullets}\n\n"
        "请用中文写 150-250 字研究现状综述，点明技术路线演进、共识分歧和对需求的启示。只输出正文。"
    )
    return _ask(spec, prompt, 800).strip()


def route_intro(spec, route, papers, query):
    titles = "; ".join(paper["title"] for paper in papers)
    prompt = (
        f'请用中文写 1-2 句话，概括“{route}”路线的核心思路与优势。\n'
        f"研究需求：{query}\n"
        f"包含论文：{titles}\n"
        "只输出这段话。"
    )
    return _ask(spec, prompt, 300).strip()


def run_pipeline(cfg, params, progress=None):
    def emit(pct, stage, desc=""):
        if progress:
            progress(pct, stage, desc)

    sp_cheap = cfg["spec_cheap"]
    sp_main = cfg["spec_main"]
    sp_premium = cfg.get("spec_premium") or sp_main
    out_dir = cfg["out_dir"]
    os.makedirs(os.path.join(out_dir, "pdfs"), exist_ok=True)

    query = params["query"]
    n = params["num_papers"]
    years_back = params["years_back"]
    arxiv_mirror = cfg.get("arxiv_mirror") or None

    emit(5, "search", "正在转换检索关键词")
    search_query = build_query(sp_cheap, query)
    emit(12, "search", f"正在检索：{search_query}")

    arxiv_papers = search_arxiv(search_query, 40, years_back)
    time.sleep(1)
    s2_papers = search_s2(search_query, 40, years_back, cfg.get("semantic_scholar_key", ""))
    candidates = merge_dedup(arxiv_papers, s2_papers)
    if not candidates:
        raise RuntimeError("没有检索到论文。可能是网络无法访问 arXiv/Semantic Scholar，或需要换一种需求写法。")

    if len(candidates) > max(2 * n, 20):
        candidates = sorted(
            candidates,
            key=lambda paper: ((paper.get("citations") or 0), paper.get("year") or 0),
            reverse=True,
        )[: max(2 * n, 20)]

    emit(30, "filter", f"找到 {len(candidates)} 篇候选论文，正在筛选最相关的 {n} 篇")
    selected = rank(sp_cheap, query, candidates, n)

    deep_count = min(len(selected), max(3, min(6, (n + 2) // 3)))
    deep_papers = selected[:deep_count]
    light_papers = selected[deep_count:]

    for i, paper in enumerate(deep_papers):
        emit(30 + int(38 * i / max(len(deep_papers), 1)), "read", f"正在深读核心论文 {i + 1}/{len(deep_papers)}")
        dl_extract(paper, out_dir, arxiv_mirror)
        paper["route"], paper["summary"] = summarize(sp_main, paper, query)
        paper["read_level"] = "全文精读"

    if light_papers:
        emit(70, "read", f"正在对其余 {len(light_papers)} 篇做摘要级研判")
        summarize_abstract_batch(sp_cheap, light_papers, query)

    emit(85, "write", "正在归纳技术路线")
    groups = OrderedDict()
    for paper in selected:
        groups.setdefault(paper["route"], []).append(paper)
    for route in groups:
        groups[route].sort(key=lambda item: item.get("year") or 0, reverse=True)

    intros = {route: route_intro(sp_cheap, route, papers, query) for route, papers in groups.items()}
    review = overall(sp_premium, selected, query)

    emit(96, "write", "正在生成报告")
    result = {
        "query": query,
        "date": datetime.date.today().isoformat(),
        "review": review,
        "groups": [{"route": route, "intro": intros[route], "papers": papers} for route, papers in groups.items()],
        "count": len(selected),
    }

    html_path = os.path.join(out_dir, f"report_{result['date']}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_report_html(result))
    result["html_path"] = html_path
    emit(100, "done", "完成")
    return result


def split_points(text):
    points = re.split(r"\n+|；|;|(?<=[。.!?？])\s+", str(text or ""))
    return [re.sub(r"^[-•·\d.、\s]+", "", point).strip() for point in points if point.strip()][:3]


def render_report_html(result):
    def esc(value):
        return html_lib.escape(str(value or ""))

    groups_html = ""
    for group in result["groups"]:
        cards = ""
        for paper in group["papers"]:
            summary = paper.get("summary") or {}
            takeaway = summary.get(TAKEAWAY_FIELD) or summary.get("研究问题") or "暂无明确核心结论。"
            sections = ""
            for field in FIELDS:
                points = "；".join(split_points(summary.get(field, ""))) or "未明确说明"
                sections += f"<section><h4>{esc(field)}</h4><p>{esc(points)}</p></section>"
            meta_parts = [
                esc(", ".join(paper.get("authors", []))),
                esc(paper.get("year", "")),
                esc(paper.get("venue", "")),
                esc(paper.get("read_level", "全文精读")),
                f"<a href='{esc(paper.get('url', ''))}'>原文</a>" if paper.get("url") else "",
            ]
            meta = " · ".join(part for part in meta_parts if part)
            cards += (
                "<article class='paper'>"
                f"<h3>{esc(paper['title'])}</h3>"
                f"<p class='meta'>{meta}</p>"
                f"<p class='takeaway'>{esc(takeaway)}</p>"
                f"<div class='sections'>{sections}</div>"
                "</article>"
            )
        groups_html += (
            "<section class='group'>"
            f"<h2>{esc(group['route'])} <span>{len(group['papers'])} 篇</span></h2>"
            f"<p class='intro'>{esc(group['intro'])}</p>"
            f"<div class='grid'>{cards}</div>"
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>PaperLens 报告</title>
<style>
body{{margin:0;background:#f5f5f7;color:#1d1d1f;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1080px;margin:0 auto;padding:34px 24px 52px}}
h1{{font-size:28px;margin:0 0 10px}}
.meta,.intro{{color:#6e6e73;line-height:1.7}}
.review,.paper{{background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:20px;box-shadow:0 8px 26px rgba(0,0,0,.06)}}
.review{{padding:20px;margin:20px 0}}
.review p{{line-height:1.9;margin:8px 0 0}}
.group{{margin-top:28px}}
.group h2{{font-size:20px;margin:0 0 8px}}
.group h2 span{{font-size:12px;color:#6e6e73;background:rgba(0,0,0,.06);border-radius:99px;padding:4px 9px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:14px}}
.paper{{padding:18px}}
.paper h3{{font-size:16px;line-height:1.45;margin:0}}
.paper .meta{{font-size:12px;margin:8px 0 14px}}
.paper a{{color:#0071e3;text-decoration:none}}
.takeaway{{margin:0 0 12px;padding:11px 12px;border-radius:14px;background:#eef7ff;color:#233954;line-height:1.75}}
.sections{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
.sections section{{padding:10px;border-radius:12px;background:#f8fbff;border:1px solid rgba(0,0,0,.06)}}
.sections h4{{margin:0 0 5px;color:#0071e3;font-size:12px}}
.sections p{{margin:0;font-size:13px;line-height:1.65;color:#334155}}
footer{{color:#86868b;font-size:12px;margin-top:30px;padding-top:16px;border-top:1px solid rgba(0,0,0,.08)}}
</style>
</head>
<body>
<main>
  <h1>PaperLens 检索总结报告</h1>
  <p class="meta"><b>研究需求：</b>{esc(result['query'])}<br><b>生成日期：</b>{esc(result['date'])} · <b>论文数：</b>{esc(result['count'])}</p>
  <section class="review"><b>研究现状综述</b><p>{esc(result['review'])}</p></section>
  {groups_html}
  <footer>由 PaperLens 自动生成</footer>
</main>
</body>
</html>"""
