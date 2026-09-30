"""Guest-side tool helper for solve sessions.

Runs INSIDE the sandbox as ``python -I -B -S -c <this file> <json request>``. The source travels in
the argument vector on every call, so candidate code cannot replace it. It uses the standard
library only, never follows symlinks, never leaves the workspace root, and prints exactly one JSON
object. The host validates arguments before sending and validates the reply afterwards; this code
re-checks paths because guest output and guest input are both untrusted from the host's side.

Importable on the host (no side effects at import) so the patch engine can be unit tested.
"""

import ctypes
import fnmatch
import hashlib
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import unicodedata

ROOT = "/workspace"
RESERVED = ".pcb_"
MAX_PATH_CHARS = 512
MAX_PATH_DEPTH = 40
LIST_MAX_ENTRIES = 1000
LIST_SCAN_LIMIT = 200_000
READ_FILE_LIMIT = 16 * 1024 * 1024
READ_MAX_BYTES = 64 * 1024
SEARCH_FILE_LIMIT = 2 * 1024 * 1024
SEARCH_MAX_MATCHES = 200
SEARCH_MAX_BYTES = 64 * 1024
SEARCH_LINE_CHARS = 240
SEARCH_DEADLINE_SECONDS = 8.0
PATCH_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
CAPTURE_HEAD = 192 * 1024
CAPTURE_TAIL = 64 * 1024
COMMAND_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/workspace",
    "LANG": "C.UTF-8",
    "TMPDIR": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1",
}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class ToolFailure(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --------------------------------------------------------------------------------- paths


def clean_path(raw, allow_root=False):
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PATH_CHARS:
        raise ToolFailure("path_forbidden", "path must be a non-empty relative string")
    if "\\" in raw or raw.startswith("/"):
        raise ToolFailure("path_forbidden", "path must be relative with forward slashes")
    if any(unicodedata.category(ch)[0] == "C" for ch in raw):
        raise ToolFailure("path_forbidden", "path contains control characters")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ToolFailure("path_forbidden", "path cannot leave the workspace")
    if any(p.startswith(RESERVED) for p in parts):
        raise ToolFailure("path_forbidden", "path is reserved for the harness")
    if len(parts) > MAX_PATH_DEPTH:
        raise ToolFailure("path_forbidden", "path is too deep")
    if not parts:
        if allow_root:
            return "."
        raise ToolFailure("path_forbidden", "path must name a file")
    return "/".join(parts)


def resolve(root, rel, want=None, must_exist=True):
    """Absolute path for ``rel`` after proving no component is a symlink."""
    current = root
    parts = [] if rel == "." else rel.split("/")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if must_exist:
                raise ToolFailure("not_found", "path does not exist") from None
            return os.path.join(current, *parts[index + 1 :])
        except NotADirectoryError:
            raise ToolFailure(
                "not_a_directory", "a parent of the path is not a directory"
            ) from None
        except OSError:
            raise ToolFailure("tool_failure", "path cannot be inspected") from None
        if stat.S_ISLNK(info.st_mode):
            raise ToolFailure("path_forbidden", "symbolic links are not followed")
    if want == "file":
        if not stat.S_ISREG(os.lstat(current).st_mode):
            raise ToolFailure("not_a_file", "path is not a regular file")
    elif want == "dir":
        if not stat.S_ISDIR(os.lstat(current).st_mode):
            raise ToolFailure("not_a_directory", "path is not a directory")
    return current


def iter_files(root):
    """Sorted relative paths of regular files, skipping symlinks and harness paths."""
    found = []
    stack = [("", root)]
    scanned = 0
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            names = os.listdir(abs_dir)
        except OSError:
            continue
        for name in names:
            if name.startswith(RESERVED):
                continue
            scanned += 1
            if scanned > LIST_SCAN_LIMIT:
                raise ToolFailure("too_many_entries", "workspace has too many entries")
            absolute = os.path.join(abs_dir, name)
            rel = name if not rel_dir else rel_dir + "/" + name
            info = os.lstat(absolute)
            if stat.S_ISDIR(info.st_mode):
                stack.append((rel, absolute))
            elif stat.S_ISREG(info.st_mode):
                found.append(rel)
    found.sort()
    return found


# ------------------------------------------------------------------------------ read ops


def op_list_files(root, req):
    rel = clean_path(req.get("path", "."), allow_root=True)
    depth = int(req.get("depth", 1))
    cursor = req.get("cursor")
    base = root if rel == "." else resolve(root, rel, want="dir")
    entries = []
    scanned = 0
    stack = [(rel, base, 1)]
    while stack:
        rel_dir, abs_dir, level = stack.pop()
        for name in os.listdir(abs_dir):
            if name.startswith(RESERVED):
                continue
            scanned += 1
            if scanned > LIST_SCAN_LIMIT:
                raise ToolFailure("too_many_entries", "directory has too many entries")
            absolute = os.path.join(abs_dir, name)
            path = name if rel_dir == "." else rel_dir + "/" + name
            info = os.lstat(absolute)
            if stat.S_ISLNK(info.st_mode):
                entries.append({"path": path, "type": "symlink", "size": 0})
            elif stat.S_ISDIR(info.st_mode):
                entries.append({"path": path, "type": "dir", "size": 0})
                if level < depth:
                    stack.append((path, absolute, level + 1))
            elif stat.S_ISREG(info.st_mode):
                entries.append({"path": path, "type": "file", "size": info.st_size})
            else:
                entries.append({"path": path, "type": "other", "size": 0})
    entries.sort(key=lambda item: item["path"])
    if cursor is not None:
        entries = [item for item in entries if item["path"] > cursor]
    page = entries[:LIST_MAX_ENTRIES]
    more = len(entries) > LIST_MAX_ENTRIES
    return {
        "entries": page,
        "next_cursor": page[-1]["path"] if more else None,
        "total_remaining": max(0, len(entries) - len(page)),
    }


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def op_read_file(root, req):
    rel = clean_path(req["path"])
    path = resolve(root, rel, want="file")
    size = os.lstat(path).st_size
    if size > READ_FILE_LIMIT:
        return {"path": rel, "size": size, "binary": False, "too_large": True}
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read()
    except OSError:
        raise ToolFailure("not_found", "file could not be read") from None
    digest = _sha(data)
    try:
        if b"\x00" in data[:8192]:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "nul")
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "size": len(data), "binary": True, "sha256": digest}
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = int(req.get("start_line", 1))
    wanted = int(req.get("max_lines", 400))
    selected = lines[start - 1 : start - 1 + wanted]
    out = []
    used = 0
    truncated = False
    for offset, line in enumerate(selected):
        rendered = "%d\t%s" % (start + offset, line)
        cost = len(rendered.encode("utf-8")) + 1
        if used + cost > READ_MAX_BYTES:
            truncated = True
            break
        out.append(rendered)
        used += cost
    last = start + len(out) - 1
    return {
        "path": rel,
        "size": len(data),
        "binary": False,
        "sha256": digest,
        "total_lines": len(lines),
        "start_line": start,
        "end_line": last if out else start - 1,
        "text": "\n".join(out),
        "truncated": truncated or (start - 1 + wanted < len(lines)),
        "next_start_line": last + 1 if last < len(lines) else None,
    }


