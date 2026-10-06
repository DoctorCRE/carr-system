#!/usr/bin/env python3
"""Verify a signed, redacted collector envelope without persisting its input."""
from __future__ import annotations
import base64, hashlib, json, os, subprocess
from pathlib import Path
from typing import Any, Callable
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lib.machine_prerequisites import openssl_executable
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
 sig_r,sig_w=os.pipe(); payload_fd=signature_fd=-1
 try:
  input_path="/dev/stdin"; input_data:bytes|None=payload; signature_path=f"/dev/fd/{sig_r}"
  if hasattr(os,"memfd_create"):
   payload_fd=os.memfd_create("carr-calendar-envelope"); signature_fd=os.memfd_create("carr-calendar-signature"); remaining=memoryview(payload)
   while remaining:
    written=os.write(payload_fd,remaining)
    if written<1: raise Refusal("collector envelope could not be buffered")
    remaining=remaining[written:]
   os.lseek(payload_fd,0,os.SEEK_SET); os.write(signature_fd,signature); os.lseek(signature_fd,0,os.SEEK_SET); input_path=f"/dev/fd/{payload_fd}"; input_data=None; signature_path=f"/dev/fd/{signature_fd}"
  else: os.write(sig_w,signature)
  proc=subprocess.Popen([openssl_executable(),"pkeyutl","-verify","-pubin","-inkey",str(public_key),"-rawin","-in",input_path,"-sigfile",signature_path],stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=tuple(item for item in (sig_r,payload_fd,signature_fd) if item>=0))
  os.close(sig_r); os.close(sig_w); sig_w=-1
  proc.communicate(input_data,timeout=5)
 except Exception as e: raise Refusal("collector signature verification failed") from e
 finally:
  if sig_w!=-1: os.close(sig_w)
  for item in (payload_fd,signature_fd):
   if item>=0: os.close(item)
 if proc.returncode!=0: raise Refusal("collector signature verification failed")
 return {k:envelope[k] for k in REQUIRED if k!="signature"}
