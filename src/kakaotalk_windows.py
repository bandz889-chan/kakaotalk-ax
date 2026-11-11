"""Experimental Windows backend for the official KakaoTalk PC app.

The same tool surface as the macOS AX helper, driven through the running app only:

* window discovery: Win32 window classes of the KakaoTalk.exe process
* reading: the app's own "select all + copy" on the message list, then the copied
  text is parsed (the previous clipboard text is restored afterwards)
* opening rooms: the app's chat search box (WM_SETTEXT + Enter)
* sending: WM_SETTEXT on the room's composer, then Enter, then a re-read to observe
* room list (optional): Windows UI Automation via the ``uiautomation`` package

No database files, no network protocol, no physical mouse movement. Window class
names come from the KakaoTalk PC app and can change with app updates; every step
stops with a reason code when the expected control is not found exactly once.
"""

import re
import sys
import time
from pathlib import Path

from kakaotalk_ax import AXOperations, boolean, integer, query_text, title, ui_session
from kakaotalk_state import digest

SOURCE = "windows_win32"
PROCESS_NAMES = ("kakaotalk.exe",)
MAIN_TITLES = ("카카오톡", "KakaoTalk")
MAIN_CHILD_CLASS = "EVA_ChildWindow"
TAB_CLASS = "EVA_Window"
LIST_CLASS = "EVA_VH_ListControl_Dblclk"
COMPOSER_CLASS = "RICHEDIT50W"
SEARCH_EDIT_CLASS = "Edit"

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_MOUSEWHEEL = 0x020A
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_A = 0x41
VK_C = 0x43
CF_UNICODETEXT = 13

DATE_LINE = re.compile(
    r"^(?:-+\s*)?(?:\d{4}년\s*\d{1,2}월\s*\d{1,2}일(?:\s*[월화수목금토일]요일)?"
    r"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+\w+\s+\d{1,2},\s+\d{4})(?:\s*-+)?$")
MESSAGE_LINE = re.compile(
    r"^\[(?P<sender>[^\]]+)\]\s*\[(?P<time>(?:오전|오후|AM|PM)\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}(?:\s*(?:AM|PM))?)\]\s?(?P<body>.*)$",
    flags=re.IGNORECASE)
ATTACHMENT_LABELS = {
    "사진": "image", "동영상": "video", "이모티콘": "emoticon", "음성메시지": "audio",
    "photo": "image", "video": "video", "emoticon": "emoticon", "voice message": "audio",
}
ATTACHMENT_PATTERN = re.compile(r"^(?:사진\s*\d+장|photos?\s*\d+|파일:\s*.+|file:\s*.+)$", flags=re.IGNORECASE)
URL = re.compile(r"https?://\S+")


def parse_copied_chat(text):
    """Parse KakaoTalk PC copied text into rows; unknown lines are kept, never guessed.

    Lines that do not start a new "[sender] [time] body" message and are not date
    separators are attached to the previous message as continuation lines, since a
    multi-line message is copied that way. Before the first message they are kept
    as unclassified rows.
    """
    rows, date = [], None
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if rows and rows[-1]["kind"] == "text":
                rows[-1]["_lines"].append("")
            continue
        if DATE_LINE.match(line.strip()):
            date = line.strip().strip("-").strip()
            continue
        match = MESSAGE_LINE.match(line)
        if match:
            rows.append({"sender": match["sender"].strip(), "timeText": match["time"].strip(),
                         "dateText": date, "_lines": [match["body"]], "kind": "text"})
        elif rows and rows[-1]["kind"] == "text":
            rows[-1]["_lines"].append(line)
        else:
            rows.append({"sender": None, "timeText": None, "dateText": date, "_lines": [line], "kind": "unclassified"})
    items = []
    for index, row in enumerate(rows):
        body = "\n".join(row.pop("_lines")).rstrip("\n")
        label = body.strip()
        kind = row["kind"]
        attachments = []
        if kind == "text" and (label.casefold() in ATTACHMENT_LABELS or ATTACHMENT_PATTERN.match(label)):
            kind = "attachment"
            attachments = [{"type": ATTACHMENT_LABELS.get(label.casefold(), "file" if ":" in label else "image"),
                            "textSource": "copied_text", "description": label, "title": None}]
        items.append({"rowIndex": index, "kind": kind, "attachments": attachments,
                      "links": URL.findall(body) if kind == "text" else [],
                      "text": body if kind == "text" else None,
                      "sender": row["sender"] if kind != "unclassified" else None,
                      "senderSource": "copied_text" if row["sender"] else None,
                      "timeText": row["timeText"], "dateText": row["dateText"],
                      "axParts": [] if kind != "unclassified" else [{"role": "CopiedLine", "AXValue": body}]})
    return items


