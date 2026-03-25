#!/usr/bin/env python3
"""LDA-based email intent classifier with KG + RAG enrichment.

Replaces keyword-based classification with topic modeling.
Three layers:
  1. LDA — topic distribution from text (local, <1ms)
  2. KG — context enrichment from knowledge graph (<10ms)
  3. RAG — precedent lookup from similar past emails (<50ms)

Falls back to LLM only when confidence < 0.75.

Usage:
    from lda_classifier import classify_email
    result = classify_email(sender, subject, body)
    # Returns: {"intent": "status_inquiry", "confidence": 0.87, "topics": {...}, "method": "lda+kg+rag"}
"""

import os
import sys
import json
import pickle
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/home/aielevate")

log = logging.getLogger("lda-classifier")

MODEL_DIR = Path("/opt/ai-elevate/models/lda")
MODEL_PATH = MODEL_DIR / "email_lda_model.pkl"
VECTORIZER_PATH = MODEL_DIR / "email_vectorizer.pkl"
AUDIT_LOG = Path("/var/log/openclaw/shared/classification-audit.jsonl")

# Topic names mapped to intent labels
TOPIC_LABELS = {
    0: "new_inquiry",
    1: "status_inquiry",
    2: "bug_report",
    3: "revision_request",
    4: "acceptance",
    5: "scope_change",
    6: "feedback",
    7: "billing",
    8: "internal_ops",
    9: "project_delivery",
    10: "onboarding",
    11: "general",
}

# Keywords that strongly indicate specific topics (prior knowledge)
TOPIC_PRIORS = {
    "new_inquiry": ["new project", "quote", "estimate", "looking for", "need a developer", "interested in"],
    "status_inquiry": ["status", "update", "progress", "where are we", "what's happening", "how is", "ETA", "timeline"],
    "bug_report": ["bug", "broken", "crash", "error", "not working", "500", "502", "503", "down", "failed"],
    "revision_request": ["revision", "change this", "update the", "modify", "tweak", "adjust", "redo"],
    "acceptance": ["approved", "looks great", "go live", "ship it", "love it", "accepted", "go ahead"],
    "scope_change": ["scope", "additional", "also want", "new feature", "add to", "expand"],
    "feedback": ["feedback", "review", "thoughts on", "opinion", "how was"],
    "billing": ["invoice", "payment", "billing", "charge", "refund", "subscription"],
    "internal_ops": ["internal", "ops", "agent", "workflow", "deploy", "server", "cron"],
    "project_delivery": ["delivered", "handover", "documentation", "go-live", "launch", "deploy to production"],
    "onboarding": ["welcome", "getting started", "onboard", "setup", "kick-off"],
    "general": [],
}


def _preprocess(text):
    """Clean and normalize email text for classification."""
    text = text.lower()
    # Remove quoted replies
    text = re.sub(r'^>.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'on .* wrote:', '', text)
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove email addresses
    text = re.sub(r'\S+@\S+', '', text)
    # Remove special chars but keep spaces
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _keyword_score(text):
    """Score text against topic priors using keywords."""
    scores = {}
    text_lower = text.lower()
    for topic, keywords in TOPIC_PRIORS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[topic] = score
    return scores


def _kg_enrich(sender, subject, body):
    """Query knowledge graph for context about sender and referenced entities."""
    context = {}
    try:
        from knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph("gigforge")

        # Look up sender
        sender_data = kg.query(f"person:{sender}")
        if sender_data:
            context["sender_role"] = sender_data.get("role", "unknown")
            context["sender_is_internal"] = sender_data.get("internal", False)

        # Extract project references (GF-XX, ticket IDs)
        refs = re.findall(r'GF-\d+|gf-\d+', f"{subject} {body}")
        if refs:
            ref = refs[0].upper()
            project_data = kg.query(f"project:{ref}")
            if project_data:
                context["project_status"] = project_data.get("status", "unknown")
                context["project_type"] = project_data.get("type", "unknown")

    except Exception as e:
        log.debug(f"KG enrichment skipped: {e}")

    return context


def _rag_precedent(body, top_k=3):
    """Search RAG for similar past emails and their classifications."""
    precedents = []
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="127.0.0.1", port=5434, dbname="rag",
            user="rag", password="rag_vec_2026"
        )
        cur = conn.cursor()

        # Search email_interactions for similar subjects/bodies
        # Use simple text similarity (trigram) since we may not have embeddings on this table
        clean = _preprocess(body)[:200]
        cur.execute("""
            SELECT subject, classification, COUNT(*) as cnt
            FROM email_interactions
            WHERE body_text %% %s OR subject %% %s
            GROUP BY subject, classification
            ORDER BY cnt DESC
            LIMIT %s
        """, (clean, clean, top_k))
        rows = cur.fetchall()
        for row in rows:
            if row[1]:  # Has a classification
                precedents.append({"subject": row[0], "intent": row[1], "count": row[2]})
        conn.close()
    except Exception as e:
        log.debug(f"RAG precedent lookup skipped: {e}")

    return precedents


