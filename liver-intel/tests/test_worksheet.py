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
    sheet["entries"][0]["zh"] = "Phase 3 试验达到主要终点"
    sheet["entries"][1]["zh"] = "达到主要终点，重磅利好"      # evaluative -> rejected
    sheet["entries"][2]["zh"] = "已终止"
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")

    accepted, rejected, reasons = worksheet.apply([item], path)
    assert (accepted, rejected) == (2, 1)
    assert item.meta["title_zh"] == "Phase 3 试验达到主要终点"
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
        entry["zh"] = {"title": "Phase 3 试验达到主要终点", "quote": "达到主要终点",
                       "field": "已终止"}[entry["kind"]]
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")

    rendered = pipeline.render(settings, "2026-09-14")
    assert "renderings from worksheet: 3 accepted, 0 rejected" in rendered.notes
    article = rendered.wechat_path.read_text(encoding="utf-8")
    assert "Phase 3 trial met the primary endpoint</h2>" in article
    assert article.index("primary endpoint</h2>") < article.index("Phase 3 试验达到主要终点")
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


def test_phase_notation_is_never_offered_for_rendering(tmp_path):
    item = make()
    item.evidence.quotes.append(Quote(text="PHASE3", locator="ClinicalTrials.gov · phases"))
    path = tmp_path / "w.json"
    worksheet.write([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    assert all(e["text"] != "PHASE3" for e in sheet["entries"])


# --- the judgement worksheet ----------------------------------------------
def judged(title="Madrigal reports Phase 3 MASH topline", body=None):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", lines=["L3"], P="P3",
                meta={"src_kind": "company",
                      "quotable": body or (title + "\nThe MAESTRO-NASH trial met the "
                                           "primary endpoint of MASH resolution.")})
    return item


def test_the_judgement_worksheet_offers_the_vocabulary_and_the_source(tmp_path):
    item = judged()
    path = tmp_path / "j.json"
    assert worksheet.write_judgement([item], path, "2026-09-14") == 1
    sheet = json.loads(path.read_text(encoding="utf-8"))
    entry = sheet["entries"][0]
    assert entry["item"] == item.key and entry["signals"] == [] and entry["quotes"] == []
    assert "met the primary endpoint" in entry["source_text"]
    assert "PH3_RESULT" in sheet["signals"]           # the closed vocabulary travels
    assert any("逐字" in rule for rule in sheet["rules"])


def test_a_judged_signal_needs_a_verbatim_quote(tmp_path):
    item = judged()
    path = tmp_path / "j.json"
    worksheet.write_judgement([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["entries"][0]["signals"] = ["PH3_RESULT", "REG_APPROVAL"]
    sheet["entries"][0]["quotes"] = [
        {"signal": "PH3_RESULT",
         "text": "The MAESTRO-NASH trial met the primary endpoint of MASH resolution.",
         "zh": "MAESTRO-NASH 试验达到 MASH 缓解的主要终点。"},
        {"signal": "REG_APPROVAL", "text": "The FDA approved it.", "zh": "FDA 已批准。"},
    ]
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")

    applied, rejected, reasons = worksheet.apply_judgement([item], path)
    assert applied == 1 and rejected >= 1
    assert "PH3_RESULT" in item.evidence.signals
    assert "REG_APPROVAL" not in item.evidence.signals      # its quote is not in the source
    assert any("逐字" in r for r in reasons)
    added = item.evidence.quotes[-1]
    assert added.locator == "判定:PH3_RESULT"
    assert added.translation == "MAESTRO-NASH 试验达到 MASH 缓解的主要终点。"


def test_a_signal_outside_the_vocabulary_is_dropped(tmp_path):
    item = judged()
    path = tmp_path / "j.json"
    worksheet.write_judgement([item], path, "2026-09-14")
    sheet = json.loads(path.read_text(encoding="utf-8"))
    sheet["entries"][0]["quotes"] = [
        {"signal": "VERY_IMPORTANT",
         "text": "The MAESTRO-NASH trial met the primary endpoint of MASH resolution."}]
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    applied, rejected, reasons = worksheet.apply_judgement([item], path)
    assert (applied, rejected) == (0, 1) and "词表" in reasons[0]


def test_an_unfilled_judgement_worksheet_changes_nothing(tmp_path):
    item = judged()
    path = tmp_path / "j.json"
    worksheet.write_judgement([item], path, "2026-09-14")
    assert worksheet.apply_judgement([item], path) == (0, 0, [])
    assert item.evidence.signals == []


def test_judge_regrades_and_reselects_the_day(tmp_path, domain_map, monkeypatch):
    from dataclasses import replace as dc_replace
    settings = dc_replace(pipeline.Settings(), out_dir=tmp_path,
                          db_path=tmp_path / "s.sqlite3")
    item = judged()
    (tmp_path / "liver_daily_2026-09-14_candidates.json").write_text(
        json.dumps([item.to_json()], ensure_ascii=False), encoding="utf-8")
    sheet = tmp_path / "liver_daily_2026-09-14_judgement.json"
    worksheet.write_judgement([item], sheet, "2026-09-14")
    filled = json.loads(sheet.read_text(encoding="utf-8"))
    filled["entries"][0]["signals"] = ["PH3_RESULT"]
    filled["entries"][0]["quotes"] = [
        {"signal": "PH3_RESULT",
         "text": "The MAESTRO-NASH trial met the primary endpoint of MASH resolution.",
         "zh": "MAESTRO-NASH 试验达到 MASH 缓解的主要终点。"}]
    sheet.write_text(json.dumps(filled, ensure_ascii=False), encoding="utf-8")

    result = pipeline.judge(settings, "2026-09-14")
    # the rules had it at P3; the judgement moves it into the day's report
    assert [i.P for i in result.daily] == ["P0"]
    assert "PH3_RESULT" in result.daily[0].evidence.signals
    assert any("判定来自工作单：1 条信号采纳" in n for n in result.notes)
    assert result.report_path.exists()


def test_retag_reads_the_stored_text_with_the_current_rules(tmp_path, domain_map):
    """A day collected before a rule was fixed can be reissued under it.
    Grading already runs on the current rules, so without this a reissue mixes
    new grading with the tags the day happened to be collected under -- which
    is how a cohort study in a roster journal stayed at P3 after its journal
    was added to the roster."""
    from dataclasses import replace as dc_replace

    settings = dc_replace(pipeline.Settings(), out_dir=tmp_path,
                          db_path=tmp_path / "s.sqlite3")
    body = ("Madrigal reports Phase 3 MASH topline\n"
            "The MAESTRO-NASH trial met the primary endpoint of MASH resolution.")
    item = Item(src="newswire", title="Madrigal reports Phase 3 MASH topline",
                url="https://www.example.com/a", date="2026-09-14", lines=["L3"],
                P="P3", meta={"src_kind": "company", "body": body, "quotable": body})
    (tmp_path / "liver_daily_2026-09-14_candidates.json").write_text(
        json.dumps([item.to_json()], ensure_ascii=False), encoding="utf-8")
    sheet = tmp_path / "liver_daily_2026-09-14_judgement.json"
    worksheet.write_judgement([item], sheet, "2026-09-14")

    # Stored with no tags at all; the tagger reads them back off the body.
    assert item.study == []
    plain = pipeline.judge(settings, "2026-09-14")
    assert plain.daily == []

    retagged = pipeline.judge(settings, "2026-09-14", retag=True)
    assert [i.P for i in retagged.daily] == ["P0"]
    assert "PH3_RESULT" in retagged.daily[0].evidence.signals
    assert any("已用当前规则重新打标" in n for n in retagged.notes)


def test_retag_never_drops_a_tag_a_source_wrote_itself(tmp_path, domain_map):
    """ctgov writes the registry's phases into ``study``, pubmed writes
    PUBLICATION, regulator writes APPROVAL -- none of it is re-derivable from
    the text. The re-tag unions, so those survive."""
    from dataclasses import replace as dc_replace

    settings = dc_replace(pipeline.Settings(), out_dir=tmp_path,
                          db_path=tmp_path / "s.sqlite3")
    item = Item(src="ctgov", title="A study of something in cirrhosis",
                url="https://www.example.com/a", date="2026-09-14",
                study=["PHASE2", "RECRUITING"], lines=["L5"], P="P3",
                meta={"src_kind": "registry", "body": "A study of something in cirrhosis"})
    (tmp_path / "liver_daily_2026-09-14_candidates.json").write_text(
        json.dumps([item.to_json()], ensure_ascii=False), encoding="utf-8")
    worksheet.write_judgement(
        [item], tmp_path / "liver_daily_2026-09-14_judgement.json", "2026-09-14")

    result = pipeline.judge(settings, "2026-09-14", retag=True)
    kept = {tag for i in result.daily + result.weekly for tag in i.study}
    assert {"PHASE2", "RECRUITING"} <= kept


def test_judging_twice_does_not_stack_the_notes(tmp_path, domain_map):
    """A second pass re-derives every note it owns, so the first pass's copies
    have to go. The 第004期 reissue printed the first pass's "17 处待填" right
    above the second's "20 处待填"."""
    from dataclasses import replace as dc_replace

    settings = dc_replace(pipeline.Settings(), out_dir=tmp_path,
                          db_path=tmp_path / "s.sqlite3")
    item = judged()
    (tmp_path / "liver_daily_2026-09-14_candidates.json").write_text(
        json.dumps([item.to_json()], ensure_ascii=False), encoding="utf-8")
    sheet = tmp_path / "liver_daily_2026-09-14_judgement.json"
    worksheet.write_judgement([item], sheet, "2026-09-14")
    filled = json.loads(sheet.read_text(encoding="utf-8"))
    # A signal the guards reject, so a per-item reason is produced too.
    filled["entries"][0]["signals"] = ["PH3_RESULT"]
    filled["entries"][0]["quotes"] = [
        {"signal": "PH3_RESULT", "text": "not in the source text at all", "zh": ""}]
    sheet.write_text(json.dumps(filled, ensure_ascii=False), encoding="utf-8")

    first = pipeline.judge(settings, "2026-09-14")
    second = pipeline.judge(settings, "2026-09-14", retag=True)
    for prefix in ("判定来自工作单", "译文工作单"):
        assert sum(n.startswith(prefix) for n in second.notes) <= 1, prefix
    assert sum(" 判定：" in n for n in second.notes) \
        == sum(" 判定：" in n for n in first.notes)
