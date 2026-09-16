# domain_map_liver_v3

> **Reconstruction notice.** The handover pack referenced `domain_map_liver_v3.md`
> but the file was not attached to the implementation session. This file
> rebuilds the structure the engine needs (16 lines, company roster, KOL roster,
> weights) so the pipeline is runnable end to end. **Replace the JSON blocks
> below with the authoritative v3 content before production use** — the parser
> (`liver_intel/domain_map.py`) only cares about the block structure, not about
> these particular terms.
>
> Conventions kept from the brief:
> * `L1`–`L8` are main lines (weight 1.0); `L9`–`L16` are APASL auxiliary lines
>   (weight 0.5), except `L13` (AI) which carries weight 1.0.
> * An auxiliary-only hit never enters the daily report (see `select.py`).
> * `gate` terms are a disease-context requirement: the line only fires when a
>   gate term is also present. `L6` (HCC) uses this to avoid matching general
>   oncology copy; `L5` has a cirrhosis fallback.
> * Any entry marked `"verified": false` must be confirmed by the operator
>   before it is used for matching. Chinese KOL names are pinyin inferences
>   (section 3 of the handover) and are all unverified.

## lines

```json
[
  {"id": "L1", "tier": "main", "name_zh": "乙肝 / 功能性治愈", "name_en": "Chronic hepatitis B",
   "terms_en": ["hepatitis b", "chronic hepatitis b", "hbv", "hbsag", "functional cure",
                "cccdna", "hbeag", "nucleos(t)ide analogue", "siRNA HBV", "capsid assembly modulator"],
   "terms_zh": ["乙型肝炎", "乙肝", "慢乙肝", "表面抗原", "功能性治愈", "核苷类似物", "衣壳组装调节剂"]},

  {"id": "L2", "tier": "main", "name_zh": "丁肝", "name_en": "Hepatitis delta",
   "terms_en": ["hepatitis delta", "hepatitis d", "hdv", "bulevirtide", "delta virus"],
   "terms_zh": ["丁型肝炎", "丁肝", "HDV"]},

  {"id": "L3", "tier": "main", "name_zh": "代谢相关脂肪性肝炎", "name_en": "MASH / MASLD",
   "terms_en": ["mash", "nash", "masld", "nafld", "steatohepatitis", "fatty liver",
                "hepatic steatosis", "resmetirom", "fgf21", "thr-beta", "glp-1 liver"],
   "terms_zh": ["代谢相关脂肪性肝病", "脂肪肝", "脂肪性肝炎", "非酒精性脂肪性肝炎", "MASH", "NASH"]},

  {"id": "L4", "tier": "main", "name_zh": "胆汁淤积性肝病", "name_en": "PBC / PSC / cholestatic",
   "terms_en": ["primary biliary cholangitis", "pbc", "primary sclerosing cholangitis", "psc",
                "cholestatic", "cholestasis", "pruritus cholestatic", "ppar cholangitis",
                "obeticholic", "elafibranor", "seladelpar", "maralixibat", "odevixibat"],
   "terms_zh": ["原发性胆汁性胆管炎", "原发性硬化性胆管炎", "胆汁淤积", "瘙痒"]},

  {"id": "L5", "tier": "main", "name_zh": "肝硬化 / 门脉高压", "name_en": "Cirrhosis & portal hypertension",
   "terms_en": ["cirrhosis", "cirrhotic", "portal hypertension", "varices", "variceal",
                "ascites", "hepatic encephalopathy", "hvpg", "child-pugh", "meld",
                "decompensated liver", "compensated advanced chronic liver disease"],
   "terms_zh": ["肝硬化", "门脉高压", "门静脉高压", "食管胃底静脉曲张", "腹水", "肝性脑病", "失代偿"],
   "fallback_terms_zh": ["代偿期肝硬化", "失代偿期肝硬化"],
   "note": "Cirrhosis fallback per section 3: a bare 肝硬化 / cirrhosis mention is enough to fire L5."},

  {"id": "L6", "tier": "main", "name_zh": "肝细胞癌", "name_en": "Hepatocellular carcinoma",
   "terms_en": ["hepatocellular carcinoma", "liver cancer", "intrahepatic cholangiocarcinoma",
                "barcelona clinic liver cancer"],
   "terms_zh": ["肝细胞癌", "肝癌", "肝内胆管癌"],
   "gated_terms_en": ["hcc", "bclc", "tace", "transarterial chemoembolization"],
   "gated_terms_zh": ["经动脉化疗栓塞"],
   "gate_en": ["liver", "hepat", "cirrhosis", "intrahepatic", "child-pugh"],
   "gate_zh": ["肝"],
   "note": "Disease-word gate per section 3. Unambiguous names fire on their own; the abbreviations (HCC/BCLC/TACE) need liver context elsewhere in the text, so a pan-tumour release that merely lists HCC does not tag L6."},

  {"id": "L7", "tier": "main", "name_zh": "酒精性肝病 / 肝衰竭", "name_en": "ALD & (AC)LF",
   "terms_en": ["alcohol-related liver disease", "alcoholic hepatitis", "ald",
                "acute-on-chronic liver failure", "aclf", "acute liver failure",
                "liver failure", "hepatorenal syndrome"],
   "terms_zh": ["酒精性肝病", "酒精性肝炎", "慢加急性肝衰竭", "肝衰竭", "肝肾综合征", "人工肝"]},

  {"id": "L8", "tier": "main", "name_zh": "肝移植 / 再生", "name_en": "Transplant & regeneration",
   "terms_en": ["liver transplant", "liver transplantation", "graft survival",
                "machine perfusion", "normothermic perfusion", "hepatocyte transplantation",
                "liver regeneration", "bioartificial liver"],
   "terms_zh": ["肝移植", "移植物", "机械灌注", "肝细胞移植", "肝再生", "生物人工肝"]},

  {"id": "L9", "tier": "aux", "name_zh": "丙肝", "name_en": "Hepatitis C",
   "terms_en": ["hepatitis c", "hcv", "direct-acting antiviral", "sofosbuvir", "sustained virologic response"],
   "terms_zh": ["丙型肝炎", "丙肝", "直接抗病毒", "持续病毒学应答"]},

  {"id": "L10", "tier": "aux", "name_zh": "甲肝 / 戊肝", "name_en": "Hepatitis A & E",
   "terms_en": ["hepatitis a", "hepatitis e", "hev", "hav"],
   "terms_zh": ["甲型肝炎", "戊型肝炎", "甲肝", "戊肝"]},

  {"id": "L11", "tier": "aux", "name_zh": "儿童与遗传代谢性肝病", "name_en": "Paediatric & inherited liver disease",
   "terms_en": ["biliary atresia", "wilson disease", "alagille", "progressive familial intrahepatic cholestasis",
                "pfic", "alpha-1 antitrypsin", "urea cycle disorder", "glycogen storage disease"],
   "terms_zh": ["胆道闭锁", "肝豆状核变性", "威尔逊病", "进行性家族性肝内胆汁淤积", "阿拉杰里"]},

  {"id": "L12", "tier": "aux", "name_zh": "自身免疫性肝炎", "name_en": "Autoimmune hepatitis",
   "terms_en": ["autoimmune hepatitis", "aih", "igg4 hepatobiliary"],
   "terms_zh": ["自身免疫性肝炎", "自免肝"]},

  {"id": "L13", "tier": "aux", "weight": 1.0, "name_zh": "人工智能", "name_en": "AI in hepatology",
   "terms_en": [],
   "terms_zh": [],
   "gated_terms_en": ["artificial intelligence", "machine learning", "deep learning",
                      "digital pathology", "ai-based", "algorithm-derived", "foundation model",
                      "computer-aided diagnosis", "radiomics"],
   "gated_terms_zh": ["人工智能", "机器学习", "深度学习", "数字病理", "影像组学", "辅助诊断"],
   "gate_en": ["liver", "hepat", "cirrho", "steato", "mash", "nash", "masld", "nafld", "hbv", "hcv", "fibrosis"],
   "gate_zh": ["肝"],
   "note": "Every AI term here is generic, so all of them are gated: without a liver word in the same document, a corporate release that merely mentions artificial intelligence is not hepatology AI. The line is APASL-auxiliary with full weight (1.0); that weight exception does not lift the main-line requirement for the daily report."},

  {"id": "L14", "tier": "aux", "name_zh": "无创诊断与生物标志物", "name_en": "Non-invasive diagnostics",
   "terms_en": ["fibroscan", "transient elastography", "liver stiffness", "fib-4",
                "mrs-pdff", "pro-c3"],
   "terms_zh": ["瞬时弹性成像", "肝脏硬度", "肝纤维化四项"],
   "gated_terms_en": ["mre", "elf test", "cap score", "biomarker panel", "non-invasive test"],
   "gated_terms_zh": ["无创诊断", "生物标志物"],
   "gate_en": ["liver", "hepat", "cirrho", "steato", "mash", "nash", "masld", "fibrosis"],
   "gate_zh": ["肝"]},

  {"id": "L15", "tier": "aux", "name_zh": "肠肝轴与微生态", "name_en": "Gut-liver axis & microbiome",
   "terms_en": ["gut-liver axis", "fxr agonist"],
   "terms_zh": ["肠肝轴"],
   "gated_terms_en": ["microbiome", "microbiota", "bile acid"],
   "gated_terms_zh": ["肠道菌群", "微生态", "胆汁酸"],
   "gate_en": ["liver", "hepat", "cirrho", "steato", "mash", "nash", "masld"],
   "gate_zh": ["肝"]},

  {"id": "L16", "tier": "aux", "name_zh": "药物性肝损伤", "name_en": "Drug-induced liver injury",
   "terms_en": ["drug-induced liver injury", "dili", "hepatotoxicity", "hy's law",
                "alt elevation", "transaminase elevation"],
   "terms_zh": ["药物性肝损伤", "肝毒性", "转氨酶升高", "海氏法则"]}
]
```

