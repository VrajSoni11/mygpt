import json
import os
import sys

import httpx

API = "http://127.0.0.1:8000"

DIM, GREEN, YELLOW, RED, RESET = "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
os.system("")  # enables colors in the Windows terminal
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HELP = """Commands:
  /think on|off      show or skip the thinking step (off = faster replies)
  /tasks             show open tasks
  /habits            show habit streaks
  /remember <fact>   save a permanent fact
  /facts             list saved facts
  /forget <id>       delete a fact
  /reset             clear chat history (tasks, habits, facts stay)
  /quit              exit
"""


def call(method, path, **kwargs):
    r = httpx.request(method, API + path, timeout=60, **kwargs)
    r.raise_for_status()
    return r.json()


def stream_chat(message: str, think: bool):
    state = None  # None / "thinking" / "content"
    with httpx.stream("POST", API + "/chat", json={"message": message, "think": think}, timeout=None) as r:
        if r.status_code != 200:
            r.read()
            print(f"{RED}Error {r.status_code}: {r.text}{RESET}")
            return
        for line in r.iter_lines():
            if not line:
                continue
            e = json.loads(line)
            kind = e["type"]
            if kind == "thinking":
                if state != "thinking":
                    print(f"{DIM}Thinking...{RESET}")
                    state = "thinking"
                print(f"{DIM}{e['text']}{RESET}", end="", flush=True)
            elif kind == "content":
                if state != "content":
                    print(f"\n\n{GREEN}Jarvis:{RESET} ", end="")
                    state = "content"
                print(e["text"], end="", flush=True)
            elif kind == "tool":
                args = ", ".join(f"{k}={v!r}" for k, v in e["args"].items())
                print(f"\n{YELLOW}[tool] {e['name']}({args}){RESET}")
                print(f"{YELLOW}   -> {e['result']}{RESET}")
                state = None
            elif kind == "error":
                print(f"\n{RED}{e['text']}{RESET}")
    print("\n")


def show_due_reminders():
    try:
        for r in call("GET", "/reminders/due"):
            print(f"{YELLOW}[REMINDER] {r['text']}  (set for {r['remind_at']}){RESET}\n")
    except Exception:
        pass


def main():
    try:
        info = call("GET", "/")
    except Exception:
        print("Can't reach the backend. Start it first with:")
        print("  uvicorn main:app --reload")
        return

    think = True
    print(f"Connected to Jarvis (model: {info['model']}, thinking: ON)")
    print(HELP)

    while True:
        show_due_reminders()
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue

        if text == "/quit":
            break
        elif text.startswith("/think"):
            think = not text.endswith("off")
            print(f"Thinking is now {'ON' if think else 'OFF'}.")
        elif text == "/tasks":
            print(call("GET", "/tasks")["text"])
        elif text == "/habits":
            print(call("GET", "/habits")["text"])
        elif text == "/facts":
            facts = call("GET", "/facts")
            print("\n".join(f"  [{f['id']}] {f['fact']}" for f in facts) or "(no facts saved)")
        elif text.startswith("/remember "):
            print(call("POST", "/facts", json={"fact": text[len("/remember "):]})["text"])
        elif text.startswith("/forget "):
            call("DELETE", f"/facts/{text.split()[1]}")
            print("Deleted.")
        elif text == "/reset":
            call("POST", "/reset")
            print("Chat history cleared.")
        else:
            try:
                stream_chat(text, think)
            except Exception as ex:
                print(f"{RED}Error: {ex}{RESET}")


if __name__ == "__main__":
    main()