"""
Standalone SFTP connectivity check — isolates the SG_SFTP_* env vars
and the actual paramiko connection from everything else in the pipeline.

Run this INSIDE the container (same env as the real app) with:
    docker compose -f docker-compose.yml -f docker-compose.local-test.yml \
        run --rm reportgen python check_sftp.py
"""

import os
import io
import paramiko

REQUIRED_VARS = [
    "SG_SFTP_HOST",
    "SG_SFTP_USER",
    "SG_SFTP_PRIVATE_KEY",
]

print("=== 1. Checking env vars are present ===")
missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
if missing:
    print(f"❌ Missing: {missing}")
    print("   → this alone explains an SSH failure — check your .env file")
    raise SystemExit(1)
print("✅ all required env vars are set")

port = int(os.environ.get("SG_SFTP_PORT", 18765))
private_key_raw = os.environ["SG_SFTP_PRIVATE_KEY"]

print(f"\n=== 2. Checking key format ===")
print(f"Key string length: {len(private_key_raw)} chars")
print(f"Contains real newlines: {chr(10) in private_key_raw}")
print(f"Contains literal backslash-n: {chr(92)+'n' in private_key_raw}")
if (chr(92) + "n") in private_key_raw and chr(10) not in private_key_raw:
    print("⚠️  Looks like your .env has ESCAPED \\n instead of real line breaks.")
    print("    Try replacing them before loading, e.g.:")
    print('    private_key_raw = private_key_raw.replace("\\\\n", "\\n")')

print("\n=== 3. Parsing the key ===")
try:
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_raw))
    print("✅ key parsed successfully")
except Exception as e:
    print(f"❌ Key parsing failed: {type(e).__name__}: {e}")
    print("   → this is a LOCAL secrets/formatting issue, not a code bug")
    raise SystemExit(1)

print("\n=== 4. Attempting real SSH connection ===")
try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=os.environ["SG_SFTP_HOST"],
        port=port,
        username=os.environ["SG_SFTP_USER"],
        pkey=pkey,
        timeout=10,
    )
    print("✅ SSH connection succeeded")
    sftp = ssh.open_sftp()
    remote_dir = os.environ.get("SG_SFTP_REMOTE_DIR", "/")
    print(f"\n=== 5. Listing remote dir: {remote_dir} ===")
    print(sftp.listdir(remote_dir))
    sftp.close()
    ssh.close()
    print("\n✅ ALL GOOD — this was a code/env issue in the pipeline, not the SFTP setup itself")
except Exception as e:
    print(f"❌ SSH connection failed: {type(e).__name__}: {e}")
    print("   → could be: wrong host/port, key not authorized on the server,")
    print("     firewall blocking the port, or the key really is invalid")