#!/usr/bin/env python3
"""QLoRA fine-tuning of phi3:mini for email validation.

Trains on: pass/fail labeled emails from our production data.
Output: LoRA adapter that gets merged into a custom Ollama model.

Run: nohup python3 qlora_finetune.py > /var/log/openclaw/shared/qlora-training.log 2>&1 &
Expected: 2-4 hours on CPU, ~200 training examples.
"""

import sys
import os
import json
import logging
import hashlib
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "/home/aielevate")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [qlora] %(message)s")
log = logging.getLogger("qlora")

MODEL_NAME = "microsoft/phi-3-mini-4k-instruct"
OUTPUT_DIR = "/opt/ai-elevate/models/phi3-email-validator"
ADAPTER_DIR = f"{OUTPUT_DIR}/adapter"
MERGED_DIR = f"{OUTPUT_DIR}/merged"
DATA_DIR = f"{OUTPUT_DIR}/data"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ADAPTER_DIR, exist_ok=True)
os.makedirs(MERGED_DIR, exist_ok=True)


# ── Training Data Generation ─────────────────────────────────────────

def generate_training_data():
    """Build training dataset from production data + synthetic examples."""

    examples = []

    # Synthetic FAIL examples — things we've seen agents do wrong
    fails = [
        # Fabricated credentials
        ("Hi Jon,\n\nLogin URL: http://carehaven.ai\nEmail: jbrelin@gmail.com\nPassword: 27EDdwdR-qMsAShPp\n\nAlex",
         "FAIL", ["fabricated_password", "wrong_url"]),
        ("Your credentials:\nURL: https://carehaven.io/admin\nPassword: Xm9$kLpW2!\n\nAlex",
         "FAIL", ["fabricated_password", "wrong_url"]),
        ("Hi, your API key is sk-proj-abc123def456. Use it at https://techuni.com/api\n\nAlex",
         "FAIL", ["fabricated_api_key", "wrong_url"]),
        ("Login: http://gigforge.com/portal\nUser: willie\nPass: W1ll!eBru3n\n\nAlex",
         "FAIL", ["fabricated_password", "wrong_url"]),
        ("Platform URL: https://carehaven-platform.herokuapp.com\nPassword: nursing2026!\n\nAlex",
         "FAIL", ["fabricated_password", "wrong_url"]),

        # Call suggestions
        ("The rebuild plan is ready. Happy to jump on a call to walk through it.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("If you'd like to discuss this further, I'm happy to arrange a video call.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("We'd suggest scheduling a short screen share to walk through the checklist.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("I'd love to have a proper advisory conversation with you about this.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("Let me know if you want to hop on a quick Zoom.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("I'll reach out to you directly to set up a time to discuss.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("Shall we arrange a call to go over the details?\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("Would you prefer to walk through this over a quick Teams call?\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("Happy to demo this on a screen share if that helps.\n\nAlex",
         "FAIL", ["call_suggestion"]),
        ("Let's set up a brief meeting to align on next steps.\n\nAlex",
         "FAIL", ["call_suggestion"]),

        # Internal metadata
        ("Status update.\n\nTrigger: GF-54 status inquiry from Braun\n\nAlex",
         "FAIL", ["internal_metadata"]),
        ("Done.\n\nTrigger: inbound reply from Peter re LinkedIn OAuth\n\nAlex",
         "FAIL", ["internal_metadata"]),
        ("Here is the plan.\n\nTrigger: owner directive 2026-03-25\n\nAlex",
         "FAIL", ["internal_metadata"]),
        ("Workflow: email-interaction-gigforge-1774484063\nStatus: completed\n\nAlex",
         "FAIL", ["internal_metadata"]),
        ("Context: dispatch from orchestrator for braun.brelin@ai-elevate.ai\n\nAlex",
         "FAIL", ["internal_metadata"]),

        # Combinations
        ("Hi Willie,\n\nPlan is ready. Happy to jump on a call.\n\nTrigger: GF-54\n\nAlex",
         "FAIL", ["call_suggestion", "internal_metadata"]),
        ("Your login: http://carehaven.ai, pass: kL9mXq2W\n\nI'll set up a Zoom to walk you through it.\n\nTrigger: onboarding\n\nAlex",
         "FAIL", ["fabricated_password", "wrong_url", "call_suggestion", "internal_metadata"]),
    ]

    # Synthetic PASS examples — clean emails
    passes = [
        "Hi Jon,\n\nHere are your credentials:\n\nURL: http://78.47.104.139:4093/admin\nUsername: admin\nPassword: admin\n\nAlex",
        "Hi Willie,\n\nThe site analysis is complete. Here are the findings:\n\n1. WordPress compromised\n2. jQuery 1.2.6 with known CVEs\n3. Missing HTTPS\n\nI'll send the full project plan shortly.\n\nAlex",
        "Hi Braun,\n\nAll three tasks are on track:\n1. Jon's credentials sent\n2. Priority Management plan delivered to Willie\n3. OnlyOneCustomer discovery questions sent\n\nAlex",
        "Hi Drazen,\n\nThe data migration plan is ready. We'll start with the schema analysis this week.\n\nAlex",
        "Hi Peter,\n\nLinkedIn OAuth scopes updated. We need w_organization_social and r_organization_social.\n\nAlex",
        "The deployment is complete. You can verify at https://cc.techuni.ai\n\nAlex",
        "Invoice for the Contact Management App has been sent to the client.\n\nAlex",
        "Sprint 1 is complete. Here are the results:\n- 4 P0 bugs fixed\n- UX audit delivered\n- SEO recommendations implemented\n\nAlex",
        "Hi Willie,\n\nThanks for the OnlyOneCustomer concept. Here are my initial questions:\n\n1. What's the revenue model?\n2. How do you handle right-of-reply for named companies?\n3. What's your budget range?\n\nAlex",
        "Hi Jon,\n\nThe CareHaven platform is live. The admin dashboard shows resident management, medication records, and incident tracking modules.\n\nLet me know if you have questions about what you see.\n\nAlex",
    ]

    # Format for training
    for body, label, violations in fails:
        examples.append({
            "instruction": "Check this outbound email for violations (fabricated credentials, call suggestions, internal metadata). Reply with JSON: {\"pass\": true/false, \"violations\": [\"list\"]}",
            "input": body,
            "output": json.dumps({"pass": False, "violations": violations}),
        })

    for body in passes:
        examples.append({
            "instruction": "Check this outbound email for violations (fabricated credentials, call suggestions, internal metadata). Reply with JSON: {\"pass\": true/false, \"violations\": [\"list\"]}",
            "input": body,
            "output": json.dumps({"pass": True, "violations": []}),
        })

    # Add real data from email interactions if available
    try:
        import psycopg2
        conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="rag",
                               user="rag", password="rag_vec_2026")
        cur = conn.cursor()
        cur.execute("""
            SELECT body_text, direction FROM email_interactions
            WHERE body_text IS NOT NULL AND length(body_text) > 50
            ORDER BY created_at DESC LIMIT 100
        """)
        for row in cur.fetchall():
            body = row[0][:1000]
            # Auto-label based on content
            has_trigger = bool(re.search(r'^Trigger:', body, re.MULTILINE))
            has_call = bool(re.search(r'call|zoom|screen.?share|meeting|walk.*through', body, re.IGNORECASE))
            violations = []
            if has_trigger:
                violations.append("internal_metadata")
            if has_call and row[1] == "outbound":
                violations.append("call_suggestion")

            examples.append({
                "instruction": "Check this outbound email for violations (fabricated credentials, call suggestions, internal metadata). Reply with JSON: {\"pass\": true/false, \"violations\": [\"list\"]}",
                "input": body,
                "output": json.dumps({"pass": len(violations) == 0, "violations": violations}),
            })
        conn.close()
        log.info(f"Added {cur.rowcount} real email examples")
    except Exception as e:
        log.warning(f"Could not load real emails: {e}")

    # Save training data
    data_path = f"{DATA_DIR}/train.jsonl"
    with open(data_path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    log.info(f"Training data: {len(examples)} examples saved to {data_path}")
    return data_path, len(examples)


# ── QLoRA Training ───────────────────────────────────────────────────

def train():
    """Fine-tune phi3:mini with QLoRA."""
    from transformers import (
        AutoModelForCausalLM, AutoTokenizer,
        TrainingArguments, BitsAndBytesConfig,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from datasets import load_dataset
    from trl import SFTTrainer

    log.info("Starting QLoRA fine-tuning")
    log.info(f"Model: {MODEL_NAME}")
    log.info(f"Output: {ADAPTER_DIR}")

    # Generate training data
    data_path, num_examples = generate_training_data()

    # Load dataset
    dataset = load_dataset("json", data_files=data_path, split="train")
    log.info(f"Dataset loaded: {len(dataset)} examples")

    # Format for training
    def format_example(example):
        return {
            "text": f"<|user|>\n{example['instruction']}\n\n{example['input']}<|end|>\n<|assistant|>\n{example['output']}<|end|>"
        }

    dataset = dataset.map(format_example)

    # Quantization config (4-bit for CPU-friendly training)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="float16",
        bnb_4bit_use_double_quant=True,
    )

    # Load model
    log.info("Loading model...")

    # Fix rope_scaling compatibility
    from transformers import AutoConfig
    config = AutoConfig.from_pretrained(MODEL_NAME, trust_remote_code=True)
    if hasattr(config, "rope_scaling") and config.rope_scaling:
        if "type" not in config.rope_scaling and "rope_type" in config.rope_scaling:
            config.rope_scaling["type"] = config.rope_scaling["rope_type"]
        elif "type" not in config.rope_scaling:
            config.rope_scaling["type"] = "su"

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token

    try:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            config=config,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )
    except Exception:
        # CPU fallback — no quantization
        log.info("GPU not available, training without quantization (slower)")
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            config=config,
            device_map="cpu",
            trust_remote_code=True,
            torch_dtype="auto",
        )

    model = prepare_model_for_kbit_training(model)

    # LoRA config
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, lora_config)
    log.info(f"Trainable params: {model.print_trainable_parameters()}")

    # Training config (trl 0.29.1 API)
    from trl import SFTConfig

    sft_config = SFTConfig(
        output_dir=ADAPTER_DIR,
        num_train_epochs=3,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        warmup_steps=10,
        logging_steps=10,
        save_strategy="epoch",
        fp16=False,
        optim="adamw_torch",
        report_to="none",
        use_cpu=True,
        max_length=1024,
        dataset_text_field="text",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        processing_class=tokenizer,
        args=sft_config,
    )

    log.info("Training started...")
    trainer.train()

    # Save adapter
    model.save_pretrained(ADAPTER_DIR)
    tokenizer.save_pretrained(ADAPTER_DIR)
    log.info(f"Adapter saved to {ADAPTER_DIR}")

    return ADAPTER_DIR