def _search_core(root, req):
    pattern = req["pattern"]
    glob = req.get("path_glob", "*")
    cursor = req.get("cursor")
    matcher = re.compile(pattern) if req.get("regex") else None
    after_path, after_line = None, 0
    if cursor:
        head, _, tail = cursor.rpartition(":")
        if head and tail.isdigit():
            after_path, after_line = head, int(tail)
    matches, used, more = [], 0, False
    for rel in iter_files(root):
        if after_path is not None and rel < after_path:
            continue
        if not fnmatch.fnmatchcase(rel, glob):
            continue
        path = os.path.join(root, *rel.split("/"))
        try:
            info = os.lstat(path)
            if info.st_size > SEARCH_FILE_LIMIT:
                continue
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            if after_path is not None and rel == after_path and number <= after_line:
                continue
            hit = matcher.search(line) if matcher else (pattern in line)
            if not hit:
                continue
            text = line[:SEARCH_LINE_CHARS]
            cost = len(text.encode("utf-8")) + len(rel) + 16
            if len(matches) >= SEARCH_MAX_MATCHES or used + cost > SEARCH_MAX_BYTES:
                more = True
                break
            matches.append({"path": rel, "line": number, "text": text})
            used += cost
        if more:
            break
    last = matches[-1] if matches else None
    return {
        "matches": matches,
        "next_cursor": "%s:%d" % (last["path"], last["line"]) if more and last else None,
    }