## companies

`market` drives which collector owns the name: `us` → SEC EDGAR (T2) plus the
newswires (T1); `eu`/`global` → newswires plus newsroom watch (T7); `hk`/`cn` →
HKEX / CNINFO (T5). `tier` 1 is a core liver name, 2 is a large-cap with a
liver programme, 3 is a watchlist name.

```json
[
  {"name": "Madrigal Pharmaceuticals", "ticker": "MDGL", "market": "us", "tier": 1, "lines": ["L3"]},
  {"name": "Akero Therapeutics", "ticker": "AKRO", "cik": 1744659, "market": "us", "tier": 1, "lines": ["L3", "L5"],
   "note": "CIK pinned (the ticker left company_tickers.json). Filed Form 25-NSE 2025-12-09 and Form 15-12G 2025-12-19, so it is no longer a reporting company and files no 8-K: EDGAR cannot produce anything for it. Its MASH programme now sits with the acquirer -- track that entity instead."},
  {"name": "89bio", "ticker": "ETNB", "cik": 1785173, "market": "us", "tier": 1, "lines": ["L3"],
   "note": "CIK pinned, as for Akero. Filed Form 15-12G 2025-11-10; no longer a reporting company, so EDGAR produces nothing."},
  {"name": "Viking Therapeutics", "ticker": "VKTX", "market": "us", "tier": 2, "lines": ["L3"]},
  {"name": "Altimmune", "ticker": "ALT", "market": "us", "tier": 2, "lines": ["L3"]},
  {"name": "Terns Pharmaceuticals", "ticker": "TERN", "sec_filer": false, "market": "us", "tier": 2, "lines": ["L3"],
   "note": "Neither the ticker nor an EDGAR company search finds this filer. Cover it from its own newsroom. Do not guess a CIK -- one guessed here resolved to an unrelated company."},
  {"name": "Inventiva", "ticker": "IVA", "market": "eu", "tier": 1, "lines": ["L3"]},
  {"name": "GENFIT", "ticker": "GNFT", "cik": 1757064, "market": "eu", "tier": 1, "lines": ["L4", "L7"],
   "note": "CIK pinned from an EDGAR company search (conformed name Genfit S.A.); the ticker is not in company_tickers.json."},
  {"name": "Mirum Pharmaceuticals", "ticker": "MIRM", "market": "us", "tier": 1, "lines": ["L4", "L11"]},
  {"name": "Ipsen", "ticker": "IPN", "sec_filer": false, "market": "eu", "tier": 2, "lines": ["L4", "L11"],
   "note": "No SEC filings found by company search: Euronext-listed, not a SEC filer. EDGAR can never cover it -- register its IR newsroom feed instead."},
  {"name": "Aligos Therapeutics", "ticker": "ALGS", "market": "us", "tier": 1, "lines": ["L1", "L3"]},
  {"name": "Vir Biotechnology", "ticker": "VIR", "market": "us", "tier": 1, "lines": ["L1", "L2"]},
  {"name": "Arbutus Biopharma", "ticker": "ABUS", "market": "us", "tier": 1, "lines": ["L1"]},
  {"name": "Assembly Biosciences", "ticker": "ASMB", "market": "us", "tier": 2, "lines": ["L1"]},
  {"name": "Arrowhead Pharmaceuticals", "ticker": "ARWR", "market": "us", "tier": 2, "lines": ["L1", "L11"]},
  {"name": "Gilead Sciences", "ticker": "GILD", "market": "us", "tier": 1, "lines": ["L1", "L2", "L4", "L6", "L9"]},
  {"name": "GSK", "ticker": "GSK", "market": "global", "tier": 1, "lines": ["L1", "L2"]},
  {"name": "AbbVie", "ticker": "ABBV", "market": "us", "tier": 2, "lines": ["L9", "L4"]},
  {"name": "Novo Nordisk", "ticker": "NVO", "market": "eu", "tier": 1, "lines": ["L3"]},
  {"name": "Eli Lilly", "ticker": "LLY", "market": "us", "tier": 1, "lines": ["L3"]},
  {"name": "Boehringer Ingelheim", "ticker": null, "market": "eu", "tier": 2, "lines": ["L3"],
   "note": "Private: no newswire/SEC coverage, handled by T7 newsroom watch."},
  {"name": "Roche", "ticker": "ROG", "sec_filer": false, "market": "eu", "tier": 2, "lines": ["L1", "L6"],
   "note": "No SEC filings found by company search. Covered only by its own newsroom."},
  {"name": "AstraZeneca", "ticker": "AZN", "market": "global", "tier": 2, "lines": ["L3", "L6"]},
  {"name": "Takeda", "ticker": "TAK", "market": "global", "tier": 3, "lines": ["L5", "L7"]},
  {"name": "Merck & Co", "ticker": "MRK", "market": "us", "tier": 3, "lines": ["L3", "L6"]},
  {"name": "Johnson & Johnson", "ticker": "JNJ", "market": "us", "tier": 3, "lines": ["L1"]},
  {"name": "Ionis Pharmaceuticals", "ticker": "IONS", "market": "us", "tier": 3, "lines": ["L11"]},
  {"name": "Alnylam Pharmaceuticals", "ticker": "ALNY", "market": "us", "tier": 3, "lines": ["L11"]},
  {"name": "Enanta Pharmaceuticals", "ticker": "ENTA", "market": "us", "tier": 3, "lines": ["L1"]},
  {"name": "Hepion Pharmaceuticals", "ticker": "HEPA", "market": "us", "tier": 3, "lines": ["L3", "L5"]},
  {"name": "Intercept Pharmaceuticals", "ticker": null, "market": "us", "tier": 2, "lines": ["L3", "L4"],
   "note": "Taken private by Alfasigma; newswire only."},
  {"name": "Brii Biosciences 腾盛博药", "ticker": "2137.HK", "market": "hk", "tier": 1, "lines": ["L1", "L2"]},
  {"name": "Ascletis Pharma 歌礼制药", "ticker": "1672.HK", "market": "hk", "tier": 1, "lines": ["L1", "L3", "L9"]},
  {"name": "Sino Biopharmaceutical 中国生物制药 / 正大天晴", "ticker": "1177.HK", "market": "hk", "tier": 1, "lines": ["L1", "L6", "L9"]},
  {"name": "Hansoh Pharmaceutical 翰森制药", "ticker": "3692.HK", "market": "hk", "tier": 2, "lines": ["L1", "L6"]},
  {"name": "HEC Pharm 东阳光药", "ticker": "1558.HK", "market": "hk", "tier": 2, "lines": ["L1", "L9"]},
  {"name": "Haisco 海思科", "ticker": "002653.SZ", "market": "cn", "tier": 2, "lines": ["L3", "L5"]},
  {"name": "Amoytop Biotech 特宝生物", "ticker": "688278.SH", "market": "cn", "tier": 1, "lines": ["L1"]},
  {"name": "Kawin Technology 凯因科技", "ticker": "688687.SH", "market": "cn", "tier": 1, "lines": ["L1", "L9"]},
  {"name": "Cosunter 广生堂", "ticker": "300436.SZ", "market": "cn", "tier": 2, "lines": ["L1", "L3"]},
  {"name": "Fuji Pharma 福瑞股份", "ticker": "300049.SZ", "market": "cn", "tier": 3, "lines": ["L14"]}
]
```

