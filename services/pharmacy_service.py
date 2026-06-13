"""
MediVerse Pharmacy Agent — FastAPI Backend
==========================================
نظام صيدلية ذكي بيعمل:
1. ترشيح أدوية بناءً على شكوى المريض + تاريخه المرضي
2. قراءة روشتات بالـ OCR (Anchor-based)
3. تحليل تعارضات الأدوية
4. اقتراح بدائل
"""

import os
import re
import csv
import json
import base64
import logging
from io import StringIO
from pathlib import Path
from datetime import date, datetime
from typing import Optional

import pyodbc
import requests
import numpy as np
from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from difflib import SequenceMatcher

load_dotenv(override=True)

# ── Config ──────────────────────────────────────────
OPENROUTER_KEY  = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDINGS_FILE = Path(__file__).parent / "drug_embeddings.npz"
DB_SERVER       = os.getenv("SQL_SERVER", "localhost")
DB_NAME         = os.getenv("SQL_DATABASE", "MediVerse_System")
DB_USER         = os.getenv("SQL_USER", "")
DB_PASSWORD     = os.getenv("SQL_PASSWORD", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mediverse")

# ── FastAPI App ─────────────────────────────────────



# ══════════════════════════════════════════════════════
#  DATABASE LAYER
# ══════════════════════════════════════════════════════

def get_db_connection():
    if DB_USER:
        cs = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={DB_SERVER};"
              f"DATABASE={DB_NAME};UID={DB_USER};PWD={DB_PASSWORD};Connection Timeout=8;")
    else:
        cs = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={DB_SERVER};"
              f"DATABASE={DB_NAME};Trusted_Connection=yes;Connection Timeout=8;")
    return pyodbc.connect(cs)


def get_patient(patient_id: int) -> Optional[dict]:
    """جيب بيانات المريض من الـ DB"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, full_name, national_id, gender, date_of_birth,
                   phone_number, blood_type, chronic_diseases, allergies,
                   current_medications, weight, height, BMI
            FROM Patients WHERE id = ?
        """, (patient_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in cursor.description]
        patient = dict(zip(cols, row))
        # حساب العمر
        if patient.get("date_of_birth"):
            dob = patient["date_of_birth"]
            if isinstance(dob, str):
                dob = datetime.strptime(dob, "%Y-%m-%d").date()
            today = date.today()
            patient["age"] = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return patient
    finally:
        conn.close()


def search_drugs_by_indication(keywords: list[str], limit: int = 20) -> list[dict]:
    """بحث في الأدوية بناءً على الأعراض/الاستخدامات — بيبحث في كل الأعمدة المهمة"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        all_results = {}
        for kw in keywords:
            kw = kw.strip()
            if len(kw) < 2:
                continue
            cursor.execute("""
                SELECT drug_name, dosage_form, active_ingredient, drug_class,
                       indications, dosage_adults, dosage_children,
                       side_effects_common, side_effects_serious,
                       contraindications, warnings, pregnancy, breastfeeding
                FROM egypt_drugs
                WHERE indications LIKE ?
                   OR drug_class LIKE ?
                   OR active_ingredient LIKE ?
                   OR drug_name LIKE ?
            """, (f"%{kw}%", f"%{kw}%", f"%{kw}%", f"%{kw}%"))
            for row in cursor.fetchall():
                cols = [desc[0] for desc in cursor.description]
                drug = dict(zip(cols, row))
                name = drug["drug_name"]
                if name not in all_results:
                    all_results[name] = drug
                    all_results[name]["_score"] = 0
                all_results[name]["_score"] += 1

        results = sorted(all_results.values(), key=lambda x: x["_score"], reverse=True)
        for r in results:
            del r["_score"]
        return results[:limit]
    finally:
        conn.close()


def search_drugs_by_name(drug_name: str, limit: int = 10) -> list[dict]:
    """بحث بالاسم"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT drug_name, dosage_form, active_ingredient, drug_class,
                   indications, dosage_adults, dosage_children,
                   side_effects_common, side_effects_serious,
                   contraindications, warnings, pregnancy, breastfeeding
            FROM egypt_drugs
            WHERE drug_name LIKE ?
        """, (f"%{drug_name}%",))
        results = []
        for row in cursor.fetchall():
            cols = [desc[0] for desc in cursor.description]
            results.append(dict(zip(cols, row)))
        return results[:limit]
    finally:
        conn.close()


def find_alternatives(drug_name: str, limit: int = 10) -> list[dict]:
    """إيجاد بدائل بنفس المادة الفعالة"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # أول حاجة نجيب المادة الفعالة للدوا
        cursor.execute(
            "SELECT active_ingredient FROM egypt_drugs WHERE drug_name LIKE ?",
            (f"%{drug_name}%",)
        )
        row = cursor.fetchone()
        if not row:
            return []
        active = row[0]
        # نبحث عن أدوية بنفس المادة الفعالة
        # ناخد أول مكون فعال (ممكن يكون فيه أكتر من واحد)
        main_ingredient = active.split(",")[0].strip()
        # ننضف الـ dosage من الاسم
        ingredient_name = re.sub(r'\d+\s*mg.*', '', main_ingredient).strip()

        cursor.execute("""
            SELECT drug_name, dosage_form, active_ingredient, drug_class,
                   indications, dosage_adults, dosage_children,
                   side_effects_common, side_effects_serious,
                   contraindications, warnings, pregnancy, breastfeeding
            FROM egypt_drugs
            WHERE active_ingredient LIKE ? AND drug_name NOT LIKE ?
        """, (f"%{ingredient_name}%", f"%{drug_name}%"))
        results = []
        for row in cursor.fetchall():
            cols = [desc[0] for desc in cursor.description]
            results.append(dict(zip(cols, row)))
        return results[:limit]
    finally:
        conn.close()