def op_search(root, req):
    if req.get("regex"):
        try:
            re.compile(req["pattern"])
        except re.error as error:
            raise ToolFailure("regex_invalid", "invalid regular expression: %s" % error) from None
    # A pathological pattern can spin inside the regex engine; run it in a child we can kill
    # so the sandbox itself is never stalled by a model-supplied expression.
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            try:
                payload = json.dumps(_search_core(root, req)).encode()
            except Exception as error:
                payload = json.dumps({"__error__": type(error).__name__}).encode()
            view = memoryview(payload)
            while view:
                view = view[os.write(write_fd, view) :]
        finally:
            os._exit(0)
    os.close(write_fd)
    chunks = []
    deadline = time.monotonic() + SEARCH_DEADLINE_SECONDS
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, signal.SIGKILL)
                raise ToolFailure("search_timeout", "search timed out")
            ready, _, _ = select.select([read_fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    if not chunks:
        raise ToolFailure("search_timeout", "search produced no result")
    result = json.loads(b"".join(chunks))
    if "__error__" in result:
        raise ToolFailure("tool_failure", "search failed: %s" % result["__error__"])
    return result


# ------------------------------------------------------------------------ patch engine


class _FilePatch:
    def __init__(self, old, new):
        self.old = old
        self.new = new
        self.hunks = []


def _patch_path(raw):
    name = raw.split("\t")[0].strip()
    if name == "/dev/null":
        return None
    return name


def _strip(name, prefix):
    return name[len(prefix) :] if name.startswith(prefix) else name


def parse_patch(text):
    """Parse a unified diff into file patches; reject renames, modes and binary patches."""
    lines = text.split("\n")
    files = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old = _patch_path(line[4:])
            i += 1
            if i >= len(lines) or not lines[i].startswith("+++ "):
                raise ToolFailure("patch_rejected", "missing +++ header after --- header")
            new = _patch_path(lines[i][4:])
            i += 1
            old_clean = None if old is None else _strip(old, "a/")
            new_clean = None if new is None else _strip(new, "b/")
            if old_clean is None and new_clean is None:
                raise ToolFailure("patch_rejected", "patch names no file")
            if old_clean is not None and new_clean is not None and old_clean != new_clean:
                raise ToolFailure("patch_rejected", "renames are not supported")
            current = _FilePatch(old_clean, new_clean)
            while i < len(lines) and lines[i].startswith("@@"):
                match = HUNK.match(lines[i])
                if not match:
                    raise ToolFailure("patch_rejected", "malformed hunk header")
                i += 1
                old_len = int(match.group(2)) if match.group(2) is not None else 1
                new_len = int(match.group(4)) if match.group(4) is not None else 1
                body = []
                seen_old = seen_new = 0
                while i < len(lines) and (seen_old < old_len or seen_new < new_len):
                    entry = lines[i]
                    if entry.startswith("\\"):
                        if body:
                            body[-1][2] = True
                        i += 1
                        continue
                    if entry == "":
                        tag, content = " ", ""
                    elif entry[0] in " -+":
                        tag, content = entry[0], entry[1:]
                    else:
                        raise ToolFailure("patch_rejected", "malformed hunk body")
                    body.append([tag, content, False])
                    if tag in " -":
                        seen_old += 1
                    if tag in " +":
                        seen_new += 1
                    i += 1
                while i < len(lines) and lines[i].startswith("\\"):
                    if body:
                        body[-1][2] = True
                    i += 1
                if seen_old != old_len or seen_new != new_len:
                    raise ToolFailure("patch_rejected", "hunk line counts do not match its header")
                current.hunks.append(
                    (int(match.group(1)), old_len, [(t, c, n) for t, c, n in body])
                )
            if not current.hunks and current.old is not None and current.new is not None:
                raise ToolFailure("patch_rejected", "file section has no hunks")
            files.append(current)
        elif line.startswith(("rename ", "similarity ", "old mode", "new mode", "copy ")):
            raise ToolFailure(
                "patch_rejected", "renames, copies and mode changes are not supported"
            )
        elif line.startswith(("Binary files", "GIT binary patch")):
            raise ToolFailure("patch_rejected", "binary patches are not supported")
        elif line.startswith(("diff ", "index ", "new file mode", "deleted file mode")):
            i += 1
        elif line.strip() == "":
            i += 1
        else:
            raise ToolFailure("patch_rejected", "unexpected text outside a file section")
    if not files:
        raise ToolFailure("patch_rejected", "patch contains no file changes")
    return files


def apply_hunks(original, hunks, path):
    """Apply hunks (exact context, nearest offset) to file text; return new text."""
    lines = original.split("\n")
    ends_with_newline = True
    if lines and lines[-1] == "":
        lines.pop()
    elif original != "":
        ends_with_newline = False
    cursor = 0
    delta = 0
    for old_start, old_len, body in hunks:
        old_block = [c for t, c, _ in body if t in " -"]
        new_block = [c for t, c, _ in body if t in " +"]
        expected = (old_start if old_len == 0 else old_start - 1) + delta
        expected = min(max(expected, cursor), len(lines))
        found = None
        if not old_block:
            found = expected
        else:
            first = old_block[0]
            size = len(old_block)
            positions = [
                index for index in range(cursor, len(lines) - size + 1) if lines[index] == first
            ]
            positions.sort(key=lambda index: (abs(index - expected), index))
            for candidate in positions:
                if lines[candidate : candidate + size] == old_block:
                    found = candidate
                    break
        if found is None:
            raise ToolFailure(
                "patch_rejected", "hunk does not match %s near line %d" % (path, old_start)
            )
        lines[found : found + len(old_block)] = new_block
        cursor = found + len(new_block)
        delta += len(new_block) - len(old_block)
        if found + len(new_block) == len(lines) and body:
            new_side = [entry for entry in body if entry[0] in " +"]
            old_side = [entry for entry in body if entry[0] in " -"]
            if new_side:
                ends_with_newline = not new_side[-1][2]
            elif old_side and old_side[-1][2]:
                ends_with_newline = True
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if ends_with_newline else "")


def plan_patch(root, text, protected, max_file_bytes):
    """Validate every file section and compute its new content; touches nothing."""
    plan = []
    seen = set()
    for file_patch in parse_patch(text):
        rel = clean_path(file_patch.new if file_patch.new is not None else file_patch.old)
        if rel in seen:
            raise ToolFailure("patch_rejected", "file appears in more than one section")
        seen.add(rel)
        if any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in protected):
            raise ToolFailure("protected_path", "protected file: %s" % rel, path=rel)
        creating = file_patch.old is None
        deleting = file_patch.new is None
        target = resolve(root, rel, must_exist=False)
        exists = os.path.lexists(target)
        if creating:
            if exists:
                raise ToolFailure("patch_rejected", "file already exists: %s" % rel)
            original, mode = "", 0o644
        else:
            target = resolve(root, rel, want="file")
            try:
                with open(target, "rb") as handle:
                    raw = handle.read()
                original = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ToolFailure("patch_rejected", "file is not text: %s" % rel) from None
            mode = os.lstat(target).st_mode & 0o777
        updated = apply_hunks(original, file_patch.hunks, rel)
        if deleting and updated != "":
            raise ToolFailure("patch_rejected", "deletion leaves content in %s" % rel)
        if len(updated.encode("utf-8")) > max_file_bytes:
            raise ToolFailure("patch_rejected", "result exceeds the file size limit: %s" % rel)
        action = "create" if creating else "delete" if deleting else "modify"
        added = sum(1 for _, _, b in file_patch.hunks for t, _, _ in b if t == "+")
        removed = sum(1 for _, _, b in file_patch.hunks for t, _, _ in b if t == "-")
        plan.append(
            {
                "path": rel,
                "target": target,
                "action": action,
                "original": None if creating else original,
                "updated": updated,
                "mode": mode,
                "hunks": len(file_patch.hunks),
                "added": added,
                "removed": removed,
            }
        )
    return plan


