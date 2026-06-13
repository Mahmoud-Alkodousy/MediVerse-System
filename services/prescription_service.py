import os
import re
import json
import base64
import logging
import time
import requests
from typing import Optional
from difflib import SequenceMatcher

from database.connection import DatabaseManager

logger = logging.getLogger("mediverse")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
VISION_MODEL = "qwen/qwen2.5-vl-72b-instruct"
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "openai/gpt-4o-mini")

_DB_COLS = "drug_name, dosage_form, active_ingredient, drug_class, indications, dosage_adults, dosage_children, side_effects_common, side_effects_serious, contraindications, warnings, pregnancy, breastfeeding"
_DB_COL_LIST = [c.strip() for c in _DB_COLS.split(",")]


# ══════════════════════════════════════════════
# API Helpers
# ══════════════════════════════════════════════

def _call_vision(image_b64, mime, prompt, max_tokens=800):
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": VISION_MODEL, "max_tokens": max_tokens, "temperature": 0,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]}]
                },
                timeout=120
            )
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                logger.warning(f"Vision 429, waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise Exception(f"Qwen error {resp.status_code}: {resp.text[:300]}")
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep((attempt + 1) * 10)
                continue
            raise
    raise Exception("Vision API rate limited after 3 retries")


def _call_llm_json(messages, max_tokens=4000):
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": ANALYSIS_MODEL, "max_tokens": max_tokens, "temperature": 0.1, "messages": messages},
                timeout=180
            )
            if resp.status_code == 429:
                time.sleep((attempt + 1) * 10)
                continue
            if resp.status_code != 200:
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"): raw = raw[:-3]
                raw = raw.strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
        except Exception:
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


# ══════════════════════════════════════════════
# PASS 1: Masked Reading
# ══════════════════════════════════════════════

PASS1_PROMPT = """You are reading a handwritten medical prescription image.

Your job: find every drug name and write it character by character.

CRITICAL RULE:
- If you are 100% sure about a character → write it normally
- If you are NOT sure about a character → write _ (underscore) instead
- NEVER guess or hallucinate — if in doubt, ALWAYS use _
- It is BETTER to write more _ than to guess wrong characters

Examples of correct output:
  _olibra_        (first and last chars were unclear)
  Amoxicillin     (all characters were clear)
  Para___mol      (middle part unclear)
  ____icill__     (mostly unclear)
  _ugm___in       (several unclear characters)
  A_g_entin       (alternating unclear chars)

Output rules:
- One masked drug name per line
- No labels, no explanations, no numbering
- Only output the masked names, nothing else
- If you see 5 drugs, output 5 lines
- Include ALL drugs, do not stop early
"""


def _pass1_extract(image_b64, mime):
    raw = _call_vision(image_b64, mime, PASS1_PROMPT)
    drugs = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or len(line) < 2:
            continue
        if ":" in line or line[0].isdigit():
            continue
        anchors = re.findall(r'[a-zA-Z0-9]{3,}', line)
        drugs.append({
            "masked": line,
            "anchors": anchors,
            "guess": line.replace("_", "").strip() or "???"
        })
    return drugs


# ══════════════════════════════════════════════
# DB: Anchor Search
# ══════════════════════════════════════════════

def _row_to_dict(row):
    return {_DB_COL_LIST[i]: (row[i] or "") for i in range(min(len(_DB_COL_LIST), len(row)))}


def _fuzzy_score(anchor, drug_word):
    a = anchor.upper()
    b = drug_word.upper()
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.85)
    return ratio