def get_drug_details(drug_name: str) -> Optional[dict]:
    """جيب تفاصيل دوا بالاسم"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT drug_name, dosage_form, active_ingredient, drug_class,
                   indications, dosage_adults, dosage_children,
                   side_effects_common, side_effects_serious,
                   contraindications, warnings, pregnancy, breastfeeding
            FROM egypt_drugs
            WHERE drug_name = ? OR drug_name LIKE ?
        """, (drug_name, f"{drug_name}%"))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def fuzzy_find_drug(user_input: str, threshold: float = 0.55) -> dict:
    """
    بحث ذكي عن اسم دوا حتى لو المستخدم كتبه غلط
    بيرجع: {"exact_match": bool, "suggestions": [...], "best_match": str|None}
    
    مثال: "panadl" → يرشح "PANADOL EXTRA", "PANADOL" ...
    """
    user_input = user_input.strip()
    if not user_input:
        return {"exact_match": False, "suggestions": [], "best_match": None}

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Step 1: بحث مباشر بالـ LIKE أول
        cursor.execute(
            "SELECT DISTINCT drug_name FROM egypt_drugs WHERE drug_name LIKE ?",
            (f"%{user_input}%",)
        )
        exact_results = [row[0] for row in cursor.fetchall()]

        if exact_results:
            return {
                "exact_match": True,
                "suggestions": exact_results[:10],
                "best_match": exact_results[0]
            }

        # Step 2: مفيش exact → نعمل fuzzy matching
        cursor.execute("SELECT DISTINCT drug_name FROM egypt_drugs")
        all_drugs = [row[0] for row in cursor.fetchall()]

        user_upper = user_input.upper()
        scored = []

        for drug_name in all_drugs:
            # نقارن مع أول كلمة من اسم الدوا (الاسم التجاري بدون التركيز)
            first_word = drug_name.split()[0].upper()

            # SequenceMatcher
            ratio = SequenceMatcher(None, user_upper, first_word).ratio()

            # Bonus لو الـ input موجود كـ substring
            if user_upper in first_word or first_word in user_upper:
                ratio = max(ratio, 0.85)

            # Bonus لو أول 3 حروف متشابهين
            if len(user_upper) >= 3 and len(first_word) >= 3:
                if user_upper[:3] == first_word[:3]:
                    ratio = max(ratio, 0.7)

            if ratio >= threshold:
                scored.append((drug_name, first_word, ratio))

        # ترتيب بالـ score + إزالة التكرار في أول كلمة
        scored.sort(key=lambda x: x[2], reverse=True)

        # نجمع النتائج مع إزالة تكرار الاسم التجاري
        seen_first_words = set()
        suggestions = []
        for drug_name, first_word, score in scored:
            if first_word not in seen_first_words:
                seen_first_words.add(first_word)
                suggestions.append(drug_name)
            if len(suggestions) >= 8:
                break

        return {
            "exact_match": False,
            "suggestions": suggestions,
            "best_match": suggestions[0] if suggestions else None
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════
#  EMBEDDING LAYER — Vector Search
# ══════════════════════════════════════════════════════

# Cache في الـ RAM
_drug_embeddings: Optional[np.ndarray] = None   # matrix [N, dim]
_drug_names: list[str] = []                      # أسماء الأدوية بالترتيب
_drug_texts: list[str] = []                      # النصوص المجمّعة


def get_embedding(texts: list[str]) -> list[list[float]]:
    """
    يبعت نصوص لـ OpenRouter ويرجع embeddings
    بيعمل batch — يبعت لحد 100 نص في المرة
    """
    all_embeddings = []
    batch_size = 80  # OpenRouter limit

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = requests.post(
            f"{OPENROUTER_BASE}/embeddings",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": EMBEDDING_MODEL,
                "input": batch
            },
            timeout=60
        )
        if resp.status_code != 200:
            raise Exception(f"Embedding Error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        # ترتيب بالـ index لإن الـ API ممكن يرجعهم مش بالترتيب
        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        for item in sorted_data:
            all_embeddings.append(item["embedding"])

        logger.info(f"   Embedded batch {i//batch_size + 1}: {len(batch)} texts")

    return all_embeddings


def build_drug_text(drug: dict) -> str:
    """
    يجمّع الأعمدة المهمة في نص واحد للـ embedding
    بيضيف سياق طبي عشان الـ embedding يفهم أحسن
    """
    parts = []
    if drug.get("drug_name"):
        parts.append(f"اسم الدوا: {drug['drug_name']}")
    if drug.get("dosage_form"):
        parts.append(f"الشكل: {drug['dosage_form']}")
    if drug.get("drug_class"):
        parts.append(f"الفئة: {drug['drug_class']}")
    if drug.get("indications"):
        parts.append(f"يستخدم لعلاج: {drug['indications']}")
    if drug.get("active_ingredient"):
        parts.append(f"المادة الفعالة: {drug['active_ingredient']}")
    return " | ".join(parts)


def build_embeddings():
    """
    يبني embeddings لكل الأدوية ويحفظهم في ملف .npz
    بيتنادي مرة واحدة بس (أو لما تتحدث الداتا)
    """
    global _drug_embeddings, _drug_names, _drug_texts

    logger.info("🔨 Building drug embeddings...")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT drug_name, dosage_form, active_ingredient, drug_class,
                   indications, dosage_adults, dosage_children,
                   side_effects_common, side_effects_serious,
                   contraindications, warnings, pregnancy, breastfeeding
            FROM egypt_drugs
        """)
        cols = [desc[0] for desc in cursor.description]
        drugs = [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        conn.close()

    if not drugs:
        raise Exception("No drugs found in database!")

    logger.info(f"   Found {len(drugs)} drugs in DB")

    # بناء النصوص المجمّعة
    texts = []
    names = []
    for drug in drugs:
        text = build_drug_text(drug)
        texts.append(text)
        names.append(drug["drug_name"])

    # توليد الـ embeddings
    logger.info(f"   Generating embeddings via {EMBEDDING_MODEL}...")
    embeddings = get_embedding(texts)

    # تحويل لـ numpy array
    emb_matrix = np.array(embeddings, dtype=np.float32)

    # حفظ في ملف
    np.savez_compressed(
        EMBEDDINGS_FILE,
        embeddings=emb_matrix,
        names=np.array(names),
        texts=np.array(texts)
    )

    # تحميل في الـ RAM
    _drug_embeddings = emb_matrix
    _drug_names = names
    _drug_texts = texts

    logger.info(f"✅ Built & saved {len(names)} embeddings → {EMBEDDINGS_FILE}")
    return len(names)


def load_embeddings():
    """يحمّل الـ embeddings من الملف للـ RAM"""
    global _drug_embeddings, _drug_names, _drug_texts

    if not EMBEDDINGS_FILE.exists():
        logger.warning("⚠️ Embeddings file not found — run /api/embeddings/build first")
        return False

    data = np.load(EMBEDDINGS_FILE, allow_pickle=True)
    _drug_embeddings = data["embeddings"]
    _drug_names = data["names"].tolist()
    _drug_texts = data["texts"].tolist()

    logger.info(f"✅ Loaded {len(_drug_names)} embeddings from cache")
    return True


def semantic_search(query: str, top_k: int = 25) -> list[dict]:
    """
    بحث بالمعنى — يحوّل الشكوى لـ embedding ويقارنها بكل الأدوية
    بيرجع أقرب top_k دوا مع الـ similarity score
    """
    global _drug_embeddings, _drug_names

    if _drug_embeddings is None:
        loaded = load_embeddings()
        if not loaded:
            return []

    # توليد embedding للشكوى
    query_emb = get_embedding([query])[0]
    query_vec = np.array(query_emb, dtype=np.float32)

    # Cosine Similarity
    # normalize
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    emb_norms = _drug_embeddings / (np.linalg.norm(_drug_embeddings, axis=1, keepdims=True) + 1e-10)

    scores = emb_norms @ query_norm  # dot product = cosine similarity

    # أعلى top_k
    top_indices = np.argsort(scores)[::-1][:top_k]

    results = []
    for idx in top_indices:
        results.append({
            "drug_name": _drug_names[idx],
            "similarity": float(scores[idx])
        })

    return results


# ══════════════════════════════════════════════════════
#  AI AGENT — SMART RECOMMENDATION ENGINE (RAG-based)
# ══════════════════════════════════════════════════════

def call_llm(system_prompt: str, user_message: str, max_tokens: int = 2000) -> str:
    """استدعاء الـ LLM عبر OpenRouter"""
    resp = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "openai/gpt-4o-mini",
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"LLM Error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def call_llm_chat(messages: list, max_tokens: int = 2000) -> str:
    """استدعاء الـ LLM مع المحادثة كاملة (multi-turn)"""
    resp = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "openai/gpt-4o-mini",
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "messages": messages
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"LLM Error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def call_llm_chat_stream(messages: list, max_tokens: int = 2000):
    """Streaming LLM — yields text chunks"""
    import json as _json
    resp = requests.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "openai/gpt-4o-mini",
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": True,
            "messages": messages
        },
        timeout=120,
        stream=True
    )
    if resp.status_code != 200:
        raise Exception(f"LLM Error {resp.status_code}: {resp.text[:300]}")
    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if decoded.startswith("data: "):
            data = decoded[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = _json.loads(data)
                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if content:
                    yield content
            except:
                continue


def check_drug_safety(drug: dict, patient: dict) -> dict:
    """
    فحص سلامة الدوا بالنسبة للمريض
    بيرجع dict فيه: safe (bool), warnings (list), severity (str)
    """
    issues = []
    severity = "safe"

    chronic = (patient.get("chronic_diseases") or "").lower()
    allergies = (patient.get("allergies") or "").lower()
    current_meds = (patient.get("current_medications") or "").lower()
    contraindications = (drug.get("contraindications") or "").lower()
    warnings = (drug.get("warnings") or "").lower()
    active_ingredient = (drug.get("active_ingredient") or "").lower()
    gender = (patient.get("gender") or "").lower()
    age = patient.get("age", 30)

    # 1. فحص الحساسية
    if allergies:
        allergy_list = [a.strip() for a in allergies.split(",")]
        for allergy in allergy_list:
            if allergy and (allergy in active_ingredient or allergy in drug.get("drug_name", "").lower()):
                issues.append(f"⛔ المريض عنده حساسية من: {allergy}")
                severity = "dangerous"

    # 2. فحص الأمراض المزمنة ضد موانع الاستعمال
    if chronic and contraindications:
        chronic_list = [c.strip() for c in chronic.split(",")]
        for disease in chronic_list:
            if disease and disease in contraindications:
                issues.append(f"⚠️ الدوا ممنوع مع: {disease}")
                severity = "dangerous" if severity != "dangerous" else severity

    # 3. فحص العمر
    if age and age < 12 and "غير موصى للأطفال" in (drug.get("dosage_children") or ""):
        issues.append("⚠️ الدوا مش مناسب للأطفال تحت 12 سنة")
        severity = "warning" if severity == "safe" else severity

    # 4. فحص الحمل والرضاعة
    if gender in ["أنثى", "female", "f"]:
        preg = (drug.get("pregnancy") or "").lower()
        if "ممنوع" in preg or "تجنب" in preg or "خطر" in preg:
            issues.append(f"⚠️ تحذير للنساء - الحمل: {drug.get('pregnancy', '')}")
            severity = "warning" if severity == "safe" else severity

    # 5. فحص التعارض مع الأدوية الحالية
    if current_meds and warnings:
        med_list = [m.strip() for m in current_meds.split(",")]
        for med in med_list:
            if med and med.lower() in warnings:
                issues.append(f"⚠️ تعارض محتمل مع الدوا الحالي: {med}")
                severity = "warning" if severity == "safe" else severity

    return {
        "safe": severity == "safe",
        "severity": severity,
        "warnings": issues
    }


def smart_recommend(complaint: str, patient: dict) -> dict:
    """
    RAG-based Agent:
    1. يحوّل الشكوى لـ embedding
    2. يلاقي أقرب 25 دوا بالمعنى (semantic search)
    3. يجيب تفاصيلهم الكاملة من الـ DB
    4. يفلتر بناءً على التاريخ المرضي (safety check)
    5. يبعت الأدوية الآمنة للـ LLM يرشح أفضل 5
    """

    # Step 1: Semantic Search — بحث بالمعنى
    logger.info(f"🔍 Semantic search for: {complaint}")
    search_results = semantic_search(complaint, top_k=25)

    # لو مفيش embeddings — fallback للطريقة القديمة (LIKE search)
    if not search_results:
        logger.warning("⚠️ No embeddings — falling back to keyword search")
        return _fallback_keyword_recommend(complaint, patient)

    # Step 2: جيب تفاصيل الأدوية الكاملة من الـ DB
    drugs = []
    for result in search_results:
        details = get_drug_details(result["drug_name"])
        if details:
            details["_similarity"] = result["similarity"]
            drugs.append(details)

    if not drugs:
        return {
            "success": False,
            "message": "مش لاقي أدوية مناسبة للشكوى دي",
            "keywords": [],
            "recommendations": []
        }

    # Step 3: فحص كل دوا ضد تاريخ المريض
    safe_drugs = []
    unsafe_drugs = []

    for drug in drugs:
        safety = check_drug_safety(drug, patient)
        drug["safety"] = safety
        if safety["safe"]:
            safe_drugs.append(drug)
        elif safety["severity"] == "warning":
            drug["safety_note"] = "استخدم بحذر"
            safe_drugs.append(drug)
        else:
            unsafe_drugs.append(drug)

    # Step 4: الـ LLM يرشح أفضل 5 من الأدوية الآمنة
    drugs_summary = []
    for d in safe_drugs[:15]:
        drugs_summary.append({
            "name": d["drug_name"],
            "class": d["drug_class"],
            "form": d["dosage_form"],
            "ingredient": d["active_ingredient"],
            "indications": d.get("indications", "")[:150],
            "similarity": round(d.get("_similarity", 0), 3)
        })
    drugs_json_str = json.dumps(drugs_summary, ensure_ascii=False)

    rec_prompt = (
        "أنت صيدلي خبير مصري. المريض جالك بشكوى ومحتاج ترشحله أدوية.\n\n"
        f"شكوى المريض الأساسية: {complaint}\n"
        f"أمراض مزمنة (للسلامة فقط — مش للعلاج): {patient.get('chronic_diseases', 'لا يوجد')}\n"
        f"حساسيات: {patient.get('allergies', 'لا يوجد')}\n"
        f"أدوية حالية: {patient.get('current_medications', 'لا يوجد')}\n\n"
        f"قائمة أدوية مرشحة (فيها أدوية كتير مش مناسبة — لازم تفلترها!):\n{drugs_json_str}\n\n"
        "⚠️ تعليمات صارمة:\n"
        "1. اختار فقط الأدوية اللي بتعالج الشكوى الأساسية\n"
        "2. لا تختار أبداً أدوية للأمراض المزمنة (سكر/ضغط/كوليسترول) إلا لو الشكوى عنها\n"
        "   مثلاً: لو الشكوى ألم ركبة → رشح مسكنات ومضادات التهاب فقط — مش أدوية سكر أو ضغط!\n"
        "3. لو مفيش أدوية مناسبة، خلي recommendations فاضية []\n\n"
        "في الـ reason:\n"
        "- اشرح الدوا بيعمل إيه وإزاي هيفيد في الشكوى المحددة دي\n"
        "- اكتب بالعامية المصرية البسيطة\n"
        '- مثال: "ده مضاد للاحتقان بيفتح الأنف المسدود وفيه مسكن بيخفف ألم الجسم اللي بييجي مع البرد"\n\n'
        'أرجع JSON فقط:\n'
        '{"recommendations": [\n'
        '    {"drug_name": "اسم الدوا بالظبط من القائمة", "reason": "شرح إزاي الدوا ده هيفيد في الشكوى", "dosage_note": "الجرعة", "priority": 1}\n'
        '], "general_advice": "نصيحة عامة للمريض"}\n\n'
        "لا تكتب أي كلام تاني غير الـ JSON."
    )

    try:
        rec_response = call_llm(rec_prompt, "رشح الأدوية")
        rec_json = re.search(r'\{.*\}', rec_response, re.DOTALL)
        if rec_json:
            recommendations = json.loads(rec_json.group())
        else:
            recommendations = {"recommendations": [], "general_advice": ""}
    except Exception as e:
        logger.warning(f"Recommendation LLM failed: {e}")
        recommendations = {
            "recommendations": [
                {
                    "drug_name": d["drug_name"],
                    "reason": f"الدوا ده ({d.get('drug_class', '')}) فيه {d.get('active_ingredient', '')} — بيستخدم في: {(d.get('indications', '') or '')[:100]}",
                    "priority": i+1
                }
                for i, d in enumerate(safe_drugs[:5])
            ],
            "general_advice": ""
        }

    # ربط التفاصيل الكاملة
    for rec in recommendations.get("recommendations", []):
        rec_name = (rec.get("drug_name", "") or "").strip().upper()
        best_match = None
        best_score = 0
        for drug in safe_drugs:
            dn = drug["drug_name"].upper()
            if rec_name == dn or rec_name in dn or dn in rec_name:
                best_match = drug
                break
            if rec_name.split()[0] == dn.split()[0]:
                best_match = drug
                break
            score = SequenceMatcher(None, rec_name, dn).ratio()
            if score > best_score and score > 0.6:
                best_score = score
                best_match = drug
        if best_match:
            rec["details"] = best_match

    return {
        "success": True,
        "complaint": complaint,
        "search_method": "semantic (RAG)",
        "keywords": [f"{r['drug_name']} ({r['similarity']:.2f})" for r in search_results[:5]],
        "patient_summary": {
            "name": patient.get("full_name", ""),
            "age": patient.get("age"),
            "chronic_diseases": patient.get("chronic_diseases", "لا يوجد"),
            "allergies": patient.get("allergies", "لا يوجد"),
            "current_medications": patient.get("current_medications", "لا يوجد")
        },
        "recommendations": recommendations.get("recommendations", []),
        "general_advice": recommendations.get("general_advice", ""),
        "unsafe_drugs": [{"name": d["drug_name"], "reason": d["safety"]["warnings"]}
                         for d in unsafe_drugs[:5]],
        "total_found": len(drugs),
        "total_safe": len(safe_drugs)
    }


def _fallback_keyword_recommend(complaint: str, patient: dict) -> dict:
    """Fallback — لو مفيش embeddings بنستخدم keyword search"""
    extract_prompt = """أنت مساعد صيدلي ذكي خبير. استخرج كلمات مفتاحية للبحث في قاعدة بيانات أدوية.
المريض ممكن يكتب بالعامية — حوّل لفصحى طبية وفكّر طبياً.
أرجع JSON فقط:
{"keywords_ar": ["كلمة1", "كلمة2"], "keywords_en": ["word1", "word2"], "category": "الفئة"}
لا تكتب أي كلام تاني غير الـ JSON."""

    try:
        kw_response = call_llm(extract_prompt, f"شكوى المريض: {complaint}")
        kw_json = re.search(r'\{.*\}', kw_response, re.DOTALL)
        if kw_json:
            keywords_data = json.loads(kw_json.group())
        else:
            keywords_data = {"keywords_ar": [complaint], "keywords_en": []}
    except:
        keywords_data = {"keywords_ar": [complaint], "keywords_en": []}

    all_keywords = keywords_data.get("keywords_ar", []) + keywords_data.get("keywords_en", [])
    drugs = search_drugs_by_indication(all_keywords, limit=30)

    if not drugs:
        for kw in all_keywords:
            partial = search_drugs_by_indication([kw], limit=10)
            drugs.extend(partial)
        seen = set()
        drugs = [d for d in drugs if d["drug_name"] not in seen and not seen.add(d["drug_name"])]

    if not drugs:
        return {"success": False, "message": "مش لاقي أدوية مناسبة", "keywords": all_keywords, "recommendations": []}

    safe_drugs = []
    unsafe_drugs = []
    for drug in drugs[:20]:
        safety = check_drug_safety(drug, patient)
        drug["safety"] = safety
        if safety["safe"] or safety["severity"] == "warning":
            safe_drugs.append(drug)
        else:
            unsafe_drugs.append(drug)

    return {
        "success": True,
        "complaint": complaint,
        "search_method": "keyword (fallback)",
        "keywords": all_keywords,
        "patient_summary": {"name": patient.get("full_name", ""), "age": patient.get("age")},
        "recommendations": [{"drug_name": d["drug_name"], "reason": "مناسب للشكوى", "priority": i+1} for i, d in enumerate(safe_drugs[:5])],
        "general_advice": "",
        "unsafe_drugs": [{"name": d["drug_name"], "reason": d["safety"]["warnings"]} for d in unsafe_drugs[:5]],
        "total_found": len(drugs),
        "total_safe": len(safe_drugs)
    }

def check_prescription_interactions(drugs: list[dict]) -> list[str]:
    """فحص التعارضات بين الأدوية في الروشتة نفسها"""
    warnings_list = []
    drug_details = [d["details"] for d in drugs if d.get("details")]

    for i, drug_a in enumerate(drug_details):
        for j, drug_b in enumerate(drug_details):
            if i >= j:
                continue
            warnings_a = (drug_a.get("warnings") or "").lower()
            warnings_b = (drug_b.get("warnings") or "").lower()
            ingredient_a = (drug_a.get("active_ingredient") or "").lower()
            ingredient_b = (drug_b.get("active_ingredient") or "").lower()

            # فحص لو دوا بيحذر من التاني
            name_a_first = drug_a["drug_name"].split()[0].lower()
            name_b_first = drug_b["drug_name"].split()[0].lower()

            if name_b_first in warnings_a or ingredient_b.split()[0].lower() in warnings_a:
                warnings_list.append(
                    f"⚠️ {drug_a['drug_name']} فيه تحذير مع {drug_b['drug_name']}"
                )
            if name_a_first in warnings_b or ingredient_a.split()[0].lower() in warnings_b:
                warnings_list.append(
                    f"⚠️ {drug_b['drug_name']} فيه تحذير مع {drug_a['drug_name']}"
                )

            # فحص لو نفس المادة الفعالة (تكرار)
            main_a = re.sub(r'\d+\s*mg.*', '', ingredient_a.split(",")[0]).strip()
            main_b = re.sub(r'\d+\s*mg.*', '', ingredient_b.split(",")[0]).strip()
            if main_a and main_b and main_a == main_b:
                warnings_list.append(
                    f"🔴 {drug_a['drug_name']} و {drug_b['drug_name']} فيهم نفس المادة الفعالة ({main_a}) — خطر جرعة زائدة!"
                )

    return warnings_list


# ══════════════════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════════════════

# ── Models ──────────────────────────────────────────
class ComplaintRequest(BaseModel):
    patient_id: int
    complaint: str

class AlternativeRequest(BaseModel):
    patient_id: int
    drug_name: str

class DrugSearchRequest(BaseModel):
    query: str

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    patient_id: int
    messages: list[ChatMessage]


# ── TAG-BASED SEARCH ──────────────────────────────

SYMPTOM_TAGS_MAP = """
الألم والالتهاب: صداع، ألم، ألم أسنان، ألم عضلات، ألم مفاصل، ألم ظهر، ألم رقبة، ألم دورة، ألم أعصاب، التهاب، التهاب مفاصل، التهاب أوتار، التهاب أعصاب، روماتيزم، نقرس، تشنج عضلات، تشنجات عصبية
الجهاز التنفسي والبرد: برد، إنفلونزا، حمى، احتقان أنف، كحة، كحة جافة، كحة ببلغم، ضيق تنفس، ربو، التهاب حلق، التهاب شعب هوائية، التهاب رئوي، احتقان جيوب أنفية، رشح
الحساسية والجلد: حساسية، حساسية أنف، حساسية عين، حساسية جلد، حكة، أرتيكاريا، أكزيما، صدفية، طفح جلدي، حبوب، حب شباب، ترطيب جلد، حروق، جروح
المعدة والهضم: حموضة، حرقان معدة، ارتجاع مريء، قرحة معدة، جرثومة معدة، عسر هضم، انتفاخ، غازات، مغص، إسهال، إمساك، قولون عصبي، غثيان، قيء، نزلة معوية، فقدان شهية، دوار
القلب والأوعية: ضغط مرتفع، ضغط منخفض، ذبحة صدرية، جلطة، كوليسترول، دهون ثلاثية، منع تجلط، سيولة دم، ضعف دورة دموية، دوالي، قلب
العدوى: عدوى بكتيرية، عدوى فيروسية، عدوى فطرية، ديدان، طفيليات، فطريات جلد
العظام: هشاشة عظام، نقص كالسيوم، حصوات كلى
الغدد والهرمونات: سكر نوع 2، هرمونات أنثوية، تكيس مبايض، اضطراب دورة، موانع حمل، ضعف جنسي
الجهاز العصبي والنفسي: اكتئاب، قلق، توتر، أرق، إرهاق، زهايمر، شلل رعاش، دوخة، تركيز وانتباه
العيون والأذن: التهاب عين، حساسية عين، جلوكوما، ضغط عين، التهاب أذن
المسالك البولية: التهاب مسالك بولية، احتباس بول، سلس بول، كثرة تبول، قصور كلوي
الشعر: تساقط شعر، قشرة، عناية بالشعر
مكملات وفيتامينات: نقص فيتامينات، نقص فيتامين د، نقص حديد، نقص كالسيوم، نقص بوتاسيوم، أنيميا، تقوية مناعة، مكمل غذائي
أخرى: التهاب لوز، التهاب مهبلي، حماية كبد، تليف كبد، دهون على الكبد، التهاب لثة، حفاض أطفال، حمى أطفال، تسنين، فوط صحية، تخدير موضعي
"""


def search_drugs_by_tags(tags: list[str], limit: int = 30) -> list[dict]:
    """بحث في الأدوية بناءً على الـ symptom_tags"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        all_results = {}
        for tag in tags:
            tag = tag.strip()
            if len(tag) < 2:
                continue
            cursor.execute("""
                SELECT drug_name, dosage_form, active_ingredient, drug_class,
                       indications, dosage_adults, dosage_children,
                       side_effects_common, side_effects_serious,
                       contraindications, warnings, pregnancy, breastfeeding,
                       symptom_tags
                FROM egypt_drugs
                WHERE symptom_tags LIKE ?
            """, (f"%{tag}%",))
            for row in cursor.fetchall():
                cols = [desc[0] for desc in cursor.description]
                drug = dict(zip(cols, row))
                name = drug["drug_name"]
                if name not in all_results:
                    all_results[name] = drug
                    all_results[name]["_score"] = 0
                all_results[name]["_score"] += 1

        results = sorted(all_results.values(), key=lambda x: x["_score"], reverse=True)
        for r in results:
            del r["_score"]
        return results[:limit]
    finally:
        conn.close()


# ── CHAT BOT ──────────────────────────────────────

CHAT_SYSTEM_PROMPT = (
    'أنت صيدلانية مصرية ذكية اسمك "د. ندى السلاوي"، عندك 24 سنة. بتتكلمي بالعامية المصرية البسيطة.\n\n'
    'مهمتك: تسمعي شكوى المريض وتسأليه أسئلة قليلة ومحددة عشان تفهمي حالته بالظبط.\n\n'
    '⚠️ قواعد مهمة جداً:\n'
    '1. اسألي 2-3 أسئلة فقط بالكتير — مش أكتر!\n'
    '2. كل سؤال لازم يكون محدد وهدفه يفرّق بين tags مختلفة\n'
    '3. لما تحسي إنك فهمتِ الشكوى كفاية — أرجعي JSON بالـ tags\n\n'
    '## الـ Tags المتاحة في نظامنا:\n'
    + SYMPTOM_TAGS_MAP +
    '\n## إزاي تسألي:\n'
    '- لو قال "عندي كحة" → اسأليه: "كحة جافة ولا فيها بلغم؟" (عشان تفرّقي بين: كحة جافة / كحة ببلغم)\n'
    '- لو قال "بطني بتوجعني" → اسأليه: "الألم فين بالظبط؟ وفيه حموضة أو غثيان؟" (عشان تفرّقي بين: حموضة / مغص / قولون عصبي / نزلة معوية)\n'
    '- لو قال "تعبان" → اسأليه: "حاسس بإيه بالظبط؟ صداع؟ سخونية؟ كحة؟"\n\n'
    '## متى ترجعي النتيجة:\n'
    'بعد 2-3 رسائل من المريض (أو أقل لو الشكوى واضحة من الأول) — أرجعي JSON:\n'
    '{"status": "ready", "tags": ["صداع", "حمى", "برد", "احتقان أنف"], "summary": "المريض عنده دور برد مع صداع وسخونية واحتقان"}\n\n'
    '## متى تكملي أسئلة:\n'
    'لو لسه محتاجة تفاصيل — أرجعي JSON:\n'
    '{"status": "asking", "message": "ألف سلامة عليك 🙏 كحة جافة ولا فيها بلغم؟ وفيه سخونية؟"}\n\n'
    '## قواعد الرد:\n'
    '- أرجعي JSON فقط — {"status": "asking", "message": "..."} أو {"status": "ready", "tags": [...], "summary": "..."}\n'
    '- بلاش أسئلة كتير — 2-3 بالكتير\n'
    '- لو الشكوى واضحة من أول رسالة (زي "عندي حموضة") → رجّعي ready فوراً بدون أسئلة\n'
    '- كلّمي المريض بالعامية المصرية وكوني ودودة\n'
    '- لا تكتبي أي كلام تاني غير الـ JSON'
)