def _ensure_parent(root, rel):
    parts = rel.split("/")[:-1]
    current = root
    created = []
    for part in parts:
        current = os.path.join(current, part)
        if os.path.lexists(current):
            if stat.S_ISLNK(os.lstat(current).st_mode) or not stat.S_ISDIR(
                os.lstat(current).st_mode
            ):
                raise ToolFailure("path_forbidden", "parent is not a plain directory")
        else:
            os.mkdir(current, 0o755)
            created.append(current)
    return created


def _write_atomic(target, data, mode):
    tmp = os.path.join(os.path.dirname(target), "%stmp_%d" % (RESERVED, os.getpid()))
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.chmod(tmp, mode | 0o200)
    os.replace(tmp, target)


def op_apply_patch(root, req):
    if not re.fullmatch(r"\.pcb_inbox/[A-Za-z0-9_-]{1,64}\.patch", str(req.get("patch_file", ""))):
        raise ToolFailure("path_forbidden", "patch file must be in the harness inbox")
    patch_path = os.path.join(root, req["patch_file"])
    try:
        with open(patch_path, "rb") as handle:
            raw = handle.read(PATCH_MAX_BYTES + 1)
    except OSError:
        raise ToolFailure("not_found", "patch file is missing") from None
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
    if len(raw) > PATCH_MAX_BYTES:
        raise ToolFailure("patch_rejected", "diff exceeds the size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolFailure("patch_rejected", "diff is not valid UTF-8") from None
    plan = plan_patch(
        root, text, req.get("protected", []), int(req.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES))
    )
    done, created_dirs = [], []
    try:
        for item in plan:
            created_dirs += _ensure_parent(root, item["path"])
            if item["action"] == "delete":
                os.unlink(item["target"])
            else:
                _write_atomic(item["target"], item["updated"].encode("utf-8"), item["mode"])
            done.append(item)
    except OSError:
        # Roll back so the patch stays all-or-nothing even if the disk fills mid-way.
        for item in reversed(done):
            try:
                if item["action"] == "create":
                    os.unlink(item["target"])
                else:
                    _write_atomic(item["target"], item["original"].encode("utf-8"), item["mode"])
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                os.rmdir(directory)
            except OSError:
                pass
        raise ToolFailure(
            "patch_rejected", "changes could not be written; nothing was applied"
        ) from None
    return {
        "files": [
            {k: item[k] for k in ("path", "action", "hunks", "added", "removed")} for item in plan
        ]
    }


