"""
MediVerse - Regenerate RAG Embeddings
=====================================
يحوّل كل embeddings الـ RAG للموديل الجديد multilingual

الموديل القديم: all-MiniLM-L6-v2        (إنجليزي بس)
الموديل الجديد: paraphrase-multilingual-MiniLM-L12-v2 (50+ لغة منهم العربي)

الاتنين: 384 dimensions - فمش محتاج نغير الداتابيز

الاستخدام:
    python regenerate_embeddings.py
"""

import os
import sys
import struct
import time
import pyodbc
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────
NEW_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

DB_DRIVER = os.getenv("DATABASE_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.getenv("DATABASE_SERVER", "localhost")
DB_NAME = os.getenv("DATABASE_NAME", "MediVerse_System")
DB_USER = os.getenv("DATABASE_USER", "")
DB_PASSWORD = os.getenv("DATABASE_PASSWORD", "")


def get_connection_string():
    if DB_USER:
        return (
            f"DRIVER={{{DB_DRIVER}}};"
            f"SERVER={DB_SERVER};"
            f"DATABASE={DB_NAME};"
            f"UID={DB_USER};"
            f"PWD={DB_PASSWORD};"
            "Encrypt=yes;"
        )
    return (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        "Trusted_Connection=yes;"
        "Encrypt=no;"
    )


def main():
    print("=" * 60)
    print("  🔄 MediVerse - Regenerate RAG Embeddings")
    print("=" * 60)
    print()

    # ── Step 1: Load new model ──
    print(f"📦 جاري تحميل الموديل: {NEW_MODEL_NAME}...")
    print("   (أول مرة هيحمل ~500MB، بعد كده هيبقى cached)")
    print()

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(NEW_MODEL_NAME)
        print(f"   ✅ الموديل جاهز!")
    except Exception as e:
        print(f"   ❌ فشل تحميل الموديل: {e}")
        print(f"   جرب: pip install sentence-transformers --break-system-packages")
        sys.exit(1)

    # ── Quick test ──
    print()
    print("🧪 تست سريع - هل الموديل بيفهم عربي؟")
    test_ar = model.encode(["عندي صداع شديد ودوخة"], show_progress_bar=False)[0]
    test_en = model.encode(["I have severe headache and dizziness"], show_progress_bar=False)[0]
    sim = np.dot(test_ar, test_en) / (np.linalg.norm(test_ar) * np.linalg.norm(test_en))
    print(f"   'عندي صداع شديد ودوخة' ↔ 'severe headache and dizziness'")
    print(f"   Similarity: {sim:.4f} (كل ما يقرب من 1 كل ما يكون أحسن)")
    if sim > 0.7:
        print(f"   ✅ ممتاز! الموديل بيفهم عربي وإنجليزي")
    elif sim > 0.5:
        print(f"   ✅ كويس!")
    else:
        print(f"   ⚠️ ضعيف - ممكن في مشكلة")
    print()

    # ── Step 2: Connect to DB ──
    print("🔌 جاري الاتصال بالداتابيز...")
    try:
        conn = pyodbc.connect(get_connection_string())
        cursor = conn.cursor()
        print("   ✅ متصل!")
    except Exception as e:
        print(f"   ❌ فشل الاتصال: {e}")
        sys.exit(1)

    # ── Step 3: Read all rows ──
    print()
    print("📖 جاري قراءة بيانات الـ RAG...")
    cursor.execute("SELECT id, symptoms_text FROM symptoms_knowledge_base")
    rows = cursor.fetchall()
    total = len(rows)
    print(f"   📊 لقيت {total} صف في symptoms_knowledge_base")
    print()

    if total == 0:
        print("   ⚠️ الجدول فاضي! مفيش بيانات لتحويلها.")
        conn.close()
        sys.exit(0)

    # ── Step 4: Regenerate embeddings ──
    print(f"🔄 جاري إعادة توليد الـ embeddings بـ {NEW_MODEL_NAME}...")
    print()

    success = 0
    failed = 0
    start_time = time.time()

    for i, (row_id, symptoms_text) in enumerate(rows, 1):
        try:
            # Generate new embedding
            emb = model.encode([symptoms_text], show_progress_bar=False)[0]
            emb = np.asarray(emb, dtype=np.float32)

            # Convert to bytes for DB storage
            emb_bytes = emb.tobytes()

            # Also create the comma-separated string format (for symptoms_embedding column)
            emb_str = ",".join(str(float(x)) for x in emb)

            # Update both columns
            cursor.execute("""
                UPDATE symptoms_knowledge_base 
                SET symptoms_embedding_bytes = ?,
                    symptoms_embedding = ?
                WHERE id = ?
            """, (emb_bytes, emb_str, row_id))

            success += 1

            # Progress bar
            pct = (i / total) * 100
            bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
            elapsed = time.time() - start_time
            eta = (elapsed / i) * (total - i) if i > 0 else 0
            print(f"\r   [{bar}] {pct:5.1f}% ({i}/{total}) | ✅ {success} | ❌ {failed} | ETA: {eta:.0f}s", end="", flush=True)

        except Exception as e:
            failed += 1
            print(f"\n   ❌ Row {row_id} failed: {e}")

    conn.commit()
    elapsed = time.time() - start_time

    print()
    print()
    print("=" * 60)
    print(f"  ✅ انتهى! تم تحديث {success}/{total} صف")
    print(f"  ⏱️  الوقت: {elapsed:.1f} ثانية")
    print(f"  📦 الموديل: {NEW_MODEL_NAME}")
    print(f"  📐 Dimensions: 384")
    if failed > 0:
        print(f"  ❌ فشل: {failed} صف")
    print("=" * 60)
    print()

    # ── Step 5: Verify ──
    print("🧪 تحقق نهائي...")
    cursor.execute("""
        SELECT TOP 1 symptoms_text, symptoms_embedding_bytes 
        FROM symptoms_knowledge_base
    """)
    row = cursor.fetchone()
    if row:
        test_emb = np.frombuffer(row[1], dtype=np.float32)
        print(f"   النص: {row[0][:80]}...")
        print(f"   Embedding size: {test_emb.shape[0]} dimensions")
        print(f"   Sample values: [{test_emb[0]:.6f}, {test_emb[1]:.6f}, {test_emb[2]:.6f}, ...]")

        if test_emb.shape[0] == 384:
            print(f"   ✅ كل حاجة تمام! الـ RAG جاهز يشتغل بالعربي والإنجليزي")
        else:
            print(f"   ❌ حجم الـ embedding غلط! المفروض 384 لقيت {test_emb.shape[0]}")

    # ── Arabic search test ──
    print()
    print("🔍 تست بحث بالعربي...")
    test_query = "عندي طفح جلدي أحمر وحكة"
    q_emb = model.encode([test_query], show_progress_bar=False)[0]

    cursor.execute("SELECT symptoms_text, symptoms_embedding_bytes FROM symptoms_knowledge_base")
    best_match = None
    best_sim = -1

    for row in cursor.fetchall():
        try:
            db_emb = np.frombuffer(row[1], dtype=np.float32)
            sim = np.dot(q_emb, db_emb) / (np.linalg.norm(q_emb) * np.linalg.norm(db_emb) + 1e-8)
            if sim > best_sim:
                best_sim = sim
                best_match = row[0]
        except:
            continue

    print(f"   Query (عربي): '{test_query}'")
    print(f"   Best match:    '{best_match[:80]}...' " if best_match and len(best_match) > 80 else f"   Best match:    '{best_match}'")
    print(f"   Similarity:     {best_sim:.4f}")
    if best_sim > 0.5:
        print(f"   ✅ البحث بالعربي شغال!")
    else:
        print(f"   ⚠️ الـ similarity ضعيفة - ممكن تحتاج تخفض RAG_THRESHOLD في .env")

    cursor.close()
    conn.close()

    print()
    print("🎉 خلاص! دلوقتي شغّل السيرفر والمريض يقدر يكتب بالعربي")
    print("   الموديل هيفهم الأعراض بالعربي ويلاقي أقرب تشخيص في الـ RAG")
    print()


if __name__ == "__main__":
    main()