# ── Create Ollama Model ──────────────────────────────────────────────

def create_ollama_model():
    """Create a custom Ollama model from the fine-tuned adapter."""
    import subprocess

    modelfile = f"""FROM phi3:mini
ADAPTER {ADAPTER_DIR}
SYSTEM You are an email validator. Check outbound emails for: fabricated credentials, call/meeting suggestions, and internal metadata. Reply ONLY with JSON: {{"pass": true/false, "violations": ["list"]}}
PARAMETER temperature 0
PARAMETER num_predict 200
"""

    modelfile_path = f"{OUTPUT_DIR}/Modelfile"
    with open(modelfile_path, "w") as f:
        f.write(modelfile)

    result = subprocess.run(
        ["ollama", "create", "email-validator", "-f", modelfile_path],
        capture_output=True, text=True, timeout=300
    )
    log.info(f"Ollama model created: {result.stdout}")
    if result.returncode != 0:
        log.error(f"Ollama create error: {result.stderr}")
    return result.returncode == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="QLoRA Fine-tuning")
    parser.add_argument("--generate-data", action="store_true", help="Generate training data only")
    parser.add_argument("--train", action="store_true", help="Run full training")
    parser.add_argument("--create-model", action="store_true", help="Create Ollama model from adapter")
    args = parser.parse_args()

    if args.generate_data:
        path, count = generate_training_data()
        print(f"Generated {count} examples at {path}")
    elif args.train:
        adapter_path = train()
        print(f"Training complete. Adapter at {adapter_path}")
        success = create_ollama_model()
        print(f"Ollama model created: {success}")
    elif args.create_model:
        create_ollama_model()
    else:
        parser.print_help()
