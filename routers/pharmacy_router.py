"""
Pharmacy Router — integrates the pharmacy agent into MediVerse.
Endpoints prefixed with /pharmacy/
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional
import base64
import json
import logging

logger = logging.getLogger("mediverse.pharmacy")

router = APIRouter(prefix="/pharmacy", tags=["Pharmacy Agent"])

# Import functions from the pharmacy service
from services.pharmacy_service import (
    get_patient, smart_recommend,
    find_alternatives, check_drug_safety, get_drug_details,
    search_drugs_by_name, fuzzy_find_drug, semantic_search,
    build_embeddings, load_embeddings, check_prescription_interactions,
    _drug_embeddings, _drug_names, EMBEDDINGS_FILE,
    _fallback_keyword_recommend, call_llm,
)

# ── Models ──
class ComplaintRequest(BaseModel):
    patient_id: int
    complaint: str

class AlternativeRequest(BaseModel):
    patient_id: int
    drug_name: str

class DrugSearchRequest(BaseModel):
    query: str

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    patient_id: int
    messages: list[ChatMessage]


@router.post("/recommend")
def recommend_drugs(req: ComplaintRequest):
    """Smart drug recommendation based on complaint + patient history."""
    patient = get_patient(req.patient_id)
    if not patient:
        raise HTTPException(404, "المريض مش موجود")
    result = smart_recommend(req.complaint, patient)
    return result


@router.post("/prescription/analyze")
async def analyze_prescription_endpoint(
    image: UploadFile = File(...),
    patient_id: Optional[int] = Form(None)
):
    """Analyze prescription — returns JSON result (non-streaming)."""
    try:
        content = await image.read()
        ext = (image.filename or "").rsplit(".", 1)[-1].lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        from services.prescription_service import analyze_prescription as analyze_rx
        result = analyze_rx(content, mime, patient_id)
        return result
    except Exception as e:
        logger.error(f"Prescription endpoint error: {e}")
        return {"success": False, "error": str(e)}


@router.post("/prescription/analyze-stream")
async def analyze_prescription_stream(
    image: UploadFile = File(...),
    patient_id: Optional[int] = Form(None)
):
    """Analyze prescription with real-time SSE progress — async generator."""
    from fastapi.responses import StreamingResponse
    import asyncio

    content = await image.read()
    ext = (image.filename or "").rsplit(".", 1)[-1].lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")

    async def event_stream():
        import base64 as b64mod
        from services.prescription_service import (
            _pass1_extract, _step2_db_match, _pass2_confirm, _step4_analyze, VISION_MODEL
        )

        def sse(step, status, extra=None):
            event = {"step": step, "status": status, **(extra or {})}
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        image_b64 = b64mod.b64encode(content).decode("utf-8")

        # Pass 1
        yield sse("pass1", "running")
        try:
            drugs = await asyncio.to_thread(_pass1_extract, image_b64, mime)
            yield sse("pass1", "done", {"count": len(drugs), "drugs": [d["masked"] for d in drugs]})
        except Exception as e:
            yield sse("pass1", "error", {"error": str(e)})
            yield sse("result", "done", {"data": {"success": False, "error": str(e)}})
            return

        if not drugs:
            yield sse("result", "done", {"data": {"success": True, "analysis": None, "warning": "No medications"}})
            return

        # DB
        yield sse("db", "running")
        try:
            enriched = await asyncio.to_thread(_step2_db_match, drugs)
            db_found = sum(1 for d in enriched if d["found_in_db"])
            yield sse("db", "done", {"found": db_found, "total": len(enriched)})
        except Exception as e:
            yield sse("db", "error", {"error": str(e)})
            enriched = [{"masked": d["masked"], "anchors": d["anchors"], "guess": d["guess"],
                         "db_candidates": [], "best_match": None, "best_match_details": None,
                         "found_in_db": False} for d in drugs]
            db_found = 0

        # Pass 2
        yield sse("pass2", "running")
        try:
            enriched = await asyncio.to_thread(_pass2_confirm, image_b64, mime, enriched)
            yield sse("pass2", "done", {"confirmed": [d.get("confirmed", "?") for d in enriched]})
        except Exception as e:
            yield sse("pass2", "error", {"error": str(e)})
            for d in enriched:
                d["confirmed"] = d.get("best_match") or d["guess"]

        # Analysis
        yield sse("analysis", "running")
        try:
            analysis = await asyncio.to_thread(_step4_analyze, enriched, patient_id)
            yield sse("analysis", "done")
        except Exception as e:
            yield sse("analysis", "error", {"error": str(e)})
            analysis = {"success": False, "error": str(e)}

        # Final result
        result = {
            "success": True, "version": "v6",
            "ocr": {"medications_raw": [{"masked": d["masked"], "anchors": d["anchors"], "guess": d["guess"]} for d in drugs], "model": VISION_MODEL},
            "db_matching": {"total": len(enriched), "found_in_db": db_found,
                "details": [{"ocr": d["masked"], "anchors": d["anchors"],
                             "confirmed": d.get("confirmed", d.get("best_match", d["guess"])),
                             "best_match": d["best_match"], "found": d["found_in_db"],
                             "candidates": d["db_candidates"][:4]} for d in enriched]},
            "analysis": analysis,
        }
        yield sse("result", "done", {"data": result})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.post("/alternative")
def find_drug_alternative(req: AlternativeRequest):
    """Find alternative drugs with safety checks."""
    patient = get_patient(req.patient_id)
    if not patient:
        raise HTTPException(404, "المريض مش موجود")

    fuzzy = fuzzy_find_drug(req.drug_name)
    if not fuzzy["best_match"]:
        return {"success": False, "message": "مش لاقي دوا بالاسم ده", "suggestions": [], "did_you_mean": None}

    if not fuzzy["exact_match"]:
        return {
            "success": True, "did_you_mean": fuzzy["suggestions"], "exact_match": False,
            "message": f"مش لاقي '{req.drug_name}' بالظبط — هل تقصد واحد من دول؟",
            "alternatives": [], "original_drug": None, "total_found": 0, "total_safe": 0
        }

    matched_name = fuzzy["best_match"]
    alternatives = find_alternatives(matched_name, limit=10)
    safe_alternatives = []
    for alt in alternatives:
        safety = check_drug_safety(alt, patient)
        alt["safety"] = safety
        if safety["safe"] or safety["severity"] == "warning":
            safe_alternatives.append(alt)

    original = get_drug_details(matched_name)
    return {
        "success": True, "exact_match": True, "did_you_mean": None,
        "original_drug": original, "alternatives": safe_alternatives,
        "total_found": len(alternatives), "total_safe": len(safe_alternatives)
    }


@router.post("/drug/search")
def search_drug(req: DrugSearchRequest):
    """Search drugs by name with fuzzy matching."""
    results = search_drugs_by_name(req.query, limit=15)
    if not results:
        fuzzy = fuzzy_find_drug(req.query)
        if fuzzy["suggestions"]:
            fuzzy_results = [get_drug_details(name) for name in fuzzy["suggestions"][:5]]
            fuzzy_results = [r for r in fuzzy_results if r]
            return {"success": True, "results": fuzzy_results, "count": len(fuzzy_results),
                    "fuzzy_match": True, "message": f"مش لاقي '{req.query}' بالظبط — دي أقرب نتائج:"}
    return {"success": True, "results": results, "count": len(results), "fuzzy_match": False}


@router.post("/drug/interactions")
def check_interactions(drug_names: list[str]):
    """Check drug-drug interactions."""
    drugs_data = []
    for name in drug_names:
        details = get_drug_details(name)
        if details:
            drugs_data.append({"details": details, "found_in_db": True})
    warnings = check_prescription_interactions(drugs_data)
    return {"success": True, "warnings": warnings, "drugs_checked": len(drugs_data)}


@router.post("/embeddings/build")
def build_embeddings_endpoint():
    """Build drug embeddings (run once)."""
    try:
        count = build_embeddings()
        return {"success": True, "message": f"تم بناء embeddings لـ {count} دوا", "count": count}
    except Exception as e:
        raise HTTPException(500, f"خطأ: {str(e)}")


@router.get("/embeddings/status")
def embeddings_status():
    """Check embeddings status."""
    from services.pharmacy_service import _drug_embeddings, _drug_names, EMBEDDINGS_FILE
    return {
        "loaded": _drug_embeddings is not None,
        "count": len(_drug_names) if _drug_names else 0,
        "file_exists": EMBEDDINGS_FILE.exists(),
    }


@router.post("/chat")
def pharmacy_chat(req: ChatRequest):
    """SSE streaming pharmacy chat with tag-based drug search."""
    import re, json
    from difflib import SequenceMatcher
    from fastapi.responses import StreamingResponse
    from services.pharmacy_service import (
        call_llm, call_llm_chat, call_llm_chat_stream,
        search_drugs_by_tags, search_drugs_by_indication,
        get_drug_details, check_drug_safety, semantic_search,
        CHAT_SYSTEM_PROMPT
    )

    patient = get_patient(req.patient_id)
    if not patient:
        raise HTTPException(404, "المريض مش موجود")

    messages = req.messages

    # Build system prompt with patient data
    patient_info = (
        f"\n\nبيانات المريض (للسلامة فقط — لا ترشح أدوية لأمراضه المزمنة):\n"
        f"- الاسم: {patient.get('full_name', 'مريض')} | العمر: {patient.get('age', 'غير محدد')}\n"
        f"- أمراض مزمنة: {patient.get('chronic_diseases', 'لا يوجد')}\n"
        f"- حساسيات: {patient.get('allergies', 'لا يوجد')}\n"
        f"- أدوية حالية: {patient.get('current_medications', 'لا يوجد')}\n"
        f"\n⚠️ لا ترشح أدوية سكر/ضغط/كوليسترول إلا لو المريض سأل عنها بنفسه.\n"
        f"⚠️ لما ترجع ready: لازم الـ tags تغطي كل الأعراض اللي المريض ذكرها (مثلاً: كحة جافة + حمى → tags تشمل الاتنين)."
    )
    full_system = CHAT_SYSTEM_PROMPT + patient_info

    chat_messages = [{"role": "system", "content": full_system}]
    for m in messages:
        chat_messages.append({"role": m.role, "content": m.content})

    def generate():
        try:
            # Call LLM (non-streaming for JSON parsing reliability)
            raw = call_llm_chat(chat_messages)

            # Clean response
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"): clean = clean[:-3]
                clean = clean.strip()

            # Parse JSON
            try:
                result = json.loads(clean)
            except:
                # Try to extract JSON from text
                jm = re.search(r'\{.*\}', clean, re.DOTALL)
                if jm:
                    result = json.loads(jm.group())
                else:
                    # Fallback — treat as asking message
                    yield f"data: {json.dumps({'t':'c','d': clean}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'t':'done','s':'asking'}, ensure_ascii=False)}\n\n"
                    return

            status = result.get("status", "asking")

            if status == "asking":
                # Stream the message word by word for typing effect
                msg = result.get("message", "")
                words = msg.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words)-1 else "")
                    yield f"data: {json.dumps({'t':'c','d': chunk}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'t':'done','s':'asking'}, ensure_ascii=False)}\n\n"
                return

            # === STATUS: READY → SEARCH DRUGS ===
            tags = result.get("tags", [])
            summary = result.get("summary", "")

            # Stream the summary first
            nl = "\n"
            yield f"data: {json.dumps({'t':'c','d': f'✅ فهمت شكوتك{nl}{summary}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'t':'search'}, ensure_ascii=False)}\n\n"

            # Search by tags (primary) + semantic (backup)
            drugs = search_drugs_by_tags(tags, limit=25)
            if len(drugs) < 5:
                kw_drugs = search_drugs_by_indication(tags, limit=20)
                for d in kw_drugs:
                    if d["drug_name"] not in [x["drug_name"] for x in drugs]:
                        drugs.append(d)
            if len(drugs) < 5:
                for r in semantic_search(summary, top_k=15):
                    d = get_drug_details(r["drug_name"])
                    if d and d["drug_name"] not in [x["drug_name"] for x in drugs]:
                        drugs.append(d)

            # Safety check
            safe_drugs, unsafe_drugs = [], []
            for drug in drugs:
                safety = check_drug_safety(drug, patient)
                drug["safety"] = safety
                if safety["safe"] or safety["severity"] == "warning":
                    safe_drugs.append(drug)
                else:
                    unsafe_drugs.append(drug)

            # LLM picks best drugs — include ALL tags/symptoms in the prompt
            ds = [{"name":d["drug_name"],"class":d.get("drug_class",""),"form":d.get("dosage_form",""),
                   "ingredient":d.get("active_ingredient",""),"indications":(d.get("indications","") or "")[:150]}
                  for d in safe_drugs[:20]]

            rec_prompt = (
                f"أنت صيدلي مصري. شكوى المريض وأعراضه: {summary}\n"
                f"الأعراض/Tags: {', '.join(tags)}\n"
                f"حساسيات: {patient.get('allergies','لا يوجد')}\n"
                f"أمراض مزمنة (للسلامة فقط): {patient.get('chronic_diseases','لا يوجد')}\n\n"
                f"الأدوية المتاحة:\n{json.dumps(ds, ensure_ascii=False)}\n\n"
                "⚠️ قواعد صارمة:\n"
                "1. لازم ترشح أدوية تغطي كل الأعراض/Tags — مش عرض واحد بس!\n"
                f"   مثال: لو الأعراض [{', '.join(tags)}] → لازم ترشح دوا لكل عرض منهم\n"
                "2. لا تختار أدوية سكر/ضغط/كوليسترول إلا لو في الأعراض\n"
                "3. لو مفيش دوا مناسب لعرض معين في القائمة → اذكر ده في general_advice\n\n"
                "اشرح كل دوا بيعالج أنهي عرض بالعامية المصرية.\n\n"
                'أرجع JSON فقط:\n{"recommendations":[{"drug_name":"الاسم بالظبط","reason":"بيعالج [العرض]: شرح بالعامية","dosage_note":"الجرعة","priority":1}],"general_advice":"نصيحة"}'
            )

            try:
                rr = call_llm(rec_prompt, "رشح")
                rr_clean = rr.strip()
                if rr_clean.startswith("```"): rr_clean = rr_clean.split("\n",1)[1].rsplit("```",1)[0].strip()
                rj = re.search(r'\{.*\}', rr_clean, re.DOTALL)
                recs = json.loads(rj.group()) if rj else {"recommendations":[],"general_advice":""}
            except:
                recs = {"recommendations":[{"drug_name":d["drug_name"],"reason":f"{d.get('drug_class','')}","priority":i+1} for i,d in enumerate(safe_drugs[:5])],"general_advice":""}

            # Bind drug details with fuzzy matching
            for rec in recs.get("recommendations", []):
                rn = (rec.get("drug_name","") or "").strip().upper()
                best = None; bs = 0
                for drug in safe_drugs:
                    dn = drug["drug_name"].upper()
                    if rn == dn or rn in dn or dn in rn: best=drug; break
                    if rn.split()[0] == dn.split()[0]: best=drug; break
                    s = SequenceMatcher(None, rn, dn).ratio()
                    if s > bs and s > 0.6: bs=s; best=drug
                if best: rec["details"] = best

            result = {
                "t":"done","s":"done",
                "recommendations":recs.get("recommendations",[]),
                "general_advice":recs.get("general_advice",""),
                "unsafe_drugs":[{"name":d["drug_name"],"reason":d["safety"]["warnings"]} for d in unsafe_drugs[:5]],
                "total_found":len(drugs),"total_safe":len(safe_drugs)
            }
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield f"data: {json.dumps({'t':'c','d': f'⚠️ حصل خطأ — جرب تاني'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'t':'done','s':'asking'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