class Win32:
    """Thin ctypes layer over user32/kernel32. Only loaded on Windows."""

    def __init__(self):
        if sys.platform != "win32":
            raise ValueError("Windows is required for the Win32 KakaoTalk backend")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wt = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k, w = self.user32, self.kernel32, wintypes
        LPARAM, WPARAM, LRESULT = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
        self.LPARAM = LPARAM
        self.ENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, LPARAM)
        signatures = {
            (u, "EnumWindows"): ([self.ENUMPROC, LPARAM], w.BOOL),
            (u, "EnumChildWindows"): ([w.HWND, self.ENUMPROC, LPARAM], w.BOOL),
            (u, "GetClassNameW"): ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u, "GetWindowTextW"): ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u, "GetWindowTextLengthW"): ([w.HWND], ctypes.c_int),
            (u, "IsWindow"): ([w.HWND], w.BOOL),
            (u, "IsWindowVisible"): ([w.HWND], w.BOOL),
            (u, "GetWindowThreadProcessId"): ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (u, "SendMessageW"): ([w.HWND, w.UINT, WPARAM, LPARAM], LRESULT),
            (u, "PostMessageW"): ([w.HWND, w.UINT, WPARAM, LPARAM], w.BOOL),
            (u, "MapVirtualKeyW"): ([w.UINT, w.UINT], w.UINT),
            (u, "GetKeyboardState"): ([ctypes.POINTER(ctypes.c_ubyte)], w.BOOL),
            (u, "SetKeyboardState"): ([ctypes.POINTER(ctypes.c_ubyte)], w.BOOL),
            (u, "AttachThreadInput"): ([w.DWORD, w.DWORD, w.BOOL], w.BOOL),
            (u, "GetWindowRect"): ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (u, "OpenClipboard"): ([w.HWND], w.BOOL),
            (u, "CloseClipboard"): ([], w.BOOL),
            (u, "EmptyClipboard"): ([], w.BOOL),
            (u, "GetClipboardData"): ([w.UINT], w.HANDLE),
            (u, "SetClipboardData"): ([w.UINT, w.HANDLE], w.HANDLE),
            (u, "IsClipboardFormatAvailable"): ([w.UINT], w.BOOL),
            (u, "GetClipboardSequenceNumber"): ([], w.DWORD),
            (k, "GetCurrentThreadId"): ([], w.DWORD),
            (k, "OpenProcess"): ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (k, "CloseHandle"): ([w.HANDLE], w.BOOL),
            (k, "QueryFullProcessImageNameW"): ([w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            (k, "GlobalAlloc"): ([w.UINT, ctypes.c_size_t], w.HGLOBAL),
            (k, "GlobalLock"): ([w.HGLOBAL], ctypes.c_void_p),
            (k, "GlobalUnlock"): ([w.HGLOBAL], w.BOOL),
            (k, "GlobalFree"): ([w.HGLOBAL], w.HGLOBAL),
        }
        for (library, name), (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)

    def _collect(self, parent=None):
        found = []

        def callback(hwnd, _):
            found.append(hwnd)
            return True
        procedure = self.ENUMPROC(callback)
        if parent is None:
            self.user32.EnumWindows(procedure, 0)
        else:
            self.user32.EnumChildWindows(parent, procedure, 0)
        return found

    def top_windows(self):
        return [h for h in self._collect() if self.user32.IsWindowVisible(h)]

    def descendants(self, hwnd):
        return self._collect(hwnd)

    def is_window(self, hwnd):
        return bool(self.user32.IsWindow(hwnd))

    def class_name(self, hwnd):
        buffer = self.ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    def title(self, hwnd):
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def process_name(self, hwnd):
        pid = self.wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = self.ctypes.create_unicode_buffer(1024)
            size = self.wt.DWORD(1024)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, self.ctypes.byref(size)):
                return ""
            return Path(buffer.value).name.casefold()
        finally:
            self.kernel32.CloseHandle(handle)

    def control_text(self, hwnd):
        length = self.user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, self.ctypes.addressof(buffer))
        return buffer.value

    def set_text(self, hwnd, text):
        buffer = self.ctypes.create_unicode_buffer(text)
        self.user32.SendMessageW(hwnd, WM_SETTEXT, 0, self.ctypes.addressof(buffer))

    def post_key(self, hwnd, key, modifiers=()):
        """Post a key to one window; modifiers are set in the attached thread's key state."""
        u = self.user32
        target = u.GetWindowThreadProcessId(hwnd, None)
        current = self.kernel32.GetCurrentThreadId()
        attached = bool(modifiers) and bool(u.AttachThreadInput(current, target, True))
        state = (self.ctypes.c_ubyte * 256)()
        previous = None
        try:
            if modifiers:
                u.GetKeyboardState(state)
                previous = (self.ctypes.c_ubyte * 256)(*state)
                for modifier in modifiers:
                    state[modifier] = 0x80
                u.SetKeyboardState(state)
                time.sleep(0.02)
            scan = u.MapVirtualKeyW(key, 0)
            down = self.LPARAM(1 | (scan << 16)).value
            up = self.LPARAM(1 | (scan << 16) | 0xC0000000).value
            u.PostMessageW(hwnd, WM_KEYDOWN, key, down)
            time.sleep(0.03)
            u.PostMessageW(hwnd, WM_KEYUP, key, up)
            time.sleep(0.12 if modifiers else 0.03)
        finally:
            if previous is not None:
                u.SetKeyboardState(previous)
            if attached:
                u.AttachThreadInput(current, target, False)

    def post_wheel(self, hwnd, notches=10):
        rect = self.wt.RECT()
        self.user32.GetWindowRect(hwnd, self.ctypes.byref(rect))
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        position = self.LPARAM(((y & 0xFFFF) << 16) | (x & 0xFFFF)).value
        for _ in range(notches):
            self.user32.PostMessageW(hwnd, WM_MOUSEWHEEL, (120 << 16), position)
            time.sleep(0.03)

    def close(self, hwnd):
        self.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def _open_clipboard(self):
        for _ in range(20):
            if self.user32.OpenClipboard(None):
                return True
            time.sleep(0.05)
        raise RuntimeError("Clipboard is busy")

    def clipboard_sequence(self):
        return int(self.user32.GetClipboardSequenceNumber())

    def clipboard_text(self):
        self._open_clipboard()
        try:
            if not self.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = self.kernel32.GlobalLock(handle)
            try:
                return self.ctypes.wstring_at(pointer) if pointer else None
            finally:
                self.kernel32.GlobalUnlock(handle)
        finally:
            self.user32.CloseClipboard()

    def set_clipboard_text(self, text):
        data = (text + "\0").encode("utf-16-le")
        self._open_clipboard()
        try:
            self.user32.EmptyClipboard()
            handle = self.kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
            pointer = self.kernel32.GlobalLock(handle)
            self.ctypes.memmove(pointer, data, len(data))
            self.kernel32.GlobalUnlock(handle)
            if not self.user32.SetClipboardData(CF_UNICODETEXT, handle):
                self.kernel32.GlobalFree(handle)
        finally:
            self.user32.CloseClipboard()

    def uia_names(self, hwnd):
        """Names of list items under one control via UI Automation, or None if unavailable."""
        try:
            import uiautomation as automation
        except ImportError:
            return None
        control = automation.ControlFromHandle(hwnd)
        if control is None:
            return []
        names = []
        for child in control.GetChildren():
            name = (child.Name or "").strip()
            if name:
                names.append(name)
        return names