# ------------------------------------------------------------------------ process control


def _become_subreaper():
    """Adopt orphaned descendants so they can be killed and reaped (no zombies, no escapes)."""
    try:
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except Exception:
        pass


def _parents():
    table = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "rb") as handle:
                tail = handle.read().rsplit(b")", 1)[1].split()
            table[int(name)] = int(tail[1])
        except (OSError, IndexError, ValueError):
            continue
    return table


def _descendants(me):
    table = _parents()
    found, frontier = set(), {me}
    while frontier:
        nxt = {pid for pid, parent in table.items() if parent in frontier and pid not in found}
        found |= nxt
        frontier = nxt
    return found


def _reap():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def kill_descendants(everything=False):
    """SIGKILL every descendant (or every process but PID 1 and ourselves) until none remain."""
    me = os.getpid()
    killed = 0
    for _ in range(20):
        if everything:
            victims = {p for p in _parents() if p not in (1, me)}
        else:
            victims = _descendants(me)
        if not victims:
            break
        for pid in victims:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except OSError:
                pass
        time.sleep(0.03)
        _reap()
    return killed


class _Capture:
    def __init__(self):
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def feed(self, chunk):
        self.total += len(chunk)
        room = CAPTURE_HEAD - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail = (self.tail + chunk)[-CAPTURE_TAIL:]

    def text(self):
        hidden = self.total - len(self.head) - len(self.tail)
        marker = b"" if hidden <= 0 else b"\n...[%d bytes omitted]...\n" % hidden
        return (bytes(self.head) + marker + bytes(self.tail)).decode("utf-8", "replace")


def _drain(pipe, capture):
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                return
            capture.feed(chunk)
    except (OSError, ValueError):
        return


