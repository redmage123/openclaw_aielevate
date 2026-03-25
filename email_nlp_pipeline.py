#!/usr/bin/env python3
"""Email NLP Pipeline — real-time classification with full NLP stack.

Stores all emails compressed + encrypted.
Trains LDA incrementally on every email (background thread).
Full NLP pipeline: LDA + TF-IDF + NER + POS + Fuzzy + Bayesian.

Architecture:
    Email arrives → store (compressed + encrypted) → NLP pipeline → classification
                                                   ↓
                                    Background thread: incremental LDA retrain

NLP Layers:
    1. TF-IDF — term importance weighting
    2. LDA — topic distribution
    3. NER — extract entities (people, orgs, project refs)
    4. POS tagging — verb/noun patterns indicate intent
    5. Fuzzy matching — match against known intents even with typos
    6. Naive Bayes — supervised classifier trained on labeled data
    7. KG + RAG enrichment — context from knowledge graph and email history

All layers vote. Weighted ensemble produces final classification.
"""

import os
import sys
import json
import gzip
import hashlib
import pickle
import logging
import threading
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from cryptography.fernet import Fernet

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("email-nlp")

# ── Configuration ──────────────────────────────────────────────────────

MODEL_DIR = Path("/opt/ai-elevate/models/nlp")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_STORE_DIR = Path("/opt/ai-elevate/email-store")
EMAIL_STORE_DIR.mkdir(parents=True, exist_ok=True)

ENCRYPTION_KEY_PATH = Path("/opt/ai-elevate/credentials/email-store-key.txt")
AUDIT_LOG = Path("/var/log/openclaw/shared/classification-audit.jsonl")

# Intent labels
INTENTS = [
    "new_inquiry", "status_inquiry", "bug_report", "revision_request",
    "acceptance", "scope_change", "feedback", "billing", "internal_ops",
    "project_delivery", "onboarding", "general",
]

# Keyword priors per intent
INTENT_KEYWORDS = {
    "new_inquiry": ["new project", "quote", "estimate", "looking for", "need a developer", "interested in", "build us"],
    "status_inquiry": ["status", "update", "progress", "where are we", "what's happening", "how is", "ETA", "timeline", "what's going on"],
    "bug_report": ["bug", "broken", "crash", "error", "not working", "500", "502", "503", "down", "failed", "issue"],
    "revision_request": ["revision", "change this", "update the", "modify", "tweak", "adjust", "redo", "fix the"],
    "acceptance": ["approved", "looks great", "go live", "ship it", "love it", "accepted", "go ahead", "perfect"],
    "scope_change": ["scope", "additional", "also want", "new feature", "add to", "expand", "on top of"],
    "feedback": ["feedback", "review", "thoughts on", "opinion", "how was", "rate"],
    "billing": ["invoice", "payment", "billing", "charge", "refund", "subscription", "receipt", "cost"],
    "internal_ops": ["internal", "ops", "agent", "workflow", "deploy", "server", "cron", "config"],
    "project_delivery": ["delivered", "handover", "documentation", "go-live", "launch", "deploy to production", "sign-off"],
    "onboarding": ["welcome", "getting started", "onboard", "setup", "kick-off", "introduction"],
    "general": [],
}

# POS patterns that indicate intent
POS_INTENT_PATTERNS = {
    "status_inquiry": ["WRB VBZ", "WP VBZ", "WRB VBP"],  # "what is", "where are", "how is"
    "bug_report": ["NN VBZ RB VBG", "NN VBZ JJ"],  # "server is not working", "site is broken"
    "acceptance": ["VBZ JJ", "VB PRP"],  # "looks great", "ship it"
    "new_inquiry": ["MD VB", "VBP VBG IN"],  # "can you", "looking for"
    "revision_request": ["VB DT NN", "VB DT"],  # "change the X", "update the"
}

# ── Encryption ─────────────────────────────────────────────────────────

def _get_fernet():
    """Get or create Fernet encryption key."""
    if ENCRYPTION_KEY_PATH.exists():
        key = ENCRYPTION_KEY_PATH.read_text().strip().encode()
    else:
        key = Fernet.generate_key()
        ENCRYPTION_KEY_PATH.write_text(key.decode())
        os.chmod(str(ENCRYPTION_KEY_PATH), 0o600)
    return Fernet(key)