## kol

Matching is off for any entry with `"verified": false`. Section 3 of the
handover: the Chinese names below are pinyin inferences and must be confirmed
against the operator's own roster before the tagger is allowed to use them.

```json
[
  {"name": "Rohit Loomba", "lines": ["L3"], "region": "us", "verified": true},
  {"name": "Arun Sanyal", "lines": ["L3", "L5"], "region": "us", "verified": true},
  {"name": "Vlad Ratziu", "lines": ["L3"], "region": "eu", "verified": true},
  {"name": "Mary Rinella", "lines": ["L3"], "region": "us", "verified": true},
  {"name": "Quentin Anstee", "lines": ["L3"], "region": "eu", "verified": true},
  {"name": "Philip Newsome", "lines": ["L3"], "region": "eu", "verified": true},
  {"name": "Zobair Younossi", "lines": ["L3"], "region": "us", "verified": true},
  {"name": "Scott Friedman", "lines": ["L3", "L5"], "region": "us", "verified": true},
  {"name": "Heiner Wedemeyer", "lines": ["L1", "L2"], "region": "eu", "verified": true},
  {"name": "Pietro Lampertico", "lines": ["L1", "L2"], "region": "eu", "verified": true},
  {"name": "Markus Cornberg", "lines": ["L1"], "region": "eu", "verified": true},
  {"name": "Fabien Zoulim", "lines": ["L1"], "region": "eu", "verified": true},
  {"name": "Man-Fung Yuen", "lines": ["L1"], "region": "apac", "verified": true},
  {"name": "Anna Lok", "lines": ["L1"], "region": "us", "verified": true},
  {"name": "Gideon Hirschfield", "lines": ["L4"], "region": "us", "verified": true},
  {"name": "Christopher Bowlus", "lines": ["L4"], "region": "us", "verified": true},
  {"name": "David Jones", "lines": ["L4"], "region": "eu", "verified": true},
  {"name": "Josep Llovet", "lines": ["L6"], "region": "us", "verified": true},
  {"name": "Richard Finn", "lines": ["L6"], "region": "us", "verified": true},
  {"name": "Amit Singal", "lines": ["L6"], "region": "us", "verified": true},
  {"name": "Peter Galle", "lines": ["L6"], "region": "eu", "verified": true},
  {"name": "Arndt Vogel", "lines": ["L6"], "region": "eu", "verified": true},
  {"name": "Guadalupe Garcia-Tsao", "lines": ["L5"], "region": "us", "verified": true},
  {"name": "Jaime Bosch", "lines": ["L5"], "region": "eu", "verified": true},
  {"name": "Rajiv Jalan", "lines": ["L7"], "region": "eu", "verified": true},
  {"name": "PLACEHOLDER-CN-1", "lines": ["L1"], "region": "cn", "verified": false,
   "note": "Pinyin-inferred Chinese KOL roster from the previous session was not attached; fill in and set verified:true after the operator confirms."},
  {"name": "PLACEHOLDER-CN-2", "lines": ["L3"], "region": "cn", "verified": false},
  {"name": "PLACEHOLDER-CN-3", "lines": ["L6"], "region": "cn", "verified": false}
]
```

## weights

```json
{
  "line_main": 1.0,
  "line_aux": 0.5,
  "line_exceptions": {"L13": 1.0},
  "company_tier": {"1": 1.0, "2": 0.7, "3": 0.4},
  "src_kind": {
    "regulator": 1.0,
    "filing": 0.9,
    "company": 0.8,
    "registry": 0.8,
    "journal": 0.6,
    "conference": 0.6
  },
  "recency_halflife_days": 3.0,
  "kol_bonus": 0.2,
  "conference_window_bonus": 0.3
}
```
