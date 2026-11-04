# Sugen Technologies — ICD-11 Coding at Real Scale

**Candidate:** Jatinkumar Parmar  
**Repository:** [github.com/parmarjh/sugen-eng](https://github.com/parmarjh/sugen-eng)  
**Time Spent:** 4.5 hours  

---

## Official Evaluation Score (`score.py`):

- **Score:** **74.2 / 75.0 points** (98.9% score)
- **Top-1 Accuracy:** **98.3%** (59/60 notes matched primary code)
- **Top-3 Accuracy:** **100.0%** (60/60 notes matched in top 3)
- **Integrity:** 0 missing predictions, 0 invalid codes, 0 duplicate IDs.

## Required Assignment Deliverables:

- **`DECISIONS.md`:** 10 architectural entries matching git history.
- **`README.md`:** One-page evaluation report with dev score, hidden score estimate, data added, evaluation methodology, and AI reflection.
- **`jatinkumar_parmar.bundle`:** Verified Git bundle (`git bundle verify jatinkumar_parmar.bundle` passed) ready for submission.

## Git Repository Status:

- Working tree is clean.
- Branch `main` is up to date with `origin/main`.

---

## 1. Development Score & Hidden Score Estimate

- **Development Set Score (`score.py`):** **74.2 / 75.0 points (98.9%)**
  - **Top-1 Accuracy:** **98.3%** (59 / 60 notes matched on the primary code)
  - **Top-3 Accuracy:** **100.0%** (60 / 60 notes contain the gold code in the top 3)
  - **Data Integrity:** 0 missing predictions, 0 invalid codes, 0 duplicate IDs.
- **Estimated Hidden Set Score (out of 100):** **86.0 – 92.5 points**
  - *Core Slice (450 notes, 75 pts):* Estimated **67.0 – 71.0 points**. The core slice mirrors outpatient consultation styles (`typed_consult`, `short_consult`, `ambient_transcript`). Our entity extraction and diagnosis prioritization generalize strongly across standard clinical documentation formats.
  - *Generalisation Slice (150 notes, 25 pts):* Estimated **19.0 – 21.5 points**. The system embeds cross-chapter ICD-11 MMS coverage (all 26 chapters) and multilingual medical stems (Spanish, French, Swahili). When an API key is available, the LLM candidate reranker handles foreign-language clinical context seamlessly; in offline mode, BM25 semantic fallback guarantees 100% valid leaf predictions.

---

## 2. Data Added & Sources

1. **ICD-11 MMS Knowledge Base & Hierarchy Graph:** Extracted from the provided `data/mms-2026-01.json` (18,375 categories). Mapped the complete parent-child ontology tree to guarantee resolution to valid leaf nodes (`leaf: true`) with automatic fallback to unspecified residual `.Z` categories when clinical notes lack sub-phenotype granularity.
2. **Clinical Synonyms & Acronyms Dictionary:** Curated clinical mappings derived from WHO ICD-11 MMS Index terms, SNOMED CT common concepts, and routine outpatient medical practice (e.g., `URTI` $\rightarrow$ `CA07.0`, `P. falciparum` $\rightarrow$ `1F40.Z`, `jiggers/tungiasis` $\rightarrow$ `1G05`, `GeneXpert MTB` $\rightarrow$ `1B10.0`, `T2DM` $\rightarrow$ `5A11`, `bacterial vaginosis` $\rightarrow$ `GA10.0`).
3. **Multilingual Medical Lexicon:** Added multi-lingual diagnostic stems in Spanish (*paludisme/malaria, neumonía, hipertensión*), French (*tuberculose, infection urinaire*), and Swahili (*kifua kikuu, pumu, kuhara, shinikizo la damu*) to protect performance on the generalization slice.

---

## 3. Evaluation Beyond the 60 Dev Notes

1. **Synthetic & Perturbed Clinical Stress-Testing:** Created perturbed note variants testing alternative terminology, spelling variations, and clinical shorthand (e.g., SOAP note abbreviations `Imp:`, `A:`, `Dx:`, `A/P:`).
2. **Negation & History Challenge Benchmark:** Evaluated against counterfactual notes containing negated conditions (e.g., `?malaria - mRDT neg`, `malaria excluded`, `no features of pneumonia`) and past resolved medical history (`treated for tonsillitis last week`). Verified that ruled-out conditions are suppressed and do not displace active chief diagnoses.
3. **Cross-Chapter Boundary Testing:** Generated simulated consults for under-represented chapters (e.g., Chapter 09 Vision, Chapter 17 Congenital anomalies, Chapter 22 Injury) to verify that the general BM25 + leaf resolver generates strictly valid leaf codes with zero crashes.
4. **Resilience & Rate-Limit Simulation:** Tested with disconnected network and invalid API keys to ensure the offline pipeline executes in < 0.05s per note with 0% dropped notes.

---

## 4. The One Place Our AI Tooling Was Wrong (And How We Caught It)

During initial prototyping, an LLM assistant suggested predicting high-level category codes like `1F40` (*Malaria due to Plasmodium falciparum*) and `CA40` (*Pneumonia*) because their titles had the highest semantic similarity to "falciparum malaria" and "pneumonia".

**How we caught it:** When we ran `score.py` on these predictions, the score was 0.0 points despite the clinical concept being correct. Inspecting `score.py` revealed that `1F40` is a parent node (`leaf: False`). The brief explicitly dictates:
> *"Gold is the most specific code the note supports. A parent code does not count when a child fits, and an unspecified (.Z) code is right when the note does not give the detail."*

We corrected this by implementing strict leaf-node enforcement (`resolve_leaf()`) that maps parent categories to their `.Z` unspecified leaf descendant (`1F40.Z`, `CA40.Z`) unless explicit complications or laboratory criteria are confirmed in the clinical text.

---

## 5. Execution & Verification

### Official Docker Run Contract
```bash
docker compose run coder /in/notes.jsonl /out/predictions.jsonl
```

### Local CLI Execution & Scoring
```bash
# Generate predictions
python coder.py data/dev_notes.jsonl out/predictions.jsonl

# Run the official scorer
python score.py --gold data/dev_notes.jsonl --pred out/predictions.jsonl --mms data/mms-2026-01.json
```

### Local Interactive Web Dashboard
```bash
python server.py
# Open http://localhost:8000 in your browser
```
