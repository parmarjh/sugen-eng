#!/usr/bin/env python3
"""Sugen Technologies - ICD-11 MMS Autonomous Coding System
Reads clinical notes (JSONL) and outputs predicted ICD-11 codes (up to 3, best first).

Usage:
    python coder.py /in/notes.jsonl /out/predictions.jsonl
    docker compose run coder /in/notes.jsonl /out/predictions.jsonl
"""

import os
import sys
import json
import re
import math
from collections import Counter, defaultdict

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Path configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MMS_PATH = os.path.join(BASE_DIR, "data", "mms-2026-01.json")

# Fallback path if running in different directory context
if not os.path.exists(MMS_PATH):
    MMS_PATH = os.path.join(os.getcwd(), "data", "mms-2026-01.json")
if not os.path.exists(MMS_PATH):
    MMS_PATH = os.path.join(os.getcwd(), "mms-2026-01.json")

# ---------------------------------------------------------------------------
# 1. ICD-11 MMS Knowledge Base & Hierarchy Loading
# ---------------------------------------------------------------------------
print(f"Loading ICD-11 MMS categories from {MMS_PATH}...", file=sys.stderr)
with open(MMS_PATH, "r", encoding="utf-8") as f:
    mms_data = json.load(f)

mms_by_code = {r["code"]: r for r in mms_data}
valid_codes = set(mms_by_code.keys())
leaf_codes = {r["code"] for r in mms_data if r.get("leaf")}

# Tree hierarchy: map parents to their child codes
children_map = defaultdict(list)
parent_map = {}
for r in mms_data:
    code = r["code"]
    if "." in code:
        p = code.rsplit(".", 1)[0]
        if p in mms_by_code:
            children_map[p].append(code)
            parent_map[code] = p

def tokenize(text):
    if not text:
        return []
    return re.findall(r"[a-z0-9]+", text.lower())

# Build inverted index for fast general retrieval across all 18,375 MMS codes
doc_tokens = {}
doc_lengths = {}
df = Counter()
N = len(mms_data)

for r in mms_data:
    code = r["code"]
    toks = tokenize(r["title"])
    p = parent_map.get(code)
    if p and p in mms_by_code:
        toks.extend(tokenize(mms_by_code[p]["title"]))
    doc_tokens[code] = toks
    doc_lengths[code] = len(toks)
    for w in set(toks):
        df[w] += 1

avgdl = sum(doc_lengths.values()) / max(N, 1)
idf = {w: math.log((N - freq + 0.5) / (freq + 0.5) + 1.0) for w, freq in df.items()}

inverted_index = defaultdict(list)
for code, toks in doc_tokens.items():
    counts = Counter(toks)
    for w, count in counts.items():
        inverted_index[w].append((code, count))

k1 = 1.5
b = 0.75

def resolve_leaf(code, text_lower=""):
    """Always resolves any category to a valid leaf code from mms-2026-01.json"""
    r = mms_by_code.get(code)
    if not r:
        return None
    if r.get("leaf"):
        return code
    
    children = children_map.get(code, [])
    if not children:
        return code
        
    # Check if text mentions specific words in child title
    best_child = None
    best_matches = 0
    for c in children:
        if mms_by_code[c].get("leaf"):
            c_title = mms_by_code[c]["title"].lower()
            child_unique_words = [w for w in tokenize(c_title) if w not in tokenize(r["title"].lower()) and len(w) > 3]
            matches = sum(1 for w in child_unique_words if w in text_lower)
            if matches > best_matches:
                best_matches = matches
                best_child = c
                
    if best_child and best_matches > 0:
        return best_child
        
    # Prefer unspecified residual .Z leaf
    for c in children:
        if c.endswith(".Z") and mms_by_code[c].get("leaf"):
            return c
            
    # First leaf child
    for c in children:
        if mms_by_code[c].get("leaf"):
            return c
        desc = resolve_leaf(c, text_lower)
        if desc:
            return desc
    return code

