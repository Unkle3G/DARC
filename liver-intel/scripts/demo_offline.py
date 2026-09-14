#!/usr/bin/env python3
"""Run the whole pipeline against canned documents, with no network.

Useful for checking the report layout and the selection rules without waiting
for feed verification. Everything it prints comes from the real pipeline; only
the HTTP layer is replaced.

    python3 scripts/demo_offline.py
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liver_intel import pipeline                      # noqa: E402
from liver_intel.domain_map import load as load_domain_map  # noqa: E402
from liver_intel.feeds import Feed, Registry          # noqa: E402
from liver_intel.http import Response                 # noqa: E402
from liver_intel.llm import NullJudge                 # noqa: E402
from liver_intel.sources.base import Context          # noqa: E402

WIRE = "https://wire.example.com/rss"

#: Fabricated documents. The company names are real, the news is not -- this
#: exercises the pipeline, it is not publishable copy, and the WeChat article it
#: produces is stamped accordingly.
DOCS = [
    ("a", "Madrigal Announces Phase 3 Topline Results in MASH",
     "Madrigal Pharmaceuticals today announced that the Phase 3 MAESTRO-NASH trial "
     "met the primary endpoint of MASH resolution on liver biopsy in patients with "
     "F2-F3 fibrosis."),
    ("b", "腾盛博药乙肝功能性治愈方案III期达到主要终点",
     "腾盛博药宣布，其慢性乙型肝炎功能性治愈联合方案III期临床研究达到主要终点，"
     "受试者表面抗原清除率较对照组提高。"),
    ("c", "Mirum receives FDA approval in primary biliary cholangitis",
     "Mirum Pharmaceuticals announced that the FDA approved its therapy for "
     "primary biliary cholangitis."),
    ("d", "Akero reports Phase 2b MASH results",
     "Akero Therapeutics reported that the Phase 2b study did not meet the primary "
     "endpoint of fibrosis improvement."),
    ("e", "Ascletis reports hepatitis C cohort data",
     "Ascletis reported Phase 2 results in chronic hepatitis C with sustained "
     "virologic response."),
    ("f", "An AI model scores fibrosis on digital pathology",
     "A machine learning model scored fibrosis stage on digital pathology slides."),
    ("g", "Company to present at an investor conference",
     "The company will present at an investor conference next month."),
]

#: One document carries figures so the article renderer has something to lay out.
FIGURES = {
    "a": ('<meta property="og:image" content="/media/maestro-hero.jpg">',
          '<img src="/media/kaplan-meier.png" width="900" height="600" '
          'alt="MASH resolution by treatment arm"/>'
          '<img src="/media/logo.png" width="300" height="120"/>'),
}

RSS_ITEMS = "".join(
    f"<item><title>{title}</title>"
    f"<link>https://www.globenewswire.com/{slug}</link>"
    f"<pubDate>Mon, 14 Sep 2026 0{i}:00:00 GMT</pubDate></item>"
    for i, (slug, title, _) in enumerate(DOCS, start=1)
)
FEED_BODY = f"<rss><channel>{RSS_ITEMS}</channel></rss>"


class CannedFetcher:
    def __init__(self):
        self.routes = {WIRE: FEED_BODY}
        for slug, title, body in DOCS:
            head, figures = FIGURES.get(slug, ("", ""))
            self.routes[f"https://www.globenewswire.com/{slug}"] = (
                f"<html><head>{head}</head><body><p>{body}</p>{figures}</body></html>")

    def get(self, url, headers=None, etag=None, last_modified=None, allow_304=True):
        if url not in self.routes:
            raise RuntimeError(f"no canned document for {url}")
        return Response(url, 200, self.routes[url], {})


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="liver-intel-demo-"))
    settings = replace(pipeline.Settings(), out_dir=tmp / "out",
                       db_path=tmp / "state.sqlite3")
    fetcher = CannedFetcher()
    registry = Registry(entries=[Feed(id="demo.wire", url=WIRE, source="newswire",
                                      task="T1", status="verified")])
    domain_map = load_domain_map()

    pipeline.build_context = (
        lambda s, store, today, since=None, domain_map_=None:
        Context(settings=s, fetcher=fetcher, store=store, registry=registry,
                domain_map=domain_map, today=today, since=since))
    pipeline.llm.build_judge = lambda enabled=True: NullJudge()

    daily = pipeline.run_daily(settings, today="2026-09-14", only=["newswire"],
                               wechat=True)
    print(f"collected {daily.collected} | daily {len(daily.daily)} {daily.counts} "
          f"| weekly pool +{len(daily.weekly)}\n")
    print(daily.report_path.read_text(encoding="utf-8"))

    weekly = pipeline.run_weekly(settings, today="2026-09-18")
    print("=" * 72)
    print(weekly.report_path.read_text(encoding="utf-8"))

    if daily.wechat_path:
        # Re-render with the demo watermark: the copy above is fabricated and
        # must not be mistaken for something ready to publish.
        from liver_intel import report_wechat

        article = report_wechat.wechat_html(
            daily.daily, "2026-09-14", domain_map, notes=daily.notes,
            weekly_pool_size=len(daily.weekly),
            watermark="演示数据：以下稿件内容为测试用虚构文本，公司名称真实但事件不实，"
                      "仅用于检查排版与流水线，切勿发布。")
        daily.wechat_path.write_text(article, encoding="utf-8")
        print("=" * 72)
        print(f"WeChat article: {daily.wechat_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
