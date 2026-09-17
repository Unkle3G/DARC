"""Renderings filled in by the operator's session go through the same gate."""
from __future__ import annotations

import json
from dataclasses import replace

from liver_intel import pipeline, worksheet
from liver_intel.models import Item, Quote


def make(title="Phase 3 trial met the primary endpoint"):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company"}, lines=["L3"], P="P0")
    item.evidence.quotes = [
        Quote(text="met the primary endpoint", locator="tag:ENDPOINT_MET"),
        Quote(text="TERMINATED", locator="ClinicalTrials.gov · overallStatus"),
        Quote(text="该试验达到主要终点")]
    return item


def test_worksheet_lists_only_what_needs_a_rendering(tmp_path):
    path = tmp_path / "w.json"
    assert worksheet.write([make()], path, "2026-09-14") == 3
    sheet = json.loads(path.read_text(encoding="utf-8"))
    kinds = [(e["kind"], e["text"]) for e in sheet["entries"]]
    assert kinds == [("title", "Phase 3 trial met the primary endpoint"),
                     ("quote", "met the primary endpoint"),
                     ("field", "TERMINATED")]
    assert all(e["zh"] == "" for e in sheet["entries"])
    assert any("不加背景" in rule for rule in sheet["rules"])


def test_filled_worksheet_is_applied_through_the_acceptance_checks(tmp_path):
    item = make()
    path = tmp_path / "w.json"
    worksheet.write([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["entries"][0]["zh"] = "III期试验达到主要终点"
    sheet["entries"][1]["zh"] = "达到主要终点，重磅利好"      # evaluative -> rejected
    sheet["entries"][2]["zh"] = "已终止"
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")

    accepted, rejected, reasons = worksheet.apply([item], path)
    assert (accepted, rejected) == (2, 1)
    assert item.meta["title_zh"] == "III期试验达到主要终点"
    assert item.evidence.quotes[0].translation == ""
    assert item.evidence.quotes[1].translation == "已终止"
    assert reasons and "quote:0" in reasons[0]


def test_unfilled_slots_leave_the_original_alone(tmp_path):
    item = make()
    path = tmp_path / "w.json"
    worksheet.write([item], path, "2026-09-14")
    assert worksheet.apply([item], path) == (0, 0, [])
    assert "title_zh" not in item.meta


def test_a_slot_whose_source_text_was_edited_is_rejected(tmp_path):
    item = make()
    path = tmp_path / "w.json"
    worksheet.write([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["entries"][0]["text"] = "something else"
    sheet["entries"][0]["zh"] = "别的东西"
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    assert worksheet.apply([item], path)[:2] == (0, 1)


def test_render_rebuilds_the_reports_from_the_worksheet(tmp_path, monkeypatch, domain_map):
    settings = replace(pipeline.Settings(), out_dir=tmp_path, db_path=tmp_path / "s.sqlite3")
    item = make()
    result = pipeline.RunResult(report_date="2026-09-14", daily=[item], weekly=[None, None])
    pipeline._write_outputs(settings, result, domain_map, wechat=True)
    sheet_path = tmp_path / "liver_daily_2026-09-14_renderings.json"
    worksheet.write([item], sheet_path, "2026-09-14")
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    for entry in sheet["entries"]:
        entry["zh"] = {"title": "III期试验达到主要终点", "quote": "达到主要终点",
                       "field": "已终止"}[entry["kind"]]
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")

    rendered = pipeline.render(settings, "2026-09-14")
    assert "renderings from worksheet: 3 accepted, 0 rejected" in rendered.notes
    article = rendered.wechat_path.read_text(encoding="utf-8")
    assert "Phase 3 trial met the primary endpoint</h2>" in article
    assert article.index("primary endpoint</h2>") < article.index("III期试验达到主要终点")
    assert "｜已终止" in article
    assert "达到主要终点" in rendered.report_path.read_text(encoding="utf-8")
    # the JSON carries the renderings too, so a second render is idempotent
    again = pipeline.render(settings, "2026-09-14")
    assert "renderings still missing" not in " ".join(again.notes)


def test_supplementary_quote_must_exist_verbatim_in_the_source(tmp_path):
    item = make()
    item.meta["body"] = ("The Board announces that TQB6426, a GPC3 antibody-drug conjugate, "
                         "has received IND approval. GPC3 is highly expressed in HCC.")
    path = tmp_path / "w.json"
    worksheet.write([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    assert sheet["documents"][0]["source_text"].startswith("The Board announces")
    sheet["documents"][0]["supplementary"] = [
        {"text": "GPC3 is highly expressed in HCC.", "zh": "GPC3 在 HCC 中高表达。"},
        {"text": "GPC3 is a promising target.", "zh": "GPC3 是有前景的靶点。"},   # not in source
    ]
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    accepted, rejected, reasons = worksheet.apply([item], path)
    assert (accepted, rejected) == (1, 1)
    added = item.evidence.quotes[-1]
    assert added.text == "GPC3 is highly expressed in HCC." and added.locator == "supplementary"
    assert added.translation == "GPC3 在 HCC 中高表达。"
    assert "not found verbatim" in reasons[0]
