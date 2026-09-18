import pytest

from liver_intel import grade
from liver_intel.models import Item
from liver_intel.tagger import Tagger


@pytest.fixture
def tagger(domain_map):
    return Tagger(domain_map)


def item(title, src_kind="company", **meta):
    return Item(src="s", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": src_kind, **meta})


# --- layer 1: lines -------------------------------------------------------
def test_hcc_fires_on_an_unambiguous_name(tagger):
    lines, gated = tagger.tag_lines("Phase 3 study in hepatocellular carcinoma after TACE")
    assert "L6" in lines and not gated


def test_hcc_gate_blocks_a_pan_tumour_namedrop(tagger):
    lines, gated = tagger.tag_lines(
        "Our PD-1 antibody is studied in NSCLC, RCC, HCC and melanoma")
    assert "L6" not in lines
    assert "L6" in gated


def test_hcc_abbreviation_passes_with_liver_context(tagger):
    lines, _ = tagger.tag_lines("Second-line HCC therapy in Child-Pugh A cirrhosis")
    assert "L6" in lines


def test_cirrhosis_fallback(tagger):
    lines, _ = tagger.tag_lines("Patient with compensated cirrhosis and ascites")
    assert "L5" in lines


def test_chinese_phase_three_does_not_fire_phase_one(tagger):
    study = tagger.tag_study("该方案III期临床达到主要终点")
    assert "PHASE3" in study
    assert "PHASE1" not in study and "PHASE2" not in study


def test_unverified_kols_are_not_matched(domain_map):
    conservative = Tagger(domain_map)
    permissive = Tagger(domain_map, use_unverified_kols=True)
    text = "A study presented by PLACEHOLDER-CN-1 and Rohit Loomba"
    assert conservative.tag_text(text).kols == ["Rohit Loomba"]
    assert "PLACEHOLDER-CN-1" in permissive.tag_text(text).kols


# --- grading --------------------------------------------------------------
def test_phase3_readout_is_p0(tagger, domain_map):
    i = tagger.apply(item("Phase 3 MASH trial met the primary endpoint on liver biopsy"))
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert result.P == "P0" and "PH3_RESULT" in result.signals


def test_phase3_termination_with_reason_is_p0(tagger, domain_map):
    i = tagger.apply(item("Phase 3 cirrhosis trial terminated", src_kind="registry",
                          why_stopped="sponsor decision after futility analysis"))
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert result.P == "P0" and "PH3_STOPPED" in result.signals


def test_phase3_termination_without_reason_is_not_p0(tagger, domain_map):
    i = tagger.apply(item("Phase 3 cirrhosis trial terminated", src_kind="registry"))
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "PH3_STOPPED" not in result.signals


def test_phase2_readout_is_p1(tagger, domain_map):
    i = tagger.apply(item("Phase 2b MASH study did not meet the primary endpoint"))
    assert grade.grade(i, domain_map, today="2026-09-14").P == "P1"


def test_auxiliary_only_item_is_capped_to_p2(tagger, domain_map):
    i = tagger.apply(item(
        "Phase 3 hepatitis C trial met the primary endpoint of sustained virologic response"))
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert i.lines == ["L9"]
    assert result.P == "P2"
    assert any("auxiliary-only" in note for note in result.notes)


def test_auxiliary_plus_main_line_keeps_p0(tagger, domain_map):
    i = tagger.apply(item("Phase 3 hepatitis C trial in patients with cirrhosis "
                          "met the primary endpoint"))
    assert grade.grade(i, domain_map, today="2026-09-14").P == "P0"


def test_l13_has_full_weight_but_stays_auxiliary(domain_map):
    assert domain_map.line_weight("L13") == 1.0
    assert not domain_map.lines["L13"].is_main


def test_main_line_outscores_the_same_news_on_an_aux_line(tagger, domain_map):
    main = tagger.apply(item("Phase 3 MASH trial met the primary endpoint"))
    aux = tagger.apply(item("Phase 3 hepatitis C trial met the primary endpoint"))
    grade.apply(main, domain_map, today="2026-09-14")
    grade.apply(aux, domain_map, today="2026-09-14")
    assert main.score > aux.score


def test_why_is_factual_not_evaluative(tagger, domain_map):
    from liver_intel.llm import assert_not_evaluative

    i = tagger.apply(item("Phase 3 MASH trial met the primary endpoint"))
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert_not_evaluative(result.why, "grade.why")


# --- regressions found against live SEC filings ---------------------------
def test_a_board_appointment_is_not_a_phase3_readout(tagger, domain_map):
    """A real Madrigal 8-K announced a board appointment. Its 'About Rezdiffra'
    block mentions a Phase 3 trial and its legal disclaimer mentions regulatory
    approvals; grading the whole document made it a P0."""
    body = (
        "Madrigal Appoints John C. Reed, M.D., Ph.D., to its Board of Directors\n"
        "CONSHOHOCKEN, Pa. - Madrigal today announced the appointment of John C. Reed.\n"
        "About Rezdiffra\n"
        "An ongoing Phase 3 outcomes trial is evaluating Rezdiffra in compensated cirrhosis.\n"
        "Forward-Looking Statements\n"
        "risks related to obtaining and maintaining regulatory approvals, including...")
    i = item("Madrigal Appoints John C. Reed to its Board of Directors", body=body)
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "PH3_RESULT" not in result.signals
    assert "REG_APPROVAL" not in result.signals
    assert result.P != "P0"


def test_future_readout_is_not_an_event(tagger, domain_map):
    """'Topline data from Phase 3 ECLIPSE 1 expected in Q4 2026' is a forecast."""
    body = ("Vir Biotechnology Provides Corporate Update\n"
            "- Topline data from Phase 3 ECLIPSE 1 trial expected in the fourth "
            "quarter of 2026")
    i = item("Vir Biotechnology Provides Corporate Update", body=body)
    tagger.apply(i)
    assert "PH3_RESULT" not in grade.grade(i, domain_map, today="2026-09-14").signals


def test_facts_from_two_sentences_do_not_merge_into_one_signal(tagger, domain_map):
    """A Phase 3 that started plus a Phase 2 that read out is not a Phase 3 readout."""
    body = ("Altimmune Announces Second Quarter 2026 Financial Results\n"
            "Initiated global PERFORMA Phase 3 trial in MASH\n"
            "Reported positive topline data from RECLAIM Phase 2 trial in AUD")
    i = item("Altimmune Announces Second Quarter 2026 Financial Results", body=body)
    tagger.apply(i)
    signals = grade.grade(i, domain_map, today="2026-09-14").signals
    assert "PH3_RESULT" not in signals
    assert "PH2_RESULT" in signals


def test_a_real_phase3_readout_still_fires(tagger, domain_map):
    body = ("Madrigal Announces Positive Topline Results from the Phase 3 "
            "MAESTRO-NASH Trial\nThe Phase 3 trial met the primary endpoint of "
            "MASH resolution on liver biopsy.")
    i = item("Madrigal Announces Positive Topline Results from the Phase 3 MAESTRO-NASH Trial",
             body=body)
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "PH3_RESULT" in result.signals and result.P == "P0"


def test_quarterly_report_is_capped_to_background(tagger, domain_map):
    """The events in a quarter's business update were announced on their own;
    the recap must not be graded as if it broke them."""
    body = ("Mirum Pharmaceuticals Reports Second Quarter 2026 Financial Results\n"
            "Reported positive topline data from the Phase 2 study in PBC")
    i = item("Mirum Pharmaceuticals Reports Second Quarter 2026 Financial Results",
             body=body)
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert result.P == "P2"
    assert any("periodic financial report" in note for note in result.notes)


def test_a_standalone_readout_is_not_capped(tagger, domain_map):
    body = ("Altimmune Announces Positive Topline Results from RECLAIM Phase 2 Trial "
            "in MASH\nThe Phase 2 trial met the primary endpoint of MASH resolution.")
    i = item("Altimmune Announces Positive Topline Results from RECLAIM Phase 2 "
             "Trial in MASH", body=body)
    tagger.apply(i)
    assert grade.grade(i, domain_map, today="2026-09-14").P == "P1"


# --- regressions found against live newswire feeds -------------------------
def test_generic_ai_mention_does_not_fire_the_ai_line(tagger):
    """A wire's general-business feed put 34 releases into the digest because
    every corporate mention of artificial intelligence matched L13."""
    lines, gated = tagger.tag_lines(
        "Eight Rubin Rudman Partners Named to 2026 Lawdragon 500 for their "
        "artificial intelligence practice")
    assert "L13" not in lines and "L13" in gated


def test_ai_line_fires_with_liver_context(tagger):
    lines, _ = tagger.tag_lines(
        "A deep learning model scored fibrosis stage on liver biopsy slides in MASH")
    assert "L13" in lines


def test_generic_microbiome_mention_is_gated(tagger):
    lines, gated = tagger.tag_lines(
        "Kibow Biotech Wins 2026 WebAward for Best Science Website on microbiome research")
    assert "L15" not in lines and "L15" in gated


def test_microbiome_with_liver_context_fires(tagger):
    lines, _ = tagger.tag_lines("Microbiome shifts drive the gut-liver axis in cirrhosis")
    assert "L15" in lines


def test_ind_clearance_is_not_a_marketing_approval(tagger, domain_map):
    """A real HKEX announcement read 'APPLICATION FOR CLINICAL TRIAL ON TQB6426
    GPC3 ADC APPROVED BY FDA' -- permission to start a trial. Reading the word
    approved made it a P0 drug approval."""
    title = ("APPLICATION FOR CLINICAL TRIAL ON TQB6426 GPC3 ADC APPROVED BY FDA "
             "for hepatocellular carcinoma")
    i = item(title, src_kind="filing", body=title)
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "REG_APPROVAL" not in result.signals
    assert "REG_TRIAL_CLEARANCE" in result.signals
    assert result.P == "P2"


def test_a_real_marketing_approval_is_still_p0(tagger, domain_map):
    title = "FDA approved Rezdiffra for the treatment of MASH with liver fibrosis"
    i = item(title, src_kind="regulator", body=title)
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "REG_APPROVAL" in result.signals and result.P == "P0"


def test_a_consensus_conference_is_a_guideline(tagger, domain_map):
    """EASL announced Baveno VIII as a 'Consensus Conference' issuing 'updated
    guidance' -- wording none of the original guideline phrases matched, so the
    most consequential item of the run graded P3."""
    title = ("Baveno VIII Consensus Conference provides updated guidance on advanced "
             "chronic liver disease, portal hypertension and vascular liver disease")
    i = item(title, src_kind="society",
             body=title + "\nExtensively updated set of clinical recommendations "
                          "published in the Journal of Hepatology.")
    tagger.apply(i)
    result = grade.grade(i, domain_map, today="2026-09-14")
    assert "GUIDELINE" in i.study
    assert "GUIDELINE" in result.signals
    assert result.P == "P1"


def test_clinical_guidance_is_not_a_forecast(tagger):
    """To a company 'guidance' forecasts earnings; to a society it is the
    clinical guidance itself. Treating the word as future tense suppressed a
    consensus statement entirely."""
    sentences = tagger.sentence_tags(
        "The guidance comes from the Baveno VIII Consensus Conference.")
    assert sentences and not sentences[0].future


def test_financial_guidance_is_still_a_forecast(tagger):
    sentences = tagger.sentence_tags(
        "The company raised full-year guidance and expects Phase 3 data to read out.")
    assert all(s.future for s in sentences)


def test_an_approved_indication_is_not_a_cleared_trial(tagger):
    """A label reading 'this indication is approved under accelerated approval'
    matched an unbounded `ind`, turning a marketing approval into a cleared
    trial application."""
    assert "TRIAL_CLEARANCE" not in tagger.tag_study(
        "This indication is approved under accelerated approval based on "
        "improvement of MASH.")


def test_registry_scaffolding_is_not_read_as_statements(domain_map):
    """A registry record states its facts structurally; the adapter's own
    "Phase: PHASE3" line must not also fire a trial-progress signal."""
    from liver_intel.grade import rule_signals
    from liver_intel.models import Item
    item = Item(src="ctgov", title="t", url="https://clinicaltrials.gov/study/NCT1",
                date="2026-09-14", study=["PHASE3", "TERMINATED"],
                meta={"src_kind": "registry", "why_stopped": "Business Reasons",
                      "structural_study": ["PHASE3", "TERMINATED"],
                      "sentence_tags": [{"text": "Phase: PHASE3", "tags": ["PHASE3"],
                                         "future": False}]})
    assert rule_signals(item) == ["PH3_STOPPED"]


def test_a_stopped_early_phase_trial_is_a_stop_not_a_result(domain_map):
    from liver_intel.grade import rule_signals
    from liver_intel.models import Item
    item = Item(src="ctgov", title="t", url="https://clinicaltrials.gov/study/NCT1",
                date="2026-09-14", study=["PHASE1", "SUSPENDED"],
                meta={"src_kind": "registry", "why_stopped": "awaiting agreement with Sponsor",
                      "structural_study": ["PHASE1", "SUSPENDED"]})
    assert rule_signals(item) == ["TRIAL_STOPPED"]


def test_prose_fragments_are_not_drug_codes():
    from liver_intel.tagger import Tagger
    found = Tagger._drugs("SEEN ON 09 September with MK-3475 and TQB6426 and EN 09 and AB 12345")
    assert "EN 09" not in found and "ON 09" not in found
    assert {"MK-3475", "TQB6426", "AB 12345"} <= set(found)


def test_short_company_alias_does_not_match_a_numbered_gene_symbol():
    """GSK the company vs. GSK-3β the kinase.

    A phytochemical review that only named glycogen synthase kinase 3 was filed
    under GSK and shipped with "出处：GSK" on it. A numbered suffix is what makes
    a short all-caps alias a gene symbol rather than a company.
    """
    tagger = Tagger()
    for text in ("Modulating GSK 3Β-driven autophagy in liver cancer",
                 "GSK-3β inhibition in hepatocellular carcinoma",
                 "GSK3 beta signalling in cirrhosis"):
        companies, _, _ = tagger.tag_entities(text)
        assert "GSK" not in [c.name for c in companies], text
    companies, _, _ = tagger.tag_entities("GSK reported Phase 3 hepatitis B data")
    assert "GSK" in [c.name for c in companies]