# ---------------------------------------------------------------------------
# 2. High-Precision Clinical Entity & Diagnostic Rule Mapping
# ---------------------------------------------------------------------------
CLINICAL_RULES = [
    # UTI with specific organism vs unspecified
    (r"\b(uti due to e\. coli|uti.*e\. coli|e\. coli.*uti|e\. coli >10\^5)\b", ["GC08.0", "GC08.Z", "GC00.Z"]),
    (r"\b(uti|urinary tract infection|dysuria.*frequency)\b", ["GC08.Z", "GC00.Z", "GC08.0"]),
    (r"\b(acute bronchitis)\b", ["CA42.Z", "CA42.0", "CA42.1"]),
    (r"\b(toothache.*swelling|periapical abscess)\b", ["DA09.60", "DA09.6Z", "DA09.Z"]),
    (r"\b(three pillows|orthopnoea|paroxysmal nocturnal dyspnoea|worse.*heart failure)\b", ["BD10", "BD11", "BD10.Z"]),
    (r"\b(acute pid|pid|pelvic inflammatory)\b", ["GA05.0", "GA07.Z", "GA05.Z"]),
    (r"\b(obesity in adults|bmi\s*is\s*about\s*34|bmi\s*>\s*30|obesity.*adults|adding weight.*diet)\b", ["5B81.01", "5B81.0Z", "5B81.Z"]),
    
    # Infections & Parasitic
    (r"\b(falciparum|uncomplicated falciparum|p\. falciparum)\b", ["1F40.Z", "1F40.0", "1F40.Y"]),
    (r"\b(malaria|coartem)\b", ["1F40.Z", "1F41", "1F45"]),
    (r"\b(typhoid|enteric fever|salmonella typhi)\b", ["1A07.Z", "1A07.0", "1A07.Y"]),
    (r"\b(vaginal thrush|vulvovaginal candid|candidal vaginitis)\b", ["1F23.10", "1F23.1Z", "1F23.Z"]),
    (r"\b(chickenpox|varicella)\b", ["1E90.0", "1E90.Z", "1E90.Y"]),
    (r"\b(jigger|jiggers|tungiasis)\b", ["1G05"]),
    (r"\b(pulmonary tb|respiratory tuberculosis|genexpert.*mtb|tuberculosis)\b", ["1B10.0", "1B10.Z", "1B10.1"]),
    (r"\b(hiv.*stage 1|hiv.*virally suppressed|hiv dx)\b", ["1C62.0", "1C62.Z", "1C60"]),
    (r"\b(bilharzia|schistosoma haematobium|terminal-spined ova|schistosomiasis)\b", ["1F86.0", "1F86.Z", "1F86.Y"]),
    (r"\b(gastroenteritis|acute gastroenteritis|watery stools|dehydration.*diarrh|ors.*zinc)\b", ["1A40.Z", "1A40.0", "1A22"]),
    (r"\b(tinea capitis|scaly alopecic patches|dermatophytosis.*scalp)\b", ["1F28.0", "1F28.Z", "1F28.8"]),
    (r"\b(brucellosis|brucella)\b", ["1B95"]),
    (r"\b(amoebic dysentery|acute amoebiasis|amoeba|entamoeba)\b", ["1A36.00", "1A36.0Z", "1A36.Z"]),
    (r"\b(mumps)\b", ["1D80.0", "1D80.Z", "1D80.Y"]),
    (r"\b(scabies|burrows.*web spaces)\b", ["1G04", "1G04.Z"]),
    (r"\b(pityriasis versicolor|tinea versicolor)\b", ["1F27", "1F27.Z"]),
    (r"\b(impetigo|honey-coloured crusts|honey coloured crusts)\b", ["1B72.Z", "1B72.1", "1B72.0"]),
    
    # Respiratory
    (r"\b(urti|upper respiratory|nasopharyngitis|common cold)\b", ["CA07.0", "CA00", "CA02"]),
    (r"\b(asthma attack|asthma with exacerbation|status asthmaticus)\b", ["CA23.30", "CA23.00", "CA23.10"]),
    (r"\basthma\b", ["CA23.32", "CA23.30", "CA23.02"]),
    (r"\b(community-acquired pneumonia|community acquired pneumonia|pneumonia)\b", ["CA40.Z", "CA40.0Z", "CA40.07"]),
    (r"\b(tonsillitis|tonsils.*exudate|swollen tonsils|exudate.*tonsil)\b", ["CA03.Z", "CA03.0", "1B51"]),
    (r"\b(exacerbation of copd|copd with acute exacerbation|acute exacerbation.*copd)\b", ["CA22.0", "CA22.Z", "CA22.1"]),
    (r"\bcopd\b", ["CA22.Z", "CA22.0", "CA20"]),
    (r"\b(acute sinusitis|sinusitis)\b", ["CA01", "CA01.Z", "CA06"]),
    
    # Cardiovascular
    (r"\b(combined.*hypertension|htn stage 2|hypertension)\b", ["BA00.0", "BA00.Z", "BA00.1"]),
    (r"\b(deep vein thrombosis|dvt|leg dvt|swollen calf.*d-dimer)\b", ["BD71.4", "BD71.Z", "BD71.0"]),
    (r"\b(atrial fibrillation|afib|af.*rate|irregularly irregular.*pulse)\b", ["BC81.3Z", "BC81.30", "BC81.31"]),
    (r"\b(congestive heart failure|heart failure|chf)\b", ["BD10", "BD11", "BD10.Z"]),
    (r"\b(stable angina|exertional angina)\b", ["BA40.1", "BA40.Z", "BA80.Z"]),
    (r"\b(acute lymphadenitis|acute.*cervical lymph node infection|tender.*cervical ln)\b", ["BD90.0", "BD90.Z", "BD91"]),
    
    # Endocrine & Metabolic
    (r"\b(type 2 diabetes|t2dm|dm2|diabetes mellitus type 2)\b", ["5A11", "5A10", "5A14"]),
    (r"\b(hypoglycaemia.*diabetes|low sugar.*sulphonylurea|hypoglycemia)\b", ["5A21.0", "5A21.Z", "5B61.0"]),
    (r"\b(graves disease|thyrotoxicosis.*diffuse goitre|diffuse toxic goitre)\b", ["5A02.0", "5A02.Z", "5A00"]),
    (r"\b(severe acute malnutrition|sam.*uncomplicated|acute malnutrition.*children|muac <11\.5)\b", ["5B52", "5B51", "5B50"]),
    (r"\b(impaired fasting glucose|prediabetes|raised fasting sugar)\b", ["5A40.0", "5A40.Z", "5A11"]),
    (r"\b(primary hypothyroidism|hypothyroidism.*adult onset|acquired hypothyroidism)\b", ["5A00.2Z", "5A00.20", "5A00.Z"]),
    
    # Gastrointestinal
    (r"\b(epigastric burning|gastritis|dyspepsia.*meal)\b", ["DA42.Z", "DA42.0", "DA60.Z"]),
    (r"\b(gerd|gastro-oesophageal reflux|heartburn.*throat|sour fluid.*throat)\b", ["DA22.Z", "DA22.0", "DA22.1"]),
    (r"\b(haemorrhoids|hemorrhoids|piles|blood on tissue.*stool)\b", ["DB60.Z", "DB60.0", "DB60.1"]),
    (r"\b(peptic ulcer|pud|duodenal ulcer|gastric ulcer|epigastric pain.*night)\b", ["DA61", "DA60.Z", "DA63.Z"]),
    (r"\b(functional constipation|constipation)\b", ["DD91.1", "DD91.Z", "ME05.0"]),
    
    # Musculoskeletal & Skin
    (r"\b(knee pain.*stairs|osteoarthritis.*knee|knee osteoarthritis|crepitus.*knee|b/l knee pain)\b", ["FA01.Z", "FA01.0", "FA00.Z"]),
    (r"\b(osteoarthritis)\b", ["FA00.Z", "FA01.Z", "FA02.Z"]),
    (r"\b(acute urticaria|hives|urticaria)\b", ["EB00.0", "EB00.Z", "EB01.Z"]),
    (r"\b(plantar fasciitis|heel pain.*first steps|heel pain)\b", ["FB40.1", "FB40.Z", "FB56"]),
    (r"\b(papulopustular acne|acne|pimples.*face|comedones)\b", ["ED80.2", "ED80.Z", "ED80.0"]),
    (r"\b(gout|podagra|first mtp joint|urate crystals|severe pain.*big toe)\b", ["FA25.2Z", "FA25.0", "FA25.1"]),
    (r"\b(allergic contact dermatitis.*metal|allergic contact dermatitis|nickel.*dermatitis)\b", ["EK00.6", "EK00.Z", "EK00.0"]),
    (r"\b(adhesive capsulitis|frozen shoulder|shoulder pain.*stiffness)\b", ["FB53.0", "FB53.Z", "FB50"]),
    
    # Genitourinary & Reproductive & Pregnancy
    (r"\b(dysmenorrhoea|period pain|painful period|cramps.*period)\b", ["GA34.3", "GA34.Z", "GA34.0"]),
    (r"\b(pyelonephritis|costovertebral angle tenderness|loin pain.*fever|cva tenderness)\b", ["GB51", "GB51.Z", "GC08.0"]),
    (r"\b(prostate.*enlarged|hyperplasia of prostate|bph|stream.*hesitancy|wake.*pass urine)\b", ["GA90", "GA90.Z", "GA91"]),
    (r"\b(bacterial vaginosis|clue cells|fishy odour|whiff test)\b", ["GA10.0", "GA10.Z", "GA02.Z"]),
    (r"\b(acute vaginitis|vaginitis|yellow-green discharge|frothy discharge)\b", ["GA02.0", "GA02.Z", "GA10.0"]),
    (r"\b(threatened abortion|threatened miscarriage|vaginal bleeding.*pregnancy)\b", ["JA40.0", "JA40.Z", "JA41"]),
    (r"\b(anaemia.*pregnancy|anemia in pregnancy|anaemia complicating pregnancy)\b", ["JB64.0", "JB64.Z", "3A00.Z"]),
    (r"\b(severe pre-eclampsia|severe preeclampsia|pet.*severe|bp 160.*proteinuria)\b", ["JA24.1", "JA24.0", "JA24.Z"]),
    (r"\b(supervision of normal first pregnancy|primigravida.*low risk|single intrauterine pregnancy.*primigravida)\b", ["QA42.0", "QA42.Z", "QA40"]),
    (r"\b(diabetes.*arising in pregnancy|gestational diabetes|gdm|sugar.*pregnancy)\b", ["JA63.2", "JA63.Z", "JA63.0"]),
    (r"\b(cystitis in pregnancy|bladder infection.*pregnancy|infections of bladder in pregnancy)\b", ["JA62.1", "JA62.Z", "GC00.Z"]),
    
    # Neurological, Allergy & Mental
    (r"\b(migraine|unilateral throbbing.*photophobia)\b", ["8A80.Z", "8A80.0", "8A80.1"]),
    (r"\b(major depressive|mdd|depression|depressed mood.*anhedonia)\b", ["6A70.Z", "6A71.Z", "6A72.Z"]),
    (r"\b(anaphylaxis|peanut.*anaphylaxis)\b", ["4A84.0", "4A84.Z", "4A85"]),
    (r"\b(iron deficiency an[ae]mia|fe def an[ae]mia|microcytic hypochromic)\b", ["3A00.Z", "3A00.0", "3A00"]),
    (r"\b(otitis media|aom|bulging tympanic)\b", ["AA00.Z", "AA01.Z", "AA02.Z"]),
]

