"""Print the last N tool calls/results of the newest trial of a case (run as migrate; reads via sudo)."""
import json, subprocess, sys
case = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
raw = subprocess.run(["sudo", "-n", "-u", "axis-eval", "sh", "-c",
                      f'd=$(ls -td ~/ws/{case}-* | head -1); tail -{n*2} $d/out/transcript.jsonl'],
                     capture_output=True, text=True).stdout
for l in raw.splitlines():
    try: d = json.loads(l)
    except json.JSONDecodeError: continue
    if d.get("_type") == "AssistantMessage":
        for b in d.get("content") or []:
            if "name" in b: print("TOOL", b["name"], json.dumps(b["input"])[:260])
            elif b.get("text"): print("TEXT", b["text"][:400].replace("\n", " "))
    elif d.get("_type") == "UserMessage" and isinstance(d.get("content"), list):
        for b in d["content"]:
            if isinstance(b, dict) and "tool_use_id" in b:
                print("  RES", ("ERR " if b.get("is_error") else "") + str(b.get("content"))[:220].replace("\n", " "))