def _adjust_by_context(topics, kg_context):
    """Adjust topic probabilities based on KG context."""
    adjusted = dict(topics)

    # If sender is internal/owner and project is delivered, boost status_inquiry
    if kg_context.get("sender_is_internal") or kg_context.get("sender_role") == "owner":
        if kg_context.get("project_status") in ("delivered", "complete", "in_review"):
            adjusted["status_inquiry"] = adjusted.get("status_inquiry", 0) + 0.25
            adjusted["bug_report"] = adjusted.get("bug_report", 0) * 0.5

    # If project status is "building", boost revision/scope_change
    if kg_context.get("project_status") == "building":
        adjusted["revision_request"] = adjusted.get("revision_request", 0) + 0.1
        adjusted["scope_change"] = adjusted.get("scope_change", 0) + 0.1

    # Normalize
    total = sum(adjusted.values()) or 1
    return {k: v / total for k, v in adjusted.items()}


def _adjust_by_precedent(topics, precedents):
    """Adjust topic probabilities based on similar past emails."""
    if not precedents:
        return topics

    adjusted = dict(topics)
    for p in precedents:
        intent = p["intent"]
        if intent in adjusted:
            adjusted[intent] = adjusted.get(intent, 0) + 0.15 * p.get("count", 1)

    # Normalize
    total = sum(adjusted.values()) or 1
    return {k: v / total for k, v in adjusted.items()}


def _lda_classify(text):
    """Blend LDA model probabilities with keyword scores.

    Keywords are weighted higher (0.6) than LDA (0.4) because:
    - LDA is trained on limited data (85 emails, subjects only)
    - Keywords encode domain knowledge (proven priors)
    - As more body text accumulates, retrain and shift weight toward LDA
    """
    KEYWORD_WEIGHT = 0.6
    LDA_WEIGHT = 0.4

    # Always get keyword scores (fast, reliable)
    kw_scores = _keyword_score(text)
    all_topics = list(TOPIC_LABELS.values())

    # Normalize keyword scores across all topics
    kw_total = sum(kw_scores.values()) or 1
    kw_dist = {t: kw_scores.get(t, 0) / kw_total for t in all_topics}

    # Get LDA scores if model exists
    lda_dist = {t: 1.0 / len(all_topics) for t in all_topics}  # Uniform default
    if MODEL_PATH.exists() and VECTORIZER_PATH.exists():
        try:
            with open(VECTORIZER_PATH, "rb") as f:
                vectorizer = pickle.load(f)
            with open(MODEL_PATH, "rb") as f:
                model = pickle.load(f)

            vec = vectorizer.transform([_preprocess(text)])
            topic_probs = model.transform(vec)[0]
            lda_dist = {TOPIC_LABELS.get(i, f"topic_{i}"): float(p)
                        for i, p in enumerate(topic_probs)}
            # Fill any missing topics
            for t in all_topics:
                if t not in lda_dist:
                    lda_dist[t] = 0.0
        except Exception as e:
            log.warning(f"LDA model failed: {e}")

    # Blend: keywords weighted higher until we have more training data
    blended = {}
    for t in all_topics:
        kw = kw_dist.get(t, 0)
        lda = lda_dist.get(t, 0)
        if sum(kw_scores.values()) > 0:
            # Keywords found — weight them heavily
            blended[t] = (KEYWORD_WEIGHT * kw) + (LDA_WEIGHT * lda)
        else:
            # No keyword matches — rely on LDA only
            blended[t] = lda

    # Normalize
    total = sum(blended.values()) or 1
    return {k: v / total for k, v in blended.items()}


