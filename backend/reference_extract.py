"""Shared pure extraction for document imports and archived compatibility routes."""
import re


def compact(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[\s\u3000，。；：、,.!?！？:;《》〈〉“”‘’'\"（）()\[\]【】\-—_]+", "", value)
    return value


def split_reference_fields(raw: str) -> tuple[str, str, str]:
    cleaned = re.sub(r"^\s*(?:\[\s*\d+\s*\]|[（(]?\d+[）).、]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t。；;.")
    author_match = re.match(r"^([^，,。:：]{2,36})[，,]", cleaned)
    authors = author_match.group(1).strip() if author_match else ""
    title_match = re.search(r"《([^》]{2,100})》|“([^”]{2,100})”|\"([^\"]{2,100})\"", cleaned)
    if title_match:
        title = next((group for group in title_match.groups() if group), "")
    else:
        title = cleaned
        if author_match:
            title = title[author_match.end():]
        title = re.split(r"[，,。；;]", title)[0].strip(" 《》‘’\"'")
        title = title or cleaned[:100]
    year_match = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|21\d{2})(?!\d)", cleaned)
    return title[:180], authors[:80], year_match.group(1) if year_match else ""


def extract_references(pages: list[tuple[int, str]]) -> list[dict]:
    references: list[dict] = []
    in_bibliography = False
    current: list[str] = []
    current_page = 1
    item_pattern = re.compile(r"^\s*(?:\[\s*\d+\s*\]|[（(]?\d{1,3}[）).、]|[①②③④⑤⑥⑦⑧⑨⑩])\s*")
    heading_pattern = re.compile(r"^\s*(参考文献|主要参考书目|引用书目|参考资料|bibliography|references|works cited)\s*$", re.I)

    def flush():
        nonlocal current
        raw = re.sub(r"\s+", " ", " ".join(current)).strip()
        if len(raw) >= 14:
            title, authors, year = split_reference_fields(raw)
            references.append({"raw_text": raw[:2500], "title": title, "authors": authors, "year": year, "page_number": current_page})
        current = []

    for page_number, text in pages:
        for original in text.splitlines():
            line = re.sub(r"\s+", " ", original).strip()
            if not line:
                continue
            if heading_pattern.match(line.rstrip("：:")):
                flush()
                in_bibliography = True
                continue
            if in_bibliography:
                if re.match(r"^\s*(附录|致谢|作者简介|appendix|acknowledg(?:e)?ments)\b", line, re.I):
                    flush()
                    in_bibliography = False
                    continue
                if item_pattern.match(line):
                    flush()
                    current_page = page_number
                    current = [line]
                elif current and len(current[-1]) < 1800:
                    current.append(line)
    flush()
    # Preserve duplicate appearances in the document; do not silently merge editions.
    return references[:1000]
