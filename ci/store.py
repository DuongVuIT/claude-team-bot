"""Lưu data.json + state.json (đã mã hoá) vào nhánh git `data`, để bot chạy trên
GitHub Actions không mất dữ liệu giữa các lần khởi động lại.

  python3 ci/store.py load   # tải dữ liệu mới nhất về cạnh bot.py
  python3 ci/store.py save   # mã hoá và đẩy lên nhánh data (bỏ qua nếu không đổi)

Khoá mã hoá lấy từ biến DATA_KEY, hoặc file .data_key cạnh bot.py khi chạy trên máy.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["data.json", "state.json"]
BRANCH = "data"
REF = f"refs/remotes/origin/{BRANCH}"
BLOB = "store.enc"
LOADED = os.path.join(ROOT, ".store_loaded")  # chứa hash bản đã tải / đã lưu gần nhất


def key():
    k = os.environ.get("DATA_KEY", "").strip()
    path = os.path.join(ROOT, ".data_key")
    if not k and os.path.exists(path):
        k = open(path, encoding="utf-8").read().strip()
    if not k:
        sys.exit("thiếu DATA_KEY")
    return k


def git(*args, **kw):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, **kw).stdout


def openssl(mode, data):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:STORE_KEY"]
    args += ["-d"] if mode == "dec" else ["-salt"]
    env = {**os.environ, "STORE_KEY": key()}
    r = subprocess.run(args, input=data, capture_output=True, env=env)
    if r.returncode:
        sys.exit(f"openssl {mode} lỗi (sai DATA_KEY?): {r.stderr.decode(errors='replace')}")
    return r.stdout


def bundle():
    out = {}
    for name in FILES:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out[name] = json.load(f)
    return json.dumps(out, ensure_ascii=False, sort_keys=True).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch():
    """True nếu nhánh data đã có trên GitHub; lỗi mạng thì dừng hẳn (không coi là trống)."""
    r = subprocess.run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{BRANCH}"],
                       cwd=ROOT, capture_output=True)
    if r.returncode == 2:
        return False
    if r.returncode:
        sys.exit(f"không đọc được nhánh {BRANCH}: {r.stderr.decode(errors='replace')}")
    depth = ["--depth=1"] if os.environ.get("GITHUB_ACTIONS") else []
    git("fetch", "-q", *depth, "origin", f"+refs/heads/{BRANCH}:{REF}")
    return True


def load():
    if not fetch():
        sys.exit(f"chưa có nhánh {BRANCH}: chạy `python3 ci/store.py save` trên máy để đưa dữ liệu lên trước")
    raw = openssl("dec", git("show", f"{REF}:{BLOB}"))
    for name, obj in json.loads(raw).items():
        if name in FILES:
            tmp = os.path.join(ROOT, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, os.path.join(ROOT, name))
    with open(LOADED, "w") as f:
        f.write(digest(bundle()))
    print("store: đã tải dữ liệu")


def save():
    if os.environ.get("GITHUB_ACTIONS") and not os.path.exists(LOADED):
        sys.exit("store: chưa tải dữ liệu thành công, không lưu để tránh ghi đè")
    raw = bundle()
    h = digest(raw)
    if os.path.exists(LOADED) and open(LOADED).read().strip() == h:
        print("store: không đổi")
        return
    blob = openssl("enc", raw)
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "team-bot", "GIT_AUTHOR_EMAIL": "team-bot@users.noreply.github.com",
           "GIT_COMMITTER_NAME": "team-bot", "GIT_COMMITTER_EMAIL": "team-bot@users.noreply.github.com"}
    oid = git("hash-object", "-w", "--stdin", input=blob).decode().strip()
    tree = git("mktree", input=f"100644 blob {oid}\t{BLOB}\n".encode()).decode().strip()
    has_branch = fetch()
    parent = ["-p", git("rev-parse", REF).decode().strip()] if has_branch else []
    msg = "data " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    commit = subprocess.run(["git", "commit-tree", tree, *parent, "-m", msg], cwd=ROOT, env=env,
                            check=True, capture_output=True).stdout.decode().strip()
    for attempt in range(3):
        r = subprocess.run(["git", "push", "-q", "origin", f"{commit}:refs/heads/{BRANCH}"],
                           cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            break
        print("store: push lỗi, thử lại:", r.stderr.decode(errors="replace").strip(), flush=True)
        time.sleep(5 * (attempt + 1))
    else:
        sys.exit("store: không đẩy được dữ liệu lên GitHub")
    git("update-ref", REF, commit)
    with open(LOADED, "w") as f:
        f.write(h)
    print("store: đã lưu", commit[:7])


if __name__ == "__main__":
    {"load": load, "save": save}[sys.argv[1] if len(sys.argv) > 1 else ""]()
