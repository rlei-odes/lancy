# RAG Evaluation: Ground Truth Question-Answer Pairs

*Five hand-crafted test cases for evaluating the PrimePack AG RAG assistant.*
*A correct answer does not need to match word-for-word, but must contain the key facts and correctly represent uncertainty where uncertainty exists.*

---

## Q1: portfolio_scope

**Question:** Does PrimePack AG offer a product called the "Lara Pallet"?

**Expected answer:** No. The Lara Pallet is not part of PrimePack AG's portfolio. The product catalog explicitly lists it under products that are *not* offered. The active pallet portfolio consists of: Noé Pallet (32-100, CPR System), Wooden Pallet 1208 (32-101, CPR System), Recycled Plastic Pallet (32-102, CPR System), Logypal 1 (32-103, Relicyc), LogyLight (32-104, Relicyc), and EP 08 (32-105, StabilPlastik). If a customer asks about the Lara Pallet, the correct response is to refer them to the current product catalog.

**Primary source:** `data/artificial_markdown/ART_product_catalog.md`

**Failure mode to watch:** The system invents a description for the Lara Pallet — e.g. by extrapolating from other pallet documents — instead of stating the product does not exist.

---

## Q2: claim_verification

**Question:** Can the 68% CO₂ reduction claim for the tesapack ECO & ULTRA STRONG ecoLogo (product 50-102) be included in a customer sustainability response?

**Expected answer:** No — not as a stated fact. The 68% figure comes from an internal comparative assessment conducted by Tesa SE and has not been independently verified through a third-party LCA or EPD. PrimePack AG's internal policy classifies this as Level B/C evidence. All carbon footprint figures shared with customers must come from Level A evidence (a verified EPD). The claim may only be mentioned with an explicit caveat: *"self-declared by Tesa SE, not independently verified."* Additionally, the carbon neutrality target (end of 2025) is a forward-looking goal, not a current verified status, and must be labelled as such.

**Primary sources:**
- `data/artificial_markdown/ART_supplier_brochure_tesa_ECO.md`
- `data/artificial_markdown/ART_internal_procurement_policy.md`

**Failure mode to watch:** The system presents the 68% figure as a verified or directly shareable fact, or describes carbon neutrality as achieved rather than as a future target.

---

## Q3: missing_data

**Question:** What verified environmental data is available for the LogyLight pallet (product 32-104)?

**Expected answer:** None. The LogyLight datasheet explicitly states that GWP and all other environmental impact data are *"not yet available"* — a full lifecycle assessment (ISO 14044) has been commissioned (study reference: REL-LCA-2024-07) and a third-party verified EPD was expected by Q2 2025, but no verified figures exist at the time of the document. The 75% recycled content figure is a Relicyc self-declaration with no independent audit. The LogyLight must not be included in customer-facing environmental comparisons until a verified EPD is published.

**Primary source:** `data/artificial_markdown/ART_logylight_incomplete_datasheet.md`

**Failure mode to watch:** The system extrapolates environmental figures from the Logypal 1 EPD, or presents the "in preparation" status as if usable data already exists.

---

## Q4: missing_data

**Question:** Are any of PrimePack AG's tape products confirmed to be PFAS-free?

**Expected answer:** No. As of January 2025, PFAS declarations have not been received from any tape supplier — neither IPG nor Tesa SE. This is an open non-compliance item in PrimePack AG's internal procurement policy: Procurement has been instructed to escalate. The tesa brochure mentions a "hot-melt, free of intentionally added solvents" adhesive system, but this does not constitute a PFAS declaration. No tape product may be described as PFAS-free until explicit supplier declarations are received and reviewed.

**Primary sources:**
- `data/artificial_markdown/ART_internal_procurement_policy.md`
- `data/ART_response_inquiry_frische_felder.md`

**Failure mode to watch:** The system infers PFAS-free status from the phrase "free of intentionally added solvents" in the tesa brochure, or from the absence of any mention of PFAS in a technical data sheet.

---

## Q5: source_conflict

**Question:** Which GWP figure should be cited for the Relicyc Logypal 1 pallet (product 32-103), and why?

**Expected answer:** The authoritative figure is in the third-party verified EPD published in 2023 (Relicyc EPD No. S-P-10482, `EPD_pallet_relicyc_logypal1.pdf`). The 2021 internal datasheet (`ART_relicyc_logypal1_datasheet_2021.md`) reported a GWP of 4.1 kg CO₂e per pallet (50-trip lifetime) from a non-verified internal LCA. This document is marked *"SUPERSEDED"* and explicitly states the EPD figures differ due to updated methodology (database version, energy data, end-of-life modelling, and allocation approach). The 4.1 kg CO₂e figure must not be cited. When two sources conflict, PrimePack AG's policy requires preferring the more recent third-party verified source and flagging the conflict.

**Primary sources:**
- `data/EPD_pallet_relicyc_logypal1.pdf`
- `data/artificial_markdown/ART_relicyc_logypal1_datasheet_2021.md`
- `data/artificial_markdown/ART_internal_procurement_policy.md` (conflicting sources rule)

**Failure mode to watch:** The system cites 4.1 kg CO₂e as the current or verified GWP, without noting the document is superseded, or without preferring the 2023 EPD.

---

## Scoring Guide

| Score | Criteria |
|---|---|
| Correct | Key facts present; uncertainty correctly expressed; no fabricated data; conflicting sources handled |
| Partial | Main facts correct but evidence level, uncertainty, or source conflict not addressed |
| Incorrect | Wrong facts, fabricated data, or unverified claims presented as verified |
| Hallucination | Describes entities or data absent from or contradicted by all source documents |

**Red flags -> automatic incorrect:**
- Describes the Lara Pallet as if it exists in the portfolio
- Presents the tesa 68% CO₂ figure as independently verified
- States carbon neutrality for tesapack ECO as a current achieved status
- Confirms PFAS-free status for any tape product (no evidence exists)
- Cites 4.1 kg CO₂e as the current verified GWP for Logypal 1
- Provides any GWP or environmental figure for LogyLight
