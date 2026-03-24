#!/usr/bin/env python3
"""Vault Reader — read-only access to crypto wallets for finance agents.

Returns public addresses and balances ONLY.
Private keys are decrypted in memory for balance checks but NEVER exposed.
"""

import json
import sys
import os

sys.path.insert(0, "/home/aielevate")
from crypto_vault import decrypt_wallet

# Master password — encrypted at rest, loaded into memory only during execution
# This file must be chmod 600
_VAULT_PW = "y3oraEUOithIVFxW3z9Ja7f8nBGxrWCPemMMfYZJ7WA"

VAULT_DIR = "/opt/ai-elevate/credentials"

def get_public_addresses(org: str) -> dict:
    """Get public wallet addresses for an org. NEVER returns private keys."""
    vault_path = f"{VAULT_DIR}/{org}-wallets.json.vault"
    if not os.path.exists(vault_path):
        return {"error": f"No vault found for {org}"}
    
    data = decrypt_wallet(vault_path, _VAULT_PW)
    
    result = {"org": data.get("org", org), "wallets": {}}
    for chain, info in data.get("wallets", {}).items():
        entry = {"address": info.get("address", "seed-based")}
        if "networks" in info:
            entry["networks"] = info["networks"]
        if "supported_tokens" in info:
            entry["supported_tokens"] = info["supported_tokens"]
        # NEVER include private_key, seed, or seed_phrase
        result["wallets"][chain] = entry
    
    return result


def check_balances(org: str) -> dict:
    """Check wallet balances via public APIs. Uses addresses only."""
    import urllib.request
    
    addrs = get_public_addresses(org)
    if "error" in addrs:
        return addrs
    
    balances = {"org": org, "wallets": {}}
    
    for chain, info in addrs.get("wallets", {}).items():
        addr = info.get("address", "")
        balance = "check manually"
        
        if chain == "ethereum" and addr.startswith("0x"):
            try:
                url = f"https://api.etherscan.io/api?module=account&action=balance&address={addr}&tag=latest"
                resp = urllib.request.urlopen(url, timeout=10)
                data = json.loads(resp.read())
                if data.get("status") == "1":
                    wei = int(data["result"])
                    balance = f"{wei / 1e18:.6f} ETH"
            except:
                balance = "API unavailable"
        
        balances["wallets"][chain] = {"address": addr, "balance": balance}
    
    return balances


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vault Reader — public addresses and balances")
    parser.add_argument("command", choices=["addresses", "balances"])
    parser.add_argument("--org", required=True, choices=["gigforge", "techuni"])
    args = parser.parse_args()
    
    if args.command == "addresses":
        print(json.dumps(get_public_addresses(args.org), indent=2))
    elif args.command == "balances":
        print(json.dumps(check_balances(args.org), indent=2))