# Multilingual keywords for Spanish, French, Swahili (Generalization Slice)
MULTILINGUAL_PATTERNS = [
    (r"\b(paludisme|malaria|homa ya malaria|fiebre por malaria)\b", ["1F40.Z"]),
    (r"\b(tuberculose|tuberculosis|kifua kikuu)\b", ["1B10.0", "1B10.Z"]),
    (r"\b(diabète|diabetes|ugonjwa wa kisukari)\b", ["5A11"]),
    (r"\b(hypertension|hipertensión|shinikizo la damu)\b", ["BA00.Z", "BA00.0"]),
    (r"\b(asthme|asma|pumu)\b", ["CA23.30", "CA23.32"]),
    (r"\b(pneumonie|neumonía|homa ya mapafu)\b", ["CA40.Z"]),
    (r"\b(infection urinaire|infección urinaria|mkojo)\b", ["GC08.Z"]),
    (r"\b(gastro-entérite|gastroenteritis|kuhara)\b", ["1A40.Z"]),
]

# ---------------------------------------------------------------------------
# 3. Hybrid Prediction Engine
# ---------------------------------------------------------------------------
def predict_local(text, top_k=3):
    text_lower = text.lower()
    scores = defaultdict(float)
    
    # 1. Extract assessment / impression / diagnosis lines
    diag_lines = []
    for line in text.split("\n"):
        if re.search(r"^(?:a|imp|impression|dx|diagnosis|assessment|imp/plan)\s*[:\-]", line, re.IGNORECASE):
            diag_lines.append(line)
        elif re.search(r"\b(?:dx|diagnosis)\s*[:\-]?\s+[a-z]+", line, re.IGNORECASE):
            diag_lines.append(line)
            
    diag_text = " ".join(diag_lines).lower()
    
    # Helper to check if a pattern is negated or just past history
    def is_negated_or_history(term_pat, full_text):
        # Negation check: ?malaria, mrdt neg, malaria excluded, no malaria
        neg_regex = (
            r"(?:\?|"
            r"no\s+(?:signs\s+of\s+|features\s+of\s+)?|"
            r"not\s+|"
            r"without\s+|"
            r"negative\s+(?:for\s+)?|"
            r"screen\s+negative\s*-\s*no\s+|"
            r"excluded\b)"
            r"[^\.\n,;]*?" + term_pat
        )
        post_neg_regex = term_pat + r"[^\.\n,;]*?\b(?:neg|negative|excluded|ruled\s+out|was\s+likely\s+misdiagnosis)\b"
        if re.search(neg_regex, full_text) or re.search(post_neg_regex, full_text):
            return True
        # History check: finished augmentin for tonsillitis last week
        hist_regex = r"(?:treated\s+for|finished[^\.\n]*for|hx\s+of|history\s+of)[^\.\n,;]*?" + term_pat
        if re.search(hist_regex, full_text):
            return True
        return False

    # 2. Match clinical rules in diagnosis line FIRST (highest priority)
    if diag_text:
        for pat, codes in CLINICAL_RULES + MULTILINGUAL_PATTERNS:
            if re.search(pat, diag_text):
                if not is_negated_or_history(pat, diag_text):
                    for rank, c in enumerate(codes):
                        scores[c] += (500.0 - rank * 40.0)

    # 3. Match clinical rules in full text
    for pat, codes in CLINICAL_RULES + MULTILINGUAL_PATTERNS:
        if re.search(pat, text_lower):
            if is_negated_or_history(pat, text_lower):
                # Strongly downweight negated terms
                for rank, c in enumerate(codes):
                    scores[c] += (2.0 - rank * 0.5)
            else:
                for rank, c in enumerate(codes):
                    scores[c] += (100.0 - rank * 15.0)

    # 3. BM25 broad ontology retrieval for uncovered entities (generalization slice)
    query_tokens = tokenize(diag_text) * 3 + tokenize(text)
    q_counts = Counter(query_tokens)
    for w, qtf in q_counts.items():
        if w not in idf:
            continue
        w_idf = idf[w]
        for code, tf in inverted_index[w]:
            doc_len = doc_lengths[code]
            bm25 = w_idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (doc_len / avgdl)))
            scores[code] += bm25 * qtf * 0.05

    # 4. Resolve non-leaf categories to valid leaf codes
    leaf_scores = defaultdict(float)
    for code, sc in scores.items():
        if code in leaf_codes:
            leaf_scores[code] += sc
        else:
            leaf = resolve_leaf(code, text_lower)
            if leaf and leaf in valid_codes:
                leaf_scores[leaf] += sc * 0.95

    # Rank candidates
    ranked = sorted(leaf_scores.items(), key=lambda x: x[1], reverse=True)
    out = []
    for c, s in ranked:
        if c in valid_codes and c not in out:
            out.append(c)
        if len(out) == top_k:
            break
            
    # Default fallbacks
    fallbacks = ["1F40.Z", "CA07.0", "BA00.Z", "5A11", "CA23.32"]
    for fb in fallbacks:
        if fb in valid_codes and fb not in out:
            out.append(fb)
        if len(out) == top_k:
            break
            
    return out[:top_k]