def store_email(sender, recipient, subject, body, direction="inbound", agent_id=""):
    """Store email compressed + encrypted."""
    fernet = _get_fernet()

    email_data = json.dumps({
        "sender": sender,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "direction": direction,
        "agent_id": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    # Compress then encrypt
    compressed = gzip.compress(email_data, compresslevel=6)
    encrypted = fernet.encrypt(compressed)

    # Store with hash-based filename
    email_hash = hashlib.sha256(f"{sender}{subject}{time.time()}".encode()).hexdigest()[:16]
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = EMAIL_STORE_DIR / f"{date_prefix}_{email_hash}.enc"
    filepath.write_bytes(encrypted)

    return str(filepath)


def read_email(filepath):
    """Read and decrypt a stored email."""
    fernet = _get_fernet()
    encrypted = Path(filepath).read_bytes()
    compressed = fernet.decrypt(encrypted)
    data = gzip.decompress(compressed)
    return json.loads(data)


def get_all_stored_emails(limit=1000):
    """Read all stored emails for training."""
    emails = []
    files = sorted(EMAIL_STORE_DIR.glob("*.enc"), reverse=True)[:limit]
    fernet = _get_fernet()
    for f in files:
        try:
            encrypted = f.read_bytes()
            compressed = fernet.decrypt(encrypted)
            data = gzip.decompress(compressed)
            emails.append(json.loads(data))
        except Exception:
            continue
    return emails


# ── NLP Components ─────────────────────────────────────────────────────

def _preprocess(text):
    """Clean text for NLP."""
    text = text.lower()
    text = re.sub(r'^>.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'on .* wrote:', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\S+@\S+', '', text)
    text = re.sub(r'[^a-z0-9\s\'-]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _tfidf_score(text):
    """TF-IDF weighted keyword matching."""
    model_path = MODEL_DIR / "tfidf_vectorizer.pkl"
    if model_path.exists():
        with open(model_path, "rb") as f:
            vectorizer = pickle.load(f)
        vec = vectorizer.transform([_preprocess(text)])
        feature_names = vectorizer.get_feature_names_out()
        scores = {}
        for intent, keywords in INTENT_KEYWORDS.items():
            intent_score = 0
            for kw in keywords:
                for word in kw.split():
                    if word in feature_names:
                        idx = list(feature_names).index(word)
                        intent_score += vec[0, idx]
            scores[intent] = float(intent_score)
        return scores
    # Fallback: simple keyword count
    return _keyword_score(text)


def _keyword_score(text):
    """Simple keyword matching scores."""
    scores = {}
    text_lower = text.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[intent] = score
    return scores


def _ner_extract(text):
    """Extract named entities — project refs, people, orgs."""
    entities = {
        "project_refs": re.findall(r'GF-\d+|gf-\d+|CC-\d+', text),
        "emails": re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', text),
        "urls": re.findall(r'https?://\S+', text),
        "money": re.findall(r'[\$€£]\s*[\d,]+(?:\.\d+)?', text),
        "dates": re.findall(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}', text),
    }
    # Simple person name detection (capitalized word pairs)
    entities["people"] = re.findall(r'[A-Z][a-z]+ [A-Z][a-z]+', text)
    return entities


def _pos_classify(text):
    """POS-tag based classification using simple patterns."""
    scores = {}
    text_lower = text.lower()

    # Question patterns → status_inquiry
    if re.search(r'^(what|where|how|when|why|is there|are there|do we|can you)\b', text_lower):
        scores["status_inquiry"] = 2
    if text_lower.endswith("?"):
        scores["status_inquiry"] = scores.get("status_inquiry", 0) + 1

    # Imperative patterns → revision_request or new_inquiry
    if re.search(r'^(please |can you |could you |i need |we need )', text_lower):
        scores["revision_request"] = 1
        scores["new_inquiry"] = 1

    # Approval patterns → acceptance
    if re.search(r'^(yes|approved|looks? great|perfect|go ahead|ship)', text_lower):
        scores["acceptance"] = 3

    # Complaint patterns → bug_report
    if re.search(r"(doesn't work|isn't working|broken|crashed|error|failing)", text_lower):
        scores["bug_report"] = 2

    # Past tense delivery → project_delivery
    if re.search(r"(delivered|completed|finished|shipped|launched|deployed)", text_lower):
        scores["project_delivery"] = 2

    return scores


def _fuzzy_match(text):
    """Fuzzy matching against intent keywords — handles typos and variations."""
    try:
        from fuzzywuzzy import fuzz
    except ImportError:
        try:
            from thefuzz import fuzz
        except ImportError:
            return {}

    scores = {}
    words = text.lower().split()

    for intent, keywords in INTENT_KEYWORDS.items():
        best_score = 0
        for kw in keywords:
            for i in range(len(words)):
                window = " ".join(words[i:i+len(kw.split())+1])
                ratio = fuzz.partial_ratio(kw, window)
                if ratio > 75:  # Threshold for fuzzy match
                    best_score = max(best_score, ratio / 100)
        if best_score > 0:
            scores[intent] = best_score

    return scores


def _naive_bayes_classify(text):
    """Naive Bayes classifier trained on labeled email data."""
    model_path = MODEL_DIR / "naive_bayes.pkl"
    vec_path = MODEL_DIR / "nb_vectorizer.pkl"

    if model_path.exists() and vec_path.exists():
        try:
            with open(vec_path, "rb") as f:
                vectorizer = pickle.load(f)
            with open(model_path, "rb") as f:
                model = pickle.load(f)

            vec = vectorizer.transform([_preprocess(text)])
            probs = model.predict_proba(vec)[0]
            classes = model.classes_
            return {cls: float(prob) for cls, prob in zip(classes, probs)}
        except Exception as e:
            log.debug(f"Naive Bayes failed: {e}")

    return {}


def _lda_classify(text):
    """LDA topic model classification."""
    model_path = MODEL_DIR / "lda_model.pkl"
    vec_path = MODEL_DIR / "lda_vectorizer.pkl"

    if model_path.exists() and vec_path.exists():
        try:
            with open(vec_path, "rb") as f:
                vectorizer = pickle.load(f)
            with open(model_path, "rb") as f:
                model = pickle.load(f)

            vec = vectorizer.transform([_preprocess(text)])
            topic_dist = model.transform(vec)[0]
            return {INTENTS[i] if i < len(INTENTS) else f"topic_{i}": float(p)
                    for i, p in enumerate(topic_dist)}
        except Exception as e:
            log.debug(f"LDA failed: {e}")

    return {}


def _kg_enrich(sender, subject, body):
    """Knowledge graph context enrichment."""
    context = {}
    try:
        from knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph("gigforge")
        sender_data = kg.query(f"person:{sender}")
        if sender_data:
            context["sender_role"] = sender_data.get("role", "unknown")
            context["sender_is_internal"] = sender_data.get("internal", False)

        refs = re.findall(r'GF-\d+|gf-\d+', f"{subject} {body}")
        if refs:
            project_data = kg.query(f"project:{refs[0].upper()}")
            if project_data:
                context["project_status"] = project_data.get("status", "unknown")
    except Exception:
        pass
    return context


def _rag_precedent(body, top_k=3):
    """RAG similarity search for precedent emails."""
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
        cur = conn.cursor()
        clean = _preprocess(body)[:200]
        cur.execute("""
            SELECT classification, COUNT(*) as cnt
            FROM email_interactions
            WHERE classification IS NOT NULL
            AND (subject ILIKE %s OR body_text ILIKE %s)
            GROUP BY classification ORDER BY cnt DESC LIMIT %s
        """, (f"%{clean[:50]}%", f"%{clean[:50]}%", top_k))
        rows = cur.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows if row[0]}
    except Exception:
        return {}


# ── Ensemble Classifier ───────────────────────────────────────────────

# Weights for each classifier (sum to 1.0)
WEIGHTS = {
    "keywords": 0.20,
    "tfidf": 0.15,
    "lda": 0.15,
    "pos": 0.15,
    "fuzzy": 0.10,
    "bayes": 0.15,
    "kg_rag": 0.10,
}


def classify_email(sender, subject, body):
    """Full NLP ensemble classification.

    Runs all classifiers, weights their votes, produces final intent.
    """
    full_text = f"{subject}\n\n{body}"

    # Run all classifiers
    kw_scores = _keyword_score(full_text)
    tfidf_scores = _tfidf_score(full_text)
    lda_scores = _lda_classify(full_text)
    pos_scores = _pos_classify(full_text)
    fuzzy_scores = _fuzzy_match(full_text)
    bayes_scores = _naive_bayes_classify(full_text)

    # KG + RAG enrichment
    kg_context = _kg_enrich(sender, subject, body)
    rag_scores = _rag_precedent(body)

    # Normalize each scorer to sum to 1
    def normalize(scores):
        total = sum(scores.values()) or 1
        return {k: v / total for k, v in scores.items()}

    classifiers = {
        "keywords": normalize(kw_scores) if kw_scores else {},
        "tfidf": normalize(tfidf_scores) if tfidf_scores else {},
        "lda": normalize(lda_scores) if lda_scores else {},
        "pos": normalize(pos_scores) if pos_scores else {},
        "fuzzy": normalize(fuzzy_scores) if fuzzy_scores else {},
        "bayes": normalize(bayes_scores) if bayes_scores else {},
        "kg_rag": normalize(rag_scores) if rag_scores else {},
    }

    # Weighted ensemble vote
    ensemble = defaultdict(float)
    active_weight = 0
    for name, scores in classifiers.items():
        if scores:
            weight = WEIGHTS[name]
            active_weight += weight
            for intent, score in scores.items():
                if intent in INTENTS:
                    ensemble[intent] += weight * score

    # Normalize by active weight
    if active_weight > 0:
        ensemble = {k: v / active_weight for k, v in ensemble.items()}

    # KG context adjustments
    if kg_context.get("sender_is_internal") or kg_context.get("sender_role") == "owner":
        if kg_context.get("project_status") in ("delivered", "complete"):
            ensemble["status_inquiry"] = ensemble.get("status_inquiry", 0) * 1.5
            ensemble["bug_report"] = ensemble.get("bug_report", 0) * 0.5

    # Final normalization
    total = sum(ensemble.values()) or 1
    ensemble = {k: v / total for k, v in ensemble.items()}

    # Determine result
    if ensemble:
        top_intent = max(ensemble, key=ensemble.get)
        confidence = ensemble[top_intent]
    else:
        top_intent = "general"
        confidence = 0.5

    method = "nlp_ensemble"

    # LLM fallback if low confidence
    if confidence < 0.60:
        try:
            from intent_classifier import classify_intent_llm
            llm_result = classify_intent_llm(subject, body)
            if llm_result and llm_result.get("confidence", 0) > confidence:
                top_intent = llm_result["type"]
                confidence = llm_result["confidence"]
                method = "llm_fallback"
        except Exception:
            pass

    # Extract entities
    entities = _ner_extract(full_text)

    result = {
        "intent": top_intent,
        "confidence": confidence,
        "topics": dict(ensemble),
        "method": method,
        "entities": entities,
        "kg_context": kg_context,
        "classifier_votes": {k: dict(v) for k, v in classifiers.items() if v},
    }

    # Audit log
    _audit_log(sender, subject, result)

    return result


# ── Background Training Thread ─────────────────────────────────────────

_training_lock = threading.Lock()
_training_queue = []


def queue_training(sender, subject, body, classification=None):
    """Add an email to the incremental training queue."""
    _training_queue.append({
        "text": _preprocess(f"{subject} {body}"),
        "classification": classification,
        "timestamp": time.time(),
    })

    # Trigger training if queue has enough samples
    if len(_training_queue) >= 5:
        thread = threading.Thread(target=_incremental_train, daemon=True)
        thread.start()


def _incremental_train():
    """Retrain models on new data (runs in background thread)."""
    if not _training_lock.acquire(blocking=False):
        return  # Another training already running

    try:
        from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
        from sklearn.decomposition import LatentDirichletAllocation
        from sklearn.naive_bayes import MultinomialNB

        # Collect training data — stored emails + DB emails
        texts = []
        labels = []

        # From queue
        queue_copy = list(_training_queue)
        _training_queue.clear()
        for item in queue_copy:
            texts.append(item["text"])
            if item.get("classification"):
                labels.append(item["classification"])
            else:
                labels.append(None)

        # From stored encrypted emails
        stored = get_all_stored_emails(limit=500)
        for email in stored:
            text = _preprocess(f"{email.get('subject', '')} {email.get('body', '')}")
            if len(text) > 10:
                texts.append(text)
                labels.append(None)

        # From DB
        try:
            import psycopg2
            conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag", user="rag", password="rag_vec_2026")
            cur = conn.cursor()
            cur.execute("SELECT subject, COALESCE(body_text, ''), classification FROM email_interactions WHERE subject IS NOT NULL")
            for row in cur.fetchall():
                text = _preprocess(f"{row[0]} {row[1]}")
                if len(text) > 10:
                    texts.append(text)
                    labels.append(row[2])
            conn.close()
        except Exception:
            pass

        if len(texts) < 10:
            return

        log.info(f"Incremental training on {len(texts)} documents")

        # Train TF-IDF vectorizer
        tfidf = TfidfVectorizer(max_df=0.95, min_df=2, max_features=2000, stop_words="english")
        try:
            tfidf_matrix = tfidf.fit_transform(texts)
            with open(MODEL_DIR / "tfidf_vectorizer.pkl", "wb") as f:
                pickle.dump(tfidf, f)
        except ValueError:
            pass  # Not enough documents with min_df=2

        # Train LDA
        count_vec = CountVectorizer(max_df=0.95, min_df=2, max_features=1000, stop_words="english")
        try:
            count_matrix = count_vec.fit_transform(texts)
            n_topics = min(len(INTENTS), max(5, len(texts) // 10))
            lda = LatentDirichletAllocation(n_components=n_topics, max_iter=10, random_state=42)
            lda.fit(count_matrix)
            with open(MODEL_DIR / "lda_vectorizer.pkl", "wb") as f:
                pickle.dump(count_vec, f)
            with open(MODEL_DIR / "lda_model.pkl", "wb") as f:
                pickle.dump(lda, f)
        except ValueError:
            pass

        # Train Naive Bayes (only on labeled data)
        labeled_texts = [t for t, l in zip(texts, labels) if l]
        labeled_labels = [l for l in labels if l]
        if len(labeled_texts) >= 10 and len(set(labeled_labels)) >= 2:
            nb_vec = TfidfVectorizer(max_features=1000, stop_words="english")
            nb_matrix = nb_vec.fit_transform(labeled_texts)
            nb = MultinomialNB(alpha=0.1)
            nb.fit(nb_matrix, labeled_labels)
            with open(MODEL_DIR / "nb_vectorizer.pkl", "wb") as f:
                pickle.dump(nb_vec, f)
            with open(MODEL_DIR / "naive_bayes.pkl", "wb") as f:
                pickle.dump(nb, f)
            log.info(f"Naive Bayes trained on {len(labeled_texts)} labeled emails")

        log.info("Incremental training complete")

    except Exception as e:
        log.error(f"Training error: {e}")
    finally:
        _training_lock.release()


# ── Audit Logging ──────────────────────────────────────────────────────

def _audit_log(sender, subject, result):
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": sender,
            "subject": subject[:100],
            "intent": result["intent"],
            "confidence": round(result["confidence"], 3),
            "method": result["method"],
        }
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Email NLP Pipeline")
    parser.add_argument("--classify", type=str, help="Classify text")
    parser.add_argument("--train", action="store_true", help="Force full training")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    parser.add_argument("--count-stored", action="store_true", help="Count stored emails")
    args = parser.parse_args()

    if args.classify:
        result = classify_email("test@test.com", "Test", args.classify)
        print(json.dumps(result, indent=2, default=str))
    elif args.train:
        _incremental_train()
    elif args.count_stored:
        count = len(list(EMAIL_STORE_DIR.glob("*.enc")))
        print(f"Stored emails: {count}")
    elif args.stats:
        if AUDIT_LOG.exists():
            lines = AUDIT_LOG.read_text().strip().split("\n")
            methods = Counter()
            intents = Counter()
            for line in lines:
                try:
                    d = json.loads(line)
                    methods[d["method"]] += 1
                    intents[d["intent"]] += 1
                except:
                    pass
            print(f"Classifications: {len(lines)}")
            print(f"Methods: {dict(methods)}")
            print(f"Intents: {dict(intents)}")
        print(f"Stored emails: {len(list(EMAIL_STORE_DIR.glob('*.enc')))}")
        print(f"Models: {list(MODEL_DIR.glob('*.pkl'))}")
    else:
        parser.print_help()
