# -*- coding: utf-8 -*-
"""新闻与政策采集器（迭代式 Deep Research）

采集主流财经媒体新闻与政府政策文件。
来源：搜狗新闻搜索（主）/ Bing 搜索（降级）命中的公开财经媒体与官网。

单次关键词搜索覆盖面不足，本采集器实现迭代式 Deep Research：
1. 初始查询：根据目标公司与板块生成核心搜索查询；
2. 结果分析：提取关键实体、概念与新问题；
3. 查询精炼与扩展：LLM 基于新信息生成更具体深入的查询（失败降级规则法）；
4. 循环与终止：重复直到信息饱和或达到搜索深度上限（max_depth=3）。

双保险防不收敛：max_depth 深度上限 + max_items 总条数上限；
信息饱和判断：本轮新增去重条目数 < 阈值即终止。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

SOGOU_NEWS_URL = "https://news.sogou.com/news"
SINA_ROLL_URL = "https://feed.mix.sina.com.cn/api/roll/get"
BING_SEARCH_URL = "https://www.bing.com/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


@dataclass
class NewsItem:
    """新闻条目"""
    title: str
    source: str          # 来源媒体
    url: str             # 原文链接（用于溯源）
    date: str
    summary: str = ""
    sentiment: str = ""  # positive / neutral / negative（Day 3 情绪分析补充）

    def to_dict(self) -> dict:
        return {
            "title": self.title, "source": self.source, "url": self.url,
            "date": self.date, "summary": self.summary,
            "sentiment": self.sentiment,
        }


@dataclass
class NewsData:
    """新闻数据"""
    keyword: str
    collected_at: str = ""           # 采集时刻（溯源）
    depth_executed: int = 0          # 实际执行迭代轮数
    search_trace: List[dict] = field(default_factory=list)  # 每轮查询留痕
    items: List[NewsItem] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "collected_at": self.collected_at,
            "count": len(self.items),
            "depth_executed": self.depth_executed,
            "search_trace": self.search_trace,
            "items": [i.to_dict() for i in self.items],
        }


class NewsCollector:
    """新闻与政策采集器（迭代式 Deep Research）"""

    # 权威来源白名单（CitationChecker 同样引用）
    RELIABLE_SOURCES = [
        "新浪财经", "财联社", "证券时报", "上海证券报",
        "中国证券报", "经济日报", "人民日报", "新华网",
        "人民网", "央视", "21世纪经济报道", "每日经济新闻",
        "界面新闻", "澎湃新闻", "新华社", "中国政府网",
        "证监会", "央行", "财政部", "发改委",
    ]

    # 规则降级法的深挖维度
    REFINE_DIMENSIONS = ["市场份额", "最新政策", "竞争对手", "业绩展望"]

    # 非新闻页面过滤（官网/百科/行情页等对新闻研究无价值）
    NOISE_URL_RE = re.compile(
        r"baike\.baidu|wikipedia|moutaichina\.com|moutai\.com\.cn"
        r"|quote\.eastmoney|quote\.hexun|guba\.eastmoney"
        r"|xueqiu\.com/?|realstock/company|stockpage"
        r"|\.gov\.cn/?$|\.com\.cn/?$|\.cn/?$", re.I)

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.max_items = int(self.config.get("max_items", 60))
        self.min_new_per_round = int(self.config.get("min_new_per_round", 2))
        self.strict_source = bool(self.config.get("strict_source", False))
        self.proxy = self.config.get("proxy")  # Bing 走代理；None 时自动探测
        # 单关键词 Deep Research 整体时间预算（秒）：反爬退避累计可能很长，
        # 批量跑 49 家标的时必须兜底，防止单个关键词拖垮全流程
        self.time_budget = float(self.config.get("time_budget", 120))
        self._llm = None
        self._llm_init = False
        # 已执行查询（全轮去重，防止精炼查询绕圈）
        self._executed: set = set()

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def collect(self, keyword: str, limit: int = 20,
                max_depth: int = 3) -> NewsData:
        """迭代式 Deep Research 采集：搜索→分析→精炼→再搜，直到信息饱和"""
        data = NewsData(keyword=keyword,
                        collected_at=datetime.now().isoformat(timespec="seconds"))
        self._executed = set()
        seen_urls = set()
        queries = self._initial_queries(keyword)
        deadline = time.monotonic() + self.time_budget

        for depth in range(1, max_depth + 1):
            before = len(data.items)
            round_queries = []
            out_of_budget = False
            for query in queries:
                if time.monotonic() > deadline:
                    out_of_budget = True
                    break
                if query in self._executed:
                    continue
                self._executed.add(query)
                round_queries.append(query)
                for item in self._search_news(query, limit):
                    if len(data.items) >= self.max_items:
                        break
                    url = item.get("url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if self.NOISE_URL_RE.search(url):
                        continue
                    if self._keep(item.get("source", "")):
                        data.items.append(NewsItem(
                            title=_strip_tags(item.get("title", "")),
                            source=item.get("source", ""),
                            url=url,
                            date=item.get("date", ""),
                            summary=_strip_tags(item.get("summary", "")),
                        ))
                if len(data.items) >= self.max_items:
                    break
            newly = len(data.items) - before
            data.search_trace.append({
                "depth": depth, "queries": round_queries,
                "new_items": newly, "total": len(data.items),
                "budget_exceeded": out_of_budget,
            })
            data.depth_executed = depth
            logger.info("Deep Research 第 %d 轮：查询 %d 个，新增 %d 条",
                        depth, len(round_queries), newly)

            if out_of_budget:
                logger.warning(
                    "Deep Research 超出时间预算 %.0fs，提前终止（depth=%d）",
                    self.time_budget, depth)
                break
            if len(data.items) >= self.max_items:
                break
            # 本轮几乎无新增 → 信息饱和，终止
            if newly < self.min_new_per_round:
                logger.info("新增条目 %d < %d，信息饱和，终止迭代",
                            newly, self.min_new_per_round)
                break
            # 基于新信息精炼扩展查询；无新角度则终止
            new_queries = self._refine_queries(
                keyword, round_queries, data.items)
            if not new_queries and depth == 1:
                # 强制至少 2 轮迭代（验收要求）：规则法兜底生成深挖查询
                new_queries = self._refine_by_rules(keyword, data.items)
                if new_queries:
                    logger.info("LLM 判定饱和但未满 2 轮，规则法兜底深挖")
            if not new_queries:
                break
            queries = new_queries
            time.sleep(0.5)  # 温和限速
        return data

    # ------------------------------------------------------------------
    # 查询生成
    # ------------------------------------------------------------------
    def _initial_queries(self, keyword: str) -> List[str]:
        """根据关键词生成初始查询集"""
        return [keyword, f"{keyword} 最新动态", f"{keyword} 政策"]

    def _refine_queries(self, keyword: str, queries: List[str],
                        items: List[NewsItem]) -> List[str]:
        """从已有结果提取新实体/新问题，生成下一轮更深入的查询；
        无新增有价值信息时返回空列表（信息饱和）。
        主路径：LLM 精炼；失败降级：高频实体 × 深挖维度规则法。"""
        llm = self._get_llm()
        if llm is not None:
            try:
                return self._refine_by_llm(llm, keyword, queries, items)
            except Exception as e:
                logger.warning("LLM 查询精炼失败，降级规则法: %s", e)
        return self._refine_by_rules(keyword, items)

    def _refine_by_llm(self, llm, keyword: str,
                       queries: List[str], items: List[NewsItem]) -> List[str]:
        titles = "\n".join(
            f"{i + 1}. {it.title}" for i, it in enumerate(items[-12:]))
        prompt = (
            f"你是金融新闻研究员。当前研究主题：「{keyword}」。\n"
            f"已执行搜索查询：{'; '.join(sorted(self._executed))}\n"
            f"最新采集到的新闻标题：\n{titles}\n\n"
            "请提取与已执行查询不重叠的新研究角度（如新兴业务、竞争对手、"
            "市场份额、行业政策、供应链、风险提示等），生成 1-3 个更具体"
            f"深入的后续搜索查询。要求：每个查询以「{keyword}」开头，"
            "只拼接 1-2 个具体的业务/事件/政策关键词（≤ 15 字），"
            "禁止使用网站名、百科、走势等噪声词。"
            "若信息已饱和（没有新的高价值角度），返回空数组。\n"
            '严格输出 JSON 数组，例如 ["贵州茅台 i茅台 市场份额"]。')
        result = llm.chat_json(prompt, max_tokens=512, temperature=0.2)
        if not isinstance(result, list):
            return []
        refined = [str(q).strip() for q in result
                   if str(q).strip() and str(q).strip() not in self._executed]
        return refined[:3]

    def _refine_by_rules(self, keyword: str,
                         items: List[NewsItem]) -> List[str]:
        """规则降级：从标题抽取高频新实体，组合深挖维度生成查询"""
        stop = set(self._executed)
        counter: dict = {}
        for it in items:
            for w in re.findall(r"[\u4e00-\u9fa5]{2,8}", it.title):
                if keyword in w or w in keyword:
                    continue
                counter[w] = counter.get(w, 0) + 1
        entities = [w for w, c in sorted(counter.items(),
                                         key=lambda x: -x[1]) if c >= 2][:3]
        refined = []
        for ent in entities:
            for dim in self.REFINE_DIMENSIONS:
                q = f"{keyword} {ent} {dim}"
                if q not in stop:
                    refined.append(q)
                    break
        return refined[:3]

    # ------------------------------------------------------------------
    # 搜索实现（搜狗主 → Bing 降级）
    # ------------------------------------------------------------------
    def _search_news(self, keyword: str, limit: int) -> List[dict]:
        """检索新闻（公开渠道），逐源降级，全部失败返回空列表"""
        for fetch, name in ((self._search_sogou, "搜狗新闻搜索"),
                            (self._search_sina_roll, "新浪滚动财经(关键词过滤)"),
                            (self._search_bing, "Bing 搜索")):
            try:
                items = fetch(keyword, limit)
                if items:
                    logger.info("%s 命中 %d 条（query=%s）",
                                name, len(items), keyword)
                    return items
            except Exception as e:
                logger.warning("%s 失败（query=%s）: %s", name, keyword, e)
        return []

    def _search_sogou(self, keyword: str, limit: int) -> List[dict]:
        """搜狗新闻搜索：直连境内，HTML 解析；结果波动时自动重试"""
        import requests

        session = requests.Session()
        session.trust_env = False
        html = ""
        for attempt in range(4):
            resp = session.get(
                SOGOU_NEWS_URL, params={"query": keyword, "sort": "1"},
                headers={"User-Agent": UA}, timeout=15)
            resp.raise_for_status()
            html = resp.text
            if "vr-title" in html:
                break
            if attempt < 3:  # 最后一次失败后不再空等（最坏省 16s）
                time.sleep(4 * (attempt + 1))  # 反爬限频退避

        items = []
        blocks = list(re.finditer(
            r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>', html))
        for idx, m in enumerate(blocks[:limit]):
            end = blocks[idx + 1].start() if idx + 1 < len(blocks) \
                else m.start() + 4000
            seg = html[m.start():end]
            a = re.search(
                r'<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', seg)
            if not a:
                continue
            url, title = a.group(1), _strip_tags(a.group(2))
            if not title:
                continue
            if url.startswith("/link?"):  # 搜狗跳转链，补全域名（可直达原文）
                url = "https://news.sogou.com" + url
            # 来源与时间：fz-mid 块（"来源 时间"格式，可能粘连无空格）
            source, date, summary = "", "", ""
            fz = re.search(r'<div[^>]*class="[^"]*fz-mid[^"]*"[^>]*>'
                           r'([\s\S]*?)</div>', seg)
            if fz:
                text = _strip_tags(fz.group(1)).strip()
                m = re.search(
                    r'(.*?)\s*'
                    r'(\d{4}年\d{1,2}月\d{1,2}日|\d+小时前|\d+分钟前'
                    r'|\d{4}-\d{2}-\d{2}[\s\d:]*)$', text)
                if m:
                    source, date = m.group(1), m.group(2)
                else:
                    source = text
            # 摘要：f-text 块（若有）
            ft = re.search(r'<[^>]*class="[^"]*f-text[^"]*"[^>]*>'
                           r'([\s\S]*?)</(?:div|p|span)>', seg)
            if ft:
                summary = _strip_tags(ft.group(1))
            items.append({"title": title, "url": url,
                          "source": source, "date": date,
                          "summary": summary})
        return items

    def _search_sina_roll(self, keyword: str, limit: int) -> List[dict]:
        """新浪滚动财经新闻：结构化 JSON，拉取多页后按关键词过滤标题/摘要"""
        import requests
        from datetime import datetime

        session = requests.Session()
        session.trust_env = False
        # 匹配词：完整关键词 + 末尾核心词（如"贵州茅台"→"茅台"）
        matchers = {keyword}
        if len(keyword) >= 4:
            matchers.add(keyword[-2:])
        items = []
        pages = int(self.config.get("sina_roll_pages", 6))
        lids = self.config.get("sina_roll_lids", (2516, 2517))  # 财经/股票频道
        for lid in lids:
            for page in range(1, pages + 1):
                resp = session.get(
                    SINA_ROLL_URL,
                    params={"pageid": 153, "lid": lid, "k": "",
                            "num": 50, "page": page},
                    headers={"User-Agent": UA}, timeout=15)
                resp.raise_for_status()
                data = (resp.json().get("result") or {}).get("data") or []
                if not data:
                    break
                for it in data:
                    title = it.get("title", "")
                    text = title + (it.get("summary") or it.get("intro") or "")
                    if any(m in text for m in matchers):
                        ctime = it.get("ctime")
                        date = datetime.fromtimestamp(int(ctime)).strftime(
                            "%Y-%m-%d %H:%M") if str(ctime).isdigit() else ""
                        items.append({
                            "title": title,
                            "url": it.get("url", ""),
                            "source": it.get("media_name", "新浪财经"),
                            "date": date,
                            "summary": it.get("summary") or it.get("intro", ""),
                        })
                        if len(items) >= limit:
                            return items
        return items

    def _search_bing(self, keyword: str, limit: int) -> List[dict]:
        """Bing 搜索：需代理，HTML 解析 b_algo 结果块"""
        import requests

        proxy = self.proxy or self._detect_proxy()
        if not proxy:
            logger.info("无可用代理，跳过 Bing 搜索")
            return []
        session = requests.Session()
        session.trust_env = False
        resp = session.get(
            BING_SEARCH_URL, params={"q": keyword, "count": limit},
            headers={"User-Agent": UA},
            proxies={"http": proxy, "https": proxy}, timeout=15)
        resp.raise_for_status()

        items = []
        # 相关性过滤：通用网页搜索结果标题须含关键词/核心词，阻断无关噪声
        matchers = {keyword} | ({keyword[-2:]} if len(keyword) >= 4 else set())
        for block in re.findall(
                r'<li class="b_algo"[\s\S]*?</li>', resp.text)[:limit * 2]:
            a = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>'
                          r'([\s\S]*?)</a>', block)
            if not a:
                continue
            url, title = a.group(1), _strip_tags(a.group(2))
            if not any(m in title for m in matchers):
                continue
            cite = re.search(r'<cite[^>]*>([\s\S]*?)</cite>', block)
            p = re.search(r'<p[^>]*>([\s\S]*?)</p>', block)
            items.append({
                "title": title, "url": url,
                "source": _strip_tags(cite.group(1)) if cite else "",
                "summary": _strip_tags(p.group(1)) if p else "",
                "date": "",
            })
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _detect_proxy() -> str:
        """探测 Windows 系统代理（境外站点访问用）"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Internet Settings")
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if enable and server:
                return f"http://{server}"
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # 来源可信度
    # ------------------------------------------------------------------
    def _keep(self, source: str) -> bool:
        """strict_source=True 时仅保留白名单来源；默认全部保留并标注来源"""
        if not self.strict_source:
            return True
        return self._is_reliable(source)

    def _is_reliable(self, source: str) -> bool:
        return any(s in source for s in self.RELIABLE_SOURCES)

    def _get_llm(self):
        """惰性初始化 LLM 客户端（无 Key 时返回 None，走规则降级）"""
        if not self._llm_init:
            self._llm_init = True
            try:
                from common.llm_client import LLMClient
            except ImportError:
                # 兼容包导入/直跑：按绝对路径定位技能内 common 目录
                import os
                import sys
                _common = os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))
                if _common not in sys.path:
                    sys.path.insert(0, _common)
                from common.llm_client import LLMClient
            llm = LLMClient(self.config.get("llm"))
            if llm.api_key:
                self._llm = llm
        return self._llm