def _db_anchor_search(anchors, top_n=6):
    if not anchors:
        return []
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            seen = {}

            for anchor in anchors:
                anchor = anchor.strip()
                if len(anchor) < 3:
                    continue
                cursor.execute(
                    f"SELECT {_DB_COLS} FROM egypt_drugs WHERE drug_name LIKE ?",
                    (f"%{anchor}%",)
                )
                for row in cursor.fetchall():
                    d = _row_to_dict(row)
                    name = d["drug_name"]
                    if name not in seen:
                        seen[name] = {"details": d, "score": 0}
                    seen[name]["score"] += 1

            missing_anchors = []
            for anchor in anchors:
                anchor = anchor.strip()
                if len(anchor) < 3:
                    continue
                cursor.execute(
                    "SELECT COUNT(*) FROM egypt_drugs WHERE drug_name LIKE ?",
                    (f"%{anchor}%",)
                )
                if cursor.fetchone()[0] == 0:
                    missing_anchors.append(anchor)

            if missing_anchors:
                cursor.execute(f"SELECT {_DB_COLS} FROM egypt_drugs")
                all_drugs = [_row_to_dict(row) for row in cursor.fetchall()]
                for anchor in missing_anchors:
                    fuzzy_hits = []
                    for d in all_drugs:
                        first_word = d["drug_name"].split()[0]
                        score = _fuzzy_score(anchor, first_word)
                        if score >= 0.75:
                            fuzzy_hits.append((d, score))
                    fuzzy_hits.sort(key=lambda x: x[1], reverse=True)
                    for d, score in fuzzy_hits[:top_n]:
                        name = d["drug_name"]
                        if name not in seen:
                            seen[name] = {"details": d, "score": 0}
                        seen[name]["score"] += 0.5

            cursor.close()
            sorted_results = sorted(seen.items(), key=lambda x: x[1]["score"], reverse=True)
            return [item[1]["details"] for item in sorted_results[:top_n]]
    except Exception as e:
        logger.error(f"DB anchor search error: {e}")
        return []


def _step2_db_match(drugs):
    enriched = []
    for d in drugs:
        all_candidates = {}

        for c in _db_anchor_search(d["anchors"]):
            all_candidates[c["drug_name"]] = c

        guess = d["guess"]
        if len(all_candidates) < 3 and guess and len(guess) >= 3:
            for c in _db_anchor_search([guess]):
                if c["drug_name"] not in all_candidates:
                    all_candidates[c["drug_name"]] = c

        if len(all_candidates) < 3 and guess and len(guess) >= 5:
            try:
                with DatabaseManager.get_connection() as conn:
                    cursor = conn.cursor()
                    prefix = guess[:5]
                    cursor.execute(
                        f"SELECT {_DB_COLS} FROM egypt_drugs WHERE drug_name LIKE ?",
                        (f"{prefix}%",)
                    )
                    for row in cursor.fetchall():
                        dd = _row_to_dict(row)
                        if dd["drug_name"] not in all_candidates:
                            all_candidates[dd["drug_name"]] = dd
                    cursor.close()
            except Exception as e:
                logger.warning(f"Prefix search failed: {e}")

        candidates_list = list(all_candidates.values())
        candidates_names = [c["drug_name"] for c in candidates_list]
        best = candidates_list[0] if candidates_list else None

        enriched.append({
            "masked": d["masked"],
            "anchors": d["anchors"],
            "guess": d["guess"],
            "db_candidates": candidates_names[:8],
            "best_match": best["drug_name"] if best else None,
            "best_match_details": best if best else None,
            "found_in_db": len(candidates_list) > 0,
        })
    return enriched


# ══════════════════════════════════════════════
# PASS 2: Confirm with Candidates
# ══════════════════════════════════════════════

def _pass2_confirm(image_b64, mime, enriched):
    if not enriched:
        return enriched

    lines = []
    for i, d in enumerate(enriched):
        cands = ", ".join(d["db_candidates"][:4]) or "none found"
        lines.append(
            f'{i+1}. Masked reading: "{d["masked"]}" | '
            f'Confident parts: {", ".join(d["anchors"]) if d["anchors"] else "none"} | '
            f'DB candidates: {cands}'
        )

    prompt = f"""You are reading a handwritten medical prescription.

I did a first pass and masked unclear characters with _:

{chr(10).join(lines)}

Now look at the prescription image carefully.
For each drug, pick the CORRECT name from the DB candidates that best matches:
1. The visible characters (non-underscore parts)
2. The overall word shape and length

Rules:
- ONLY pick from the DB candidates list — never invent names
- If no candidate matches well, write the best guess from the masked reading
- The _ characters could be ANY letter — use the candidates to fill them in

Output format — one line per drug, exactly like this:
1. CORRECT: Dolibran
2. CORRECT: Amoxicillin

Only output these lines, nothing else.
"""
    try:
        raw = _call_vision(image_b64, mime, prompt)
    except Exception as e:
        logger.warning(f"Pass 2 failed: {e}, using Pass 1 results")
        for d in enriched:
            d["confirmed"] = d["best_match"] or d["guess"]
        return enriched

    for line in raw.strip().split("\n"):
        m = re.match(r"(\d+)\.\s*CORRECT\s*:\s*(.+)", line.strip(), re.I)
        if m:
            idx = int(m.group(1)) - 1
            name = m.group(2).strip()
            if 0 <= idx < len(enriched):
                matched_candidate = None
                name_upper = name.upper()
                for cand in enriched[idx]["db_candidates"]:
                    cand_first_word = cand.split()[0].upper()
                    name_first_word = name.split()[0].upper()
                    if name_upper == cand.upper() or name_first_word == cand_first_word:
                        matched_candidate = cand
                        break
                enriched[idx]["confirmed"] = matched_candidate or name

    for d in enriched:
        if "confirmed" not in d:
            d["confirmed"] = d["best_match"] or d["guess"]

    return enriched


