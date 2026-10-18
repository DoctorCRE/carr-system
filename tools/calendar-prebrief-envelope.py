#!/usr/bin/env python3
"""Verify a signed, redacted collector envelope without persisting its input."""
from __future__ import annotations
import base64, hashlib, json
from pathlib import Path
from typing import Any, Callable
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lib.ed25519 import verify as verify_ed25519
class Refusal(RuntimeError): pass
REQUIRED={"sponsor","job_id","lease","scheduled_for","allowlist_revision","observed_calendar_keys","raw_capture_digest","raw_capture_count","collector_version","key_fingerprint","signature"}
def canonical(envelope:dict[str,Any])->bytes:
 if set(envelope)!=REQUIRED: raise Refusal("collector envelope has an unsupported shape")
 body={k:v for k,v in envelope.items() if k!="signature"}
 if body.get("sponsor") not in {"joe","dell"} or not isinstance(body.get("collector_version"),str) or not isinstance(body.get("key_fingerprint"),str): raise Refusal("collector envelope identity is invalid")
 if not isinstance(body.get("observed_calendar_keys"),list) or not all(isinstance(x,str) and len(x)==64 for x in body["observed_calendar_keys"]): raise Refusal("collector envelope keys are invalid")
 if not isinstance(body.get("raw_capture_digest"),str) or len(body["raw_capture_digest"])!=64 or type(body.get("raw_capture_count")) is not int or body["raw_capture_count"]<0: raise Refusal("collector envelope capture evidence is invalid")
 return json.dumps(body,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
def verify(envelope:dict[str,Any],public_key:Path,expected_fingerprint:str,replay_seen:Callable[[str],bool])->dict[str,Any]:
 payload=canonical(envelope)
 if envelope["key_fingerprint"]!=expected_fingerprint or not public_key.is_file() or public_key.is_symlink(): raise Refusal("collector key is not trusted")
 try: signature=base64.b64decode(envelope["signature"],validate=True)
 except Exception as e: raise Refusal("collector signature is malformed") from e
 digest=hashlib.sha256(payload).hexdigest()
 if replay_seen(digest): raise Refusal("collector envelope replay refused")
 if not verify_ed25519(public_key.read_bytes(),payload,signature): raise Refusal("collector signature verification failed")
 return {k:envelope[k] for k in REQUIRED if k!="signature"}