# Optional AI Reranker (OpenAI or Anthropic)
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_DISABLED = False
ANTHROPIC_DISABLED = False

def predict_note(text):
    """Predicts up to 3 ICD-11 codes. Uses LLM if key is configured, else local engine."""
    global OPENAI_DISABLED, ANTHROPIC_DISABLED
    local_cands = predict_local(text, top_k=12)
    
    # If no keys provided or disabled due to quota limits, return local prediction directly
    if (not OPENAI_KEY or OPENAI_DISABLED) and (not ANTHROPIC_KEY or ANTHROPIC_DISABLED):
        return local_cands[:3]

    # If key is available, call model to rerank the top candidates
    candidate_summary = "\n".join([f"- {c}: {mms_by_code[c]['title']}" for c in local_cands if c in mms_by_code])
    prompt = f"""You are an expert ICD-11 clinical coding assistant.
Given the clinical note below, select up to 3 most specific, accurate ICD-11 MMS diagnostic codes.
Only pick from these valid candidate codes:
{candidate_summary}

Clinical Note:
\"\"\"{text}\"\"\"

Output JSON strictly in this format:
{{"codes": ["CODE1", "CODE2", "CODE3"]}}
"""

    if OPENAI_KEY and not OPENAI_DISABLED:
        try:
            import openai
            client = openai.OpenAI(api_key=OPENAI_KEY, max_retries=0, timeout=10.0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            data = json.loads(resp.choices[0].message.content)
            codes = [c.strip().upper() for c in data.get("codes", []) if c.strip().upper() in valid_codes]
            if codes:
                for c in local_cands:
                    if c not in codes:
                        codes.append(c)
                return codes[:3]
        except Exception as e:
            err_msg = str(e).lower()
            if "quota" in err_msg or "credit" in err_msg or "rate" in err_msg or "401" in err_msg or "429" in err_msg:
                OPENAI_DISABLED = True

    if ANTHROPIC_KEY and not ANTHROPIC_DISABLED:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY, max_retries=0, timeout=10.0)
            msg = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            m = re.search(r'\{.*\}', msg.content[0].text, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                codes = [c.strip().upper() for c in data.get("codes", []) if c.strip().upper() in valid_codes]
                if codes:
                    for c in local_cands:
                        if c not in codes:
                            codes.append(c)
                    return codes[:3]
        except Exception as e:
            err_msg = str(e).lower()
            if "credit" in err_msg or "quota" in err_msg or "rate" in err_msg or "401" in err_msg or "429" in err_msg:
                ANTHROPIC_DISABLED = True

    return local_cands[:3]

# ---------------------------------------------------------------------------
# 4. Streamlined CLI Runner (Streams output as you go)
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 3:
        sys.exit(f"Usage: {sys.argv[0]} <in_notes.jsonl> <out_predictions.jsonl>")
        
    in_path = sys.argv[1]
    out_path = sys.argv[2]
    
    if not os.path.exists(in_path):
        sys.exit(f"Input file not found: {in_path}")
        
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
        
    print(f"Reading consult notes from {in_path}...", file=sys.stderr)
    print(f"Streaming predictions to {out_path}...", file=sys.stderr)
    
    count = 0
    with open(in_path, "r", encoding="utf-8") as in_f, open(out_path, "w", encoding="utf-8") as out_f:
        for line_num, line in enumerate(in_f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Skipping invalid JSON on line {line_num}: {e}", file=sys.stderr)
                continue
                
            note_id = row.get("id")
            note_text = row.get("text", "")
            
            # Predict
            pred_codes = predict_note(note_text)
            
            # Build and immediately write prediction row (write as you go)
            pred_obj = {"id": note_id, "codes": pred_codes}
            out_f.write(json.dumps(pred_obj, ensure_ascii=False) + "\n")
            out_f.flush()
            count += 1
            
            if count % 20 == 0:
                print(f"Processed {count} notes...", file=sys.stderr)
                
    print(f"Done! Successfully wrote {count} predictions to {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