# ══════════════════════════════════════════════
# STEP 4: GPT-4o Analysis
# ══════════════════════════════════════════════

def _get_patient_data(patient_id):
    try:
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT full_name,gender,date_of_birth,blood_type,chronic_diseases,allergies,current_medications,weight,height FROM Patients WHERE id=?",
                (patient_id,))
            r = cursor.fetchone()
            cursor.close()
        if not r:
            return None
        from utils.helpers import calculate_age
        return {
            "full_name": r[0], "gender": r[1] or "Unknown", "age": calculate_age(r[2]),
            "blood_type": r[3] or "Unknown", "chronic_diseases": r[4] or "None",
            "allergies": r[5] or "None", "current_medications": r[6] or "None",
            "weight": r[7], "height": r[8]
        }
    except Exception as e:
        logger.error(f"Patient data error: {e}")
        return None


def _step4_analyze(enriched, patient_id=None):
    patient = _get_patient_data(patient_id) if patient_id else None

    meds_lines = []
    for i, d in enumerate(enriched):
        confirmed = d.get("confirmed", d.get("best_match", d["guess"]))
        meds_lines.append(
            f'  {i+1}. Confirmed: "{confirmed}" (OCR masked: "{d["masked"]}", anchors: {d["anchors"]})')
    meds_text = "\n".join(meds_lines)

    patient_section = ""
    interaction_checks = "- Check drug-drug interactions between prescribed medications"
    if patient:
        patient_section = (
            f"\n## Patient:\n- Name: {patient['full_name']} | Age: {patient['age']} | Gender: {patient['gender']}\n"
            f"- Chronic: {patient['chronic_diseases']}\n- Allergies: {patient['allergies']}\n"
            f"- Current Meds: {patient['current_medications']}")
        interaction_checks = ("- Check drug-drug interactions (prescribed + current meds)\n"
            "- Check drug-disease interactions\n- Check drug-allergy conflicts\n- Check duplicate therapy")
    else:
        patient_section = "\n## Patient: NOT PROVIDED"

    prompt = f"""You are a clinical pharmacist. These medications were confirmed from a prescription:
{patient_section}

## Confirmed Medications:
{meds_text}

Return ONLY JSON:
{{
  "medications": [
    {{
      "ocr_name": "original masked reading",
      "corrected_name": "confirmed drug name",
      "found_in_db": true/false,
      "arabic_name": "بالعربي",
      "description_ar": "وصف - المادة الفعالة والاستخدام",
      "active_ingredient": "ingredient",
      "dosage": "الجرعة",
      "dosage_source": "prescription/suggested",
      "frequency": "عدد المرات",
      "frequency_source": "prescription/suggested",
      "duration": "المدة أو null",
      "route": "oral/injection/topical/nasal",
      "is_safe": true/false,
      "warnings": ["تحذيرات"]
    }}
  ],
  "interactions": [
    {{
      "type": "drug_drug/drug_disease/drug_allergy",
      "severity": "mild/moderate/severe/critical",
      "drugs_involved": ["drug1", "drug2"],
      "description_ar": "شرح",
      "recommendation_ar": "توصية",
      "alternative": "بديل أو null"
    }}
  ],
  "overall_risk_level": "safe/low_risk/moderate_risk/high_risk/critical",
  "summary_ar": "ملخص بالعربي",
  "summary_en": "summary"
}}

{interaction_checks}
Return ONLY valid JSON."""

    data = _call_llm_json([
        {"role": "system", "content": "Clinical pharmacist AI. Arabic drug info + interactions. JSON only."},
        {"role": "user", "content": prompt}
    ])

    if not data:
        return {"success": False, "error": "Analysis LLM returned no valid JSON"}

    for med in data.get("medications", []):
        ocr = med.get("ocr_name", "").lower()
        for orig in enriched:
            masked_lower = orig["masked"].lower()
            guess_lower = orig["guess"].lower()
            if ocr == masked_lower or ocr in masked_lower or masked_lower in ocr or ocr == guess_lower:
                if orig.get("best_match_details"):
                    med["db_name"] = orig["best_match"]
                    med["found_in_db"] = True
                    med["db_details"] = orig["best_match_details"]
                else:
                    med["found_in_db"] = False
                    med["db_details"] = None
                break

    data["success"] = True
    data["model_used"] = ANALYSIS_MODEL
    if patient:
        data["patient_name"] = patient["full_name"]
        data["patient_id"] = patient_id
    return data