def _audit_log(sender, subject, result):
    """Log classification for feedback loop and retraining."""
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": sender,
            "subject": subject[:100],
            "intent": result["intent"],
            "confidence": round(result["confidence"], 3),
            "method": result["method"],
            "topics": {k: round(v, 3) for k, v in result["topics"].items() if v > 0.05},
        }
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def classify_email(sender, subject, body):
    """Classify email intent using LDA + KG + RAG.

    Returns:
        dict with keys: intent, confidence, topics, method, kg_context
    """
    full_text = f"{subject}\n\n{body}"

    # Layer 1: LDA topic distribution
    topics = _lda_classify(full_text)

    # Layer 2: KG context enrichment
    kg_context = _kg_enrich(sender, subject, body)
    if kg_context:
        topics = _adjust_by_context(topics, kg_context)

    # Layer 3: RAG precedent lookup
    precedents = _rag_precedent(body)
    if precedents:
        topics = _adjust_by_precedent(topics, precedents)

    # Determine top intent and confidence
    top_intent = max(topics, key=topics.get)
    confidence = topics[top_intent]
    method = "lda+kg+rag"

    # If confidence is too low, fall back to LLM
    if confidence < 0.75:
        try:
            from intent_classifier import classify_intent_llm
            llm_result = classify_intent_llm(subject, body)
            if llm_result and llm_result.get("confidence", 0) > confidence:
                top_intent = llm_result["type"]
                confidence = llm_result["confidence"]
                method = "llm_fallback"
        except Exception:
            pass  # Stick with LDA result

    result = {
        "intent": top_intent,
        "confidence": confidence,
        "topics": topics,
        "method": method,
        "kg_context": kg_context,
    }

    _audit_log(sender, subject, result)
    return result


def train_model():
    """Train LDA model on historical email data."""
    try:
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.decomposition import LatentDirichletAllocation
    except ImportError:
        print("Installing scikit-learn...")
        os.system("pip install scikit-learn")
        from sklearn.feature_extraction.text import CountVectorizer
        from sklearn.decomposition import LatentDirichletAllocation

    import psycopg2
    conn = psycopg2.connect(
        host="127.0.0.1", port=5434, dbname="rag",
        user="rag", password="rag_vec_2026"
    )
    cur = conn.cursor()

    # Get all email interactions
    cur.execute("SELECT subject, COALESCE(body_text, '') FROM email_interactions WHERE subject IS NOT NULL AND length(subject) > 3")
    rows = cur.fetchall()
    conn.close()

    if len(rows) < 20:
        print(f"Only {len(rows)} emails — need at least 20 for training. Using keyword-only mode.")
        return

    print(f"Training LDA on {len(rows)} emails...")

    # Preprocess
    docs = [_preprocess(f"{row[0]} {row[1]}") for row in rows]

    # Vectorize
    vectorizer = CountVectorizer(
        max_df=0.95, min_df=2, max_features=1000,
        stop_words="english"
    )
    doc_term = vectorizer.fit_transform(docs)

    # Train LDA
    n_topics = min(12, max(5, len(rows) // 10))
    lda = LatentDirichletAllocation(
        n_components=n_topics,
        max_iter=20,
        learning_method="online",
        random_state=42,
    )
    lda.fit(doc_term)

    # Save model
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(lda, f)

    # Print top words per topic
    feature_names = vectorizer.get_feature_names_out()
    for i, topic in enumerate(lda.components_):
        top_words = [feature_names[j] for j in topic.argsort()[-8:]]
        label = TOPIC_LABELS.get(i, f"topic_{i}")
        print(f"  Topic {i} ({label}): {', '.join(top_words)}")

    print(f"\nModel saved to {MODEL_DIR}")
    print(f"Topics: {n_topics}, Documents: {len(rows)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LDA Email Classifier")
    parser.add_argument("--train", action="store_true", help="Train model on email history")
    parser.add_argument("--classify", type=str, help="Classify a test string")
    parser.add_argument("--stats", action="store_true", help="Show classification stats from audit log")
    args = parser.parse_args()

    if args.train:
        train_model()
    elif args.classify:
        result = classify_email("test@test.com", "Test", args.classify)
        print(json.dumps(result, indent=2))
    elif args.stats:
        if AUDIT_LOG.exists():
            lines = AUDIT_LOG.read_text().strip().split("\n")
            from collections import Counter
            methods = Counter()
            intents = Counter()
            for line in lines:
                d = json.loads(line)
                methods[d["method"]] += 1
                intents[d["intent"]] += 1
            print(f"Total classifications: {len(lines)}")
            print(f"Methods: {dict(methods)}")
            print(f"Intents: {dict(intents)}")
        else:
            print("No audit log yet")
    else:
        parser.print_help()