def run_process(root, argv, cwd_rel, timeout):
    import resource  # POSIX-only; this function only ever runs inside the guest

    cwd_rel = clean_path(cwd_rel, allow_root=True)
    cwd = root if cwd_rel == "." else resolve(root, cwd_rel, want="dir")
    _become_subreaper()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(COMMAND_ENV),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        raise ToolFailure(
            "not_found", "command could not be started: %s" % error.strerror
        ) from None
    out, err = _Capture(), _Capture()
    threads = [
        threading.Thread(target=_drain, args=(child.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(child.stderr, err), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        code = child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        code = child.wait()
    leftover = kill_descendants()
    for thread in threads:
        thread.join(timeout=2)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "exit_code": code if code >= 0 else None,
        "signal": -code if code < 0 else None,
        "timed_out": timed_out,
        "stdout": out.text(),
        "stderr": err.text(),
        "stdout_bytes": out.total,
        "stderr_bytes": err.total,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "user_ms": int((after.ru_utime - before.ru_utime) * 1000),
        "system_ms": int((after.ru_stime - before.ru_stime) * 1000),
        "max_rss_kb": after.ru_maxrss,
        "descendants_killed": leftover,
    }


def op_run_command(root, req):
    return run_process(
        root,
        ["/bin/sh", "-c", req["command"]],
        req.get("cwd", "."),
        float(req["timeout_seconds"]),
    )


def op_run_argv(root, req):
    argv = req["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ToolFailure("invalid_arguments", "argv must be a non-empty list of strings")
    return run_process(root, argv, req.get("cwd", "."), float(req["timeout_seconds"]))


def op_sweep(root, req):
    return {"killed": kill_descendants(everything=True)}


def op_process_count(root, req):
    me = os.getpid()
    return {"processes": sorted(p for p in _parents() if p not in (1, me))}


def op_restore_modes(root, req):
    changed = 0
    for rel, mode in sorted(req["modes"].items()):
        path = resolve(root, clean_path(rel), want="file")
        os.chmod(path, (int(mode) & 0o777) | 0o600)
        changed += 1
    return {"changed": changed}


MAX_REPORTED = 100


def _encodable(name):
    try:
        name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def op_remove_unsafe(root, req):
    """Delete links, special files and unrepresentable names; make every entry readable."""
    removed = []
    count = 0
    stack = [("", root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            os.chmod(abs_dir, os.lstat(abs_dir).st_mode | 0o700)
            names = os.listdir(abs_dir)
        except OSError:
            continue
        for name in names:
            absolute = os.path.join(abs_dir, name)
            rel = name if not rel_dir else rel_dir + "/" + name
            try:
                mode = os.lstat(absolute).st_mode
                if not _encodable(name):
                    if stat.S_ISDIR(mode):
                        import shutil

                        shutil.rmtree(absolute, ignore_errors=True)
                    else:
                        os.unlink(absolute)
                    count += 1
                    if len(removed) < MAX_REPORTED:
                        removed.append(ascii(name))
                elif stat.S_ISDIR(mode):
                    stack.append((rel, absolute))
                elif stat.S_ISREG(mode):
                    os.chmod(absolute, mode | 0o600)
                else:
                    os.unlink(absolute)
                    count += 1
                    if len(removed) < MAX_REPORTED:
                        removed.append(rel)
            except OSError:
                continue
    return {"removed": sorted(removed), "removed_count": count}


def op_stats(root, req):
    files = total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        for name in os.listdir(current):
            absolute = os.path.join(current, name)
            info = os.lstat(absolute)
            if stat.S_ISDIR(info.st_mode):
                stack.append(absolute)
            elif stat.S_ISREG(info.st_mode):
                files += 1
                total += info.st_size
    return {"files": files, "bytes": total}


def op_mkdirs(root, req):
    made = 0
    for rel in sorted(req["dirs"]):
        parts = clean_path(rel).split("/")
        current = root
        for part in parts:
            current = os.path.join(current, part)
            if os.path.lexists(current):
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    raise ToolFailure("path_forbidden", "symbolic links are not followed")
                continue
            os.mkdir(current, 0o755)
            made += 1
    return {"made": made}


OPS = {
    "stats": op_stats,
    "mkdirs": op_mkdirs,
    "remove_unsafe": op_remove_unsafe,
    "list_files": op_list_files,
    "read_file": op_read_file,
    "search": op_search,
    "apply_patch": op_apply_patch,
    "run_command": op_run_command,
    "run_argv": op_run_argv,
    "sweep": op_sweep,
    "process_count": op_process_count,
    "restore_modes": op_restore_modes,
}


def _load_request(argument):
    if argument.startswith("@file:"):
        name = argument[6:]
        if not re.fullmatch(r"\.pcb_inbox/[A-Za-z0-9_-]{1,64}\.req", name):
            raise ToolFailure("path_forbidden", "request file must be in the harness inbox")
        path = os.path.join(ROOT, name)
        try:
            with open(path, "rb") as handle:
                data = handle.read(4 * 1024 * 1024)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return json.loads(data)
    return json.loads(argument)


def main(argv):
    try:
        request = _load_request(argv[1])
        handler = OPS[request["op"]]
        result = {"ok": True, **handler(ROOT, request)}
    except ToolFailure as failure:
        result = {"ok": False, "error": failure.code, "message": failure.message[:300]}
        result.update(failure.extra)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, MemoryError) as error:
        result = {"ok": False, "error": "tool_failure", "message": type(error).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