# ══════════════════════════════════════════════
# MAIN PIPELINE — with SSE progress callback
# ══════════════════════════════════════════════

def analyze_prescription(image_bytes, mime_type="image/jpeg", patient_id=None, on_progress=None):
    """
    Anchor-Based OCR Pipeline v6.
    on_progress: optional callback(step, status, data) for SSE streaming.
    """
    def emit(step, status, data=None):
        if on_progress:
            on_progress(step, status, data or {})

    logger.info(f"Prescription analysis v6 | patient_id={patient_id}")
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    # ── Pass 1: Masked reading ──
    emit("pass1", "running")
    try:
        drugs = _pass1_extract(b64, mime_type)
    except Exception as e:
        logger.error(f"Pass 1 failed: {e}")
        emit("pass1", "error", {"error": str(e)})
        return {"success": False, "step_failed": "pass1", "error": str(e)}

    if not drugs:
        emit("pass1", "done", {"count": 0})
        return {"success": True, "analysis": None, "warning": "No medications detected"}

    emit("pass1", "done", {"count": len(drugs), "drugs": [d["masked"] for d in drugs]})
    logger.info(f"Pass 1: {len(drugs)} drugs")

    # ── DB Anchor Search ──
    emit("db", "running")
    try:
        enriched = _step2_db_match(drugs)
        db_found = sum(1 for d in enriched if d["found_in_db"])
        emit("db", "done", {"found": db_found, "total": len(enriched)})
        logger.info(f"DB: {db_found}/{len(enriched)} found")
    except Exception as e:
        logger.error(f"DB search failed: {e}")
        emit("db", "error", {"error": str(e)})
        enriched = [{**d, "db_candidates": [], "best_match": None,
                     "best_match_details": None, "found_in_db": False} for d in drugs]
        db_found = 0

    # ── Pass 2: Confirm with candidates ──
    emit("pass2", "running")
    try:
        enriched = _pass2_confirm(b64, mime_type, enriched)
        emit("pass2", "done", {"confirmed": [d.get("confirmed", "?") for d in enriched]})
        logger.info("Pass 2: " + ", ".join(f'"{d.get("confirmed", "?")}"' for d in enriched))
    except Exception as e:
        logger.error(f"Pass 2 failed: {e}")
        emit("pass2", "error", {"error": str(e)})
        for d in enriched:
            d["confirmed"] = d.get("best_match") or d["guess"]

    # ── Step 4: GPT-4o Analysis ──
    emit("analysis", "running")
    try:
        analysis = _step4_analyze(enriched, patient_id)
        emit("analysis", "done")
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        emit("analysis", "error", {"error": str(e)})
        analysis = {"success": False, "error": str(e)}

    emit("complete", "done")

    return {
        "success": True,
        "version": "v6",
        "ocr": {
            "medications_raw": [{"masked": d["masked"], "anchors": d["anchors"], "guess": d["guess"]} for d in drugs],
            "model": VISION_MODEL,
        },
        "db_matching": {
            "total": len(enriched),
            "found_in_db": db_found,
            "details": [{
                "ocr": d["masked"],
                "anchors": d["anchors"],
                "confirmed": d.get("confirmed", d.get("best_match", d["guess"])),
                "best_match": d["best_match"],
                "found": d["found_in_db"],
                "candidates": d["db_candidates"][:4]
            } for d in enriched]
        },
        "analysis": analysis,
    }