class WindowsKakao(AXOperations):
    """KakaoTalk PC (Windows) implementation of the kakaotalk_ax_* operations."""

    source = SOURCE

    def __init__(self, win32=None, timeout=3.0):
        self.w = win32 if win32 is not None else Win32()
        self.timeout = timeout
        self.clipboard_restored = True

    # --- discovery -----------------------------------------------------------------
    def build(self, kind="send"):
        if kind not in ("send", "read", "control"):
            raise ValueError("Unknown native bridge kind")
        return Path("windows-win32")  # Nothing to compile on Windows.

    def _kakao_top(self):
        return [h for h in self.w.top_windows() if self.w.process_name(h) in PROCESS_NAMES]

    def _find(self, parent, class_name):
        return [h for h in self.w.descendants(parent) if self.w.class_name(h).upper() == class_name.upper()]

    def _chat_windows(self):
        return [(self.w.title(h), h) for h in self._kakao_top() if self._find(h, COMPOSER_CLASS)]

    def _main(self):
        mains = [h for h in self._kakao_top() if not self._find(h, COMPOSER_CLASS) and
                 any(self.w.class_name(c) == MAIN_CHILD_CLASS for c in self.w.descendants(h))]
        if len(mains) > 1:  # Other KakaoTalk popups can share the layout; prefer the titled main window.
            mains = [h for h in mains if self.w.title(h) in MAIN_TITLES] or mains
        if len(mains) != 1:
            return None, ("kakaotalk_main_window_not_found" if not mains else "kakaotalk_main_window_not_unique")
        return mains[0], None

    def _room(self, window_title):
        matches = [h for name, h in self._chat_windows() if name == window_title]
        if not matches:
            return None, "target_room_window_not_open"
        if len(matches) > 1:
            return None, "duplicate_window_title"
        return matches[0], None

    def _search_edit(self, main):
        tabs = self._find(main, TAB_CLASS)
        edits = []
        for tab in tabs[1:2] or tabs:  # The second tab is the chat list in KakaoTalk PC.
            edits = self._find(tab, SEARCH_EDIT_CLASS)
            if edits:
                break
        return edits[0] if len(edits) >= 1 else None

    def _chat_list(self, main):
        tabs = self._find(main, TAB_CLASS)
        for tab in tabs[1:2] or tabs:
            lists = self._find(tab, LIST_CLASS)
            if len(lists) == 1:
                return lists[0]
        return None

    def _blocked(self, reason, **extra):
        return {"status": "blocked", "reason": reason, "source": SOURCE, "dispatchAttempted": False,
                "databaseCalls": 0, **extra}

    def _wait(self, predicate, seconds=None):
        until = time.monotonic() + (self.timeout if seconds is None else seconds)
        while True:
            value = predicate()
            if value or time.monotonic() >= until:
                return value
            self.w.sleep(0.1)

    def windows(self):
        items = []
        chats = self._chat_windows()
        for name, hwnd in chats:
            lists = self._find(hwnd, LIST_CLASS)
            composers = self._find(hwnd, COMPOSER_CLASS)
            same = sum(1 for other, _ in chats if other == name)
            readable = bool(name) and same == 1 and len(lists) == 1 and len(composers) == 1
            item = {"windowTitle": name, "readable": readable}
            if not readable:
                item["reason"] = ("duplicate_window_title" if same > 1 else
                                  "message_list_not_unique" if len(lists) != 1 else "composer_not_unique")
            items.append(item)
        main, _ = self._main()
        return {"status": "ok", "source": SOURCE, "items": items, "mainWindowFound": main is not None,
                "dispatchAttempted": False, "databaseCalls": 0}

    # --- reading -------------------------------------------------------------------
    def _copy_rows(self, hwnd):
        lists = self._find(hwnd, LIST_CLASS)
        if len(lists) != 1:
            return None, "message_list_not_unique"
        saved = self.w.clipboard_text()
        sequence = self.w.clipboard_sequence()
        self.clipboard_restored = True
        try:
            self.w.post_key(lists[0], VK_A, (VK_CONTROL,))
            self.w.post_key(lists[0], VK_C, (VK_CONTROL,))
            changed = self._wait(lambda: self.w.clipboard_sequence() != sequence, 2.0)
            if not changed:
                return None, "copy_not_observed"
            copied = self.w.clipboard_text()
        finally:
            if self.w.clipboard_sequence() != sequence:
                if saved is not None:
                    self.w.set_clipboard_text(saved)
                else:  # Non-text clipboard content (e.g. an image) cannot be restored.
                    self.clipboard_restored = False
        return copied or "", None

    def read_messages(self, window_title, limit=50, offset=0, snapshot_id=""):
        title(window_title)
        integer(limit, "limit", 1, 500)
        integer(offset, "offset", 0, 1000)
        if not isinstance(snapshot_id, str) or snapshot_id and not re.fullmatch(r"[0-9a-f]{24}", snapshot_id):
            raise ValueError("snapshot_id must be the ID returned by an AX read")
        if offset and not snapshot_id:
            raise ValueError("snapshot_id is required for offset > 0; read the first page again")
        started = time.monotonic()
        with ui_session():  # The clipboard is shared state, so copies run one at a time.
            hwnd, reason = self._room(window_title)
            if reason:
                return self._blocked(reason, windowTitle=window_title)
            copied, reason = self._copy_rows(hwnd)
        if reason:
            return self._blocked(reason, windowTitle=window_title)
        items = parse_copied_chat(copied)[-1000:]
        snapshot = digest(window_title + "\0" + str(hwnd) + "\0" + copied)[:24]
        if snapshot_id and snapshot_id != snapshot:
            return {"status": "snapshot_changed", "source": SOURCE, "reason": "Copied rows changed; restart at offset 0",
                    "items": [], "databaseCalls": 0, "uiActions": 0, "snapshotId": snapshot}
        end = max(0, len(items) - offset)
        start = max(0, end - limit)
        return {
            "status": "ok", "source": SOURCE, "windowTitle": window_title,
            "items": items[start:end], "total": len(items), "limit": limit, "offset": offset,
            "hasMore": start > 0, "nextOffset": offset + end - start if start > 0 else None,
            "snapshotId": snapshot, "textMessageCount": sum(item["kind"] == "text" for item in items),
            "unclassifiedRowCount": sum(item["kind"] == "unclassified" for item in items),
            "attachmentRowCount": sum(item["kind"] == "attachment" for item in items),
            "coverage": {"scope": "app_copied_loaded_messages", "historyComplete": False,
                         "consistency": "best_effort", "exposedRowCount": len(items),
                         "rowsTruncated": False, "rowStart": 0, "rowLimit": 1000, "scrollPerformed": False,
                         "clipboardRestored": self.clipboard_restored},
            "databaseCalls": 0, "uiActions": 2, "elapsedMs": round((time.monotonic()-started)*1000, 1),
        }

    def read_attachment(self, window_title, row_index, snapshot_id, output_path=None, ocr=False):
        title(window_title)
        return self._blocked("attachment_capture_not_supported_on_windows", windowTitle=window_title)

    def search_messages(self, window_title, query, limit=100, load_pages=0, case_sensitive=False,
                        use_app_search=True, max_steps=10, context_rows=2):
        """The in-room app search is not driven on Windows; search the copied messages instead."""
        result = super().search_messages(window_title, query, limit, load_pages, case_sensitive,
                                         False, max_steps, context_rows)
        if use_app_search and result.get("status") == "ok":
            result["appSearchRequested"] = True
            result["note"] = "Windows backend searches the copied loaded messages; use load_pages for older ones."
        return result

    # --- UI control ----------------------------------------------------------------
    def _control(self, action, **arguments):
        with ui_session():
            handler = getattr(self, "_control_" + action, None)
            if handler is None:
                return self._blocked(action + "_not_supported_on_windows")
            return handler(**arguments)

    def _control_rooms(self):
        main, reason = self._main()
        if reason:
            return self._blocked(reason)
        listing = self._chat_list(main)
        names = self.w.uia_names(listing) if listing else []
        edit = self._search_edit(main)
        query = self.w.control_text(edit) if edit else ""
        if names is None:
            return self._blocked("room_list_requires_uiautomation_package",
                                 hint="pip install uiautomation, or open rooms by exact name with kakaotalk_ax_open")
        if not names:
            return self._blocked("room_list_not_exposed_on_windows",
                                 hint="open rooms by exact name with kakaotalk_ax_open")
        items = [{"name": name, "isSelf": None, "rowIndex": index, "labels": [], "preview": None}
                 for index, name in enumerate(names)]
        return {"status": "ok", "source": SOURCE, "items": items, "scope": "uia_exposed_room_list",
                "searchQuery": query, "databaseCalls": 0}

    def _control_search_rooms(self, query):
        query_text(query, empty=True)
        main, reason = self._main()
        if reason:
            return self._blocked(reason)
        edit = self._search_edit(main)
        if not edit:
            return self._blocked("room_search_field_not_found")
        self.w.set_text(edit, query)
        self.w.sleep(0.4)
        listed = self._control_rooms()
        if listed.get("status") != "ok":
            return {"status": "ok", "source": SOURCE, "searchQuery": query, "items": [],
                    "itemsExposed": False, "detail": listed.get("reason"), "databaseCalls": 0}
        return {**listed, "searchQuery": query, "itemsExposed": True}

    def _control_open(self, windowTitle, requireSelf=False):
        hwnd, reason = self._room(windowTitle)
        if reason == "duplicate_window_title":
            return self._blocked(reason, windowTitle=windowTitle)
        if hwnd:
            if requireSelf:
                return self._blocked("self_room_not_verifiable_on_windows", windowTitle=windowTitle)
            return {"status": "ok", "source": SOURCE, "windowTitle": windowTitle, "alreadyOpen": True}
        if requireSelf:
            return self._blocked("self_room_not_verifiable_on_windows", windowTitle=windowTitle)
        main, reason = self._main()
        if reason:
            return self._blocked(reason, windowTitle=windowTitle)
        edit = self._search_edit(main)
        if not edit:
            return self._blocked("room_search_field_not_found", windowTitle=windowTitle)
        before = {h for _, h in self._chat_windows()}
        previous = self.w.control_text(edit)
        try:
            self.w.set_text(edit, windowTitle)
            self.w.sleep(0.5)
            self.w.post_key(edit, VK_RETURN)
            new = self._wait(lambda: [(n, h) for n, h in self._chat_windows() if h not in before])
            if new:
                self.w.sleep(0.3)  # Let the new window finish setting its title.
                new = [(n, h) for n, h in self._chat_windows() if h not in before]
        finally:
            self.w.set_text(edit, previous)
        if not new:
            return self._blocked("room_not_found_by_search", windowTitle=windowTitle, roomSearchUsed=True)
        exact = [h for n, h in new if n == windowTitle]
        if len(exact) != 1 or len(new) != 1:
            for name, h in new:  # Close only what this call opened, and only when no draft exists.
                composers = self._find(h, COMPOSER_CLASS)
                if all(not self.w.control_text(c) for c in composers):
                    self.w.close(h)
            return self._blocked("opened_room_title_mismatch", windowTitle=windowTitle, roomSearchUsed=True,
                                 openedTitles=[n for n, _ in new])
        return {"status": "ok", "source": SOURCE, "windowTitle": windowTitle, "alreadyOpen": False,
                "roomSearchUsed": True}

    def _control_close(self, windowTitle):
        hwnd, reason = self._room(windowTitle)
        if reason:
            return self._blocked(reason, windowTitle=windowTitle)
        if any(self.w.control_text(c) for c in self._find(hwnd, COMPOSER_CLASS)):
            return self._blocked("existing_draft_preserved", windowTitle=windowTitle)
        self.w.close(hwnd)
        closed = self._wait(lambda: not self.w.is_window(hwnd))
        return ({"status": "ok", "source": SOURCE, "windowTitle": windowTitle} if closed else
                self._blocked("close_not_observed", windowTitle=windowTitle))

    def _control_search_close(self, windowTitle):
        title(windowTitle)
        return {"status": "ok", "source": SOURCE, "windowTitle": windowTitle, "searchUiTouched": False}

    def _control_load_older(self, windowTitle):
        hwnd, reason = self._room(windowTitle)
        if reason:
            return self._blocked(reason, windowTitle=windowTitle)
        lists = self._find(hwnd, LIST_CLASS)
        if len(lists) != 1:
            return self._blocked("message_list_not_unique", windowTitle=windowTitle)
        self.w.post_wheel(lists[0], 15)
        self.w.sleep(0.6)
        return {"status": "ok", "source": SOURCE, "windowTitle": windowTitle, "method": "wm_mousewheel"}

    # --- send bridge (used by AXSendEngine) ----------------------------------------
    def call(self, action, titles, text=None):
        if action == "windows":
            return self.windows()
        with ui_session():
            if action == "inspect":
                return self._inspect(titles[0])
            if action == "send":
                return self._send(titles[0], text)
            if action == "raise":
                return {"status": "ok", "source": SOURCE, "dispatchAttempted": False}
        raise ValueError("Unknown Windows bridge action")

    def _composer(self, window_title):
        hwnd, reason = self._room(window_title)
        if reason:
            return None, reason
        composers = self._find(hwnd, COMPOSER_CLASS)
        if len(composers) != 1:
            return None, "composer_not_unique"
        return composers[0], None

    def _inspect(self, window_title):
        composer, reason = self._composer(window_title)
        if reason:
            return self._blocked(reason, windowTitle=window_title)
        draft = self.w.control_text(composer)
        return {"status": "ready" if not draft else "draft_present", "source": SOURCE,
                "windowTitle": window_title, "dispatchAttempted": False}

    def _send(self, window_title, text):
        composer, reason = self._composer(window_title)
        if reason:
            return self._blocked(reason, windowTitle=window_title, composerChanged=False)
        if self.w.control_text(composer):
            return self._blocked("existing_draft_preserved", windowTitle=window_title, composerChanged=False)
        wanted = text.replace("\r\n", "\n").replace("\n", "\r\n")
        self.w.set_text(composer, wanted)
        written = self.w.control_text(composer).replace("\r\n", "\n").replace("\r", "\n")
        if written != text.replace("\r\n", "\n"):
            self.w.set_text(composer, "")
            return self._blocked("composer_text_mismatch", windowTitle=window_title, composerChanged=False)
        self.w.post_key(composer, VK_RETURN)
        return {"status": "dispatched", "source": SOURCE, "windowTitle": window_title,
                "dispatchAttempted": True, "composerChanged": True, "databaseCalls": 0}
