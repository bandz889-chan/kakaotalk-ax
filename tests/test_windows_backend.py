"""Windows backend tests against a fake KakaoTalk PC window tree (runs on any OS)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kakaotalk_windows as kw
from kakaotalk_ax import AXSendEngine
from kakaotalk_windows import WindowsKakao, parse_copied_chat

COPIED = """2026년 11월 11일 수요일
[기요니] [오후 2:01] 안녕하세요
[기요니] [오후 2:01] 두 줄
메시지입니다
[큐닷 정찬미] [오후 2:07] 링크 https://cmcr.kr/ 확인 부탁드려요
[큐닷 정찬미] [오후 2:08] 사진
"""


class FakeWin32:
    """Simulates the KakaoTalk PC window classes the backend relies on."""

    def __init__(self, rooms=None, uia=None):
        self.nodes, self.parent, self.next = {}, {}, 100
        self.clipboard, self.sequence = "user clipboard", 1
        self.history = {}
        self.known_rooms = rooms if rooms is not None else {"기요니": COPIED, "큐닷 정찬미": ""}
        self.uia = uia
        self.keys = []
        self.top = []
        self.main = self.add(None, "EVA_Window_Dblclk", "카카오톡")
        child = self.add(self.main, "EVA_ChildWindow")
        self.add(child, "EVA_Window")  # friends tab
        chats = self.add(child, "EVA_Window")
        self.search = self.add(chats, "Edit")
        self.room_list = self.add(chats, "EVA_VH_ListControl_Dblclk")
        self.other = self.add(None, "Chrome_WidgetWin_1", "기요니", process="chrome.exe")

    def add(self, parent, cls, title="", process="kakaotalk.exe"):
        self.next += 1
        hwnd = self.next
        self.nodes[hwnd] = {"cls": cls, "title": title, "text": "", "children": [], "process": process}
        self.parent[hwnd] = parent
        if parent is None:
            self.top.append(hwnd)
        else:
            self.nodes[parent]["children"].append(hwnd)
        return hwnd

    def open_room(self, name):
        hwnd = self.add(None, "#32770", name)
        self.add(hwnd, "EVA_VH_ListControl_Dblclk")
        self.add(hwnd, "RICHEDIT50W")
        self.history.setdefault(name, self.known_rooms[name])
        return hwnd

    def root(self, hwnd):
        while self.parent.get(hwnd) is not None:
            hwnd = self.parent[hwnd]
        return hwnd

    # Win32 surface used by WindowsKakao
    def sleep(self, seconds):
        pass

    def top_windows(self):
        return list(self.top)

    def descendants(self, hwnd):
        found = []
        for child in self.nodes[hwnd]["children"]:
            found += [child] + self.descendants(child)
        return found

    def is_window(self, hwnd):
        return hwnd in self.nodes

    def class_name(self, hwnd):
        return self.nodes[hwnd]["cls"]

    def title(self, hwnd):
        return self.nodes[hwnd]["title"]

    def process_name(self, hwnd):
        return self.nodes[hwnd]["process"]

    def control_text(self, hwnd):
        return self.nodes[hwnd]["text"]

    def set_text(self, hwnd, text):
        self.nodes[hwnd]["text"] = text

    def post_key(self, hwnd, key, modifiers=()):
        self.keys.append((self.nodes[hwnd]["cls"], key, tuple(modifiers)))
        room = self.nodes[self.root(hwnd)]["title"]
        cls = self.nodes[hwnd]["cls"]
        if cls == "EVA_VH_ListControl_Dblclk" and key == kw.VK_C and kw.VK_CONTROL in modifiers:
            self.clipboard, self.sequence = self.history[room], self.sequence + 1
        elif cls == "RICHEDIT50W" and key == kw.VK_RETURN and self.nodes[hwnd]["text"]:
            body = self.nodes[hwnd]["text"].replace("\r\n", "\n")
            self.history[room] += "[큐닷 정찬미] [오후 3:00] " + body + "\n"
            self.nodes[hwnd]["text"] = ""
        elif cls == "Edit" and key == kw.VK_RETURN:
            query = self.nodes[hwnd]["text"]
            hits = [name for name in self.known_rooms if query and query in name]
            if hits:
                self.open_room(hits[0])

    def post_wheel(self, hwnd, notches=10):
        room = self.nodes[self.root(hwnd)]["title"]
        self.history[room] = "[기요니] [오전 9:00] 예전 메시지\n" + self.history[room]

    def close(self, hwnd):
        for child in self.descendants(hwnd):
            self.nodes.pop(child)
        self.nodes.pop(hwnd)
        self.top.remove(hwnd)

    def clipboard_sequence(self):
        return self.sequence

    def clipboard_text(self):
        return self.clipboard

    def set_clipboard_text(self, text):
        self.clipboard, self.sequence = text, self.sequence + 1

    def uia_names(self, hwnd):
        return self.uia


class WindowsTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch("kakaotalk_ax.UI_ROOT", Path(self.directory.name) / "ui")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = FakeWin32()
        self.native = WindowsKakao(self.fake, timeout=0.05)


class ParserTests(unittest.TestCase):
    def test_copied_text_keeps_sender_time_date_multiline_links_and_attachments(self):
        rows = parse_copied_chat(COPIED)
        self.assertEqual([row["kind"] for row in rows], ["text", "text", "text", "attachment"])
        self.assertEqual(rows[0]["sender"], "기요니")
        self.assertEqual(rows[0]["timeText"], "오후 2:01")
        self.assertEqual(rows[0]["dateText"], "2026년 11월 11일 수요일")
        self.assertEqual(rows[1]["text"], "두 줄\n메시지입니다")
        self.assertEqual(rows[2]["links"], ["https://cmcr.kr/"])
        self.assertIsNone(rows[3]["text"])
        self.assertEqual(rows[3]["attachments"][0]["type"], "image")

    def test_lines_before_first_message_are_kept_unclassified_not_guessed(self):
        rows = parse_copied_chat("홍길동님이 들어왔습니다.\r\n[A] [10:01 AM] hi")
        self.assertEqual(rows[0]["kind"], "unclassified")
        self.assertIsNone(rows[0]["sender"])
        self.assertEqual(rows[1]["sender"], "A")
        self.assertEqual(rows[1]["timeText"], "10:01 AM")


class WindowsReadTests(WindowsTestCase):
    def test_windows_lists_only_kakaotalk_chat_windows(self):
        self.fake.open_room("기요니")
        result = self.native.windows()
        self.assertEqual(result["items"], [{"windowTitle": "기요니", "readable": True}])
        self.assertTrue(result["mainWindowFound"])
        self.assertEqual(result["source"], "windows_win32")

    def test_read_copies_parses_paginates_and_restores_clipboard(self):
        self.fake.open_room("기요니")
        first = self.native.read_messages("기요니", limit=2)
        self.assertEqual(first["status"], "ok")
        self.assertEqual([row["kind"] for row in first["items"]], ["text", "attachment"])
        self.assertTrue(first["hasMore"])
        self.assertEqual(self.fake.clipboard, "user clipboard")
        self.assertTrue(first["coverage"]["clipboardRestored"])
        second = self.native.read_messages("기요니", limit=2, offset=first["nextOffset"], snapshot_id=first["snapshotId"])
        self.assertEqual(second["items"][0]["text"], "안녕하세요")
        self.assertFalse(second["hasMore"])
        self.fake.history["기요니"] += "[기요니] [오후 4:00] 새 메시지\n"
        changed = self.native.read_messages("기요니", limit=2, offset=2, snapshot_id=first["snapshotId"])
        self.assertEqual(changed["status"], "snapshot_changed")

    def test_closed_or_duplicate_rooms_are_blocked(self):
        self.assertEqual(self.native.read_messages("기요니")["reason"], "target_room_window_not_open")
        self.fake.open_room("기요니")
        self.fake.open_room("기요니")
        self.assertEqual(self.native.read_messages("기요니")["reason"], "duplicate_window_title")

    def test_attachment_capture_is_reported_unsupported(self):
        self.assertEqual(self.native.read_attachment("기요니", 0, "0" * 24)["reason"],
                         "attachment_capture_not_supported_on_windows")

    def test_load_more_scrolls_and_reports_new_rows(self):
        self.fake.open_room("기요니")
        result = self.native.load_more("기요니", 1)
        self.assertTrue(result["contentChanged"])
        self.assertEqual(result["source"], "windows_win32")

    def test_search_uses_copied_messages_without_app_search(self):
        self.fake.open_room("기요니")
        result = self.native.search_messages("기요니", "링크")
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["appSearchUsed"])
        self.assertTrue(result["appSearchRequested"])


class WindowsControlTests(WindowsTestCase):
    def test_open_uses_app_search_then_restores_search_text(self):
        self.fake.set_text(self.fake.search, "이전 검색")
        result = self.native.open_room("기요니")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["alreadyOpen"])
        self.assertEqual(self.fake.control_text(self.fake.search), "이전 검색")
        self.assertTrue(self.native.open_room("기요니")["alreadyOpen"])

    def test_open_closes_a_different_room_opened_by_search(self):
        self.fake.known_rooms = {"큐닷 정찬미": "", "정찬미": ""}
        result = self.native.open_room("찬미")
        self.assertEqual(result["reason"], "opened_room_title_mismatch")
        self.assertEqual(self.native.windows()["items"], [])

    def test_require_self_is_not_claimed_on_windows(self):
        self.fake.open_room("큐닷 정찬미")
        self.assertEqual(self.native.open_room("큐닷 정찬미", require_self=True)["reason"],
                         "self_room_not_verifiable_on_windows")

    def test_close_preserves_drafts(self):
        hwnd = self.fake.open_room("기요니")
        composer = [h for h in self.fake.descendants(hwnd) if self.fake.class_name(h) == "RICHEDIT50W"][0]
        self.fake.set_text(composer, "쓰던 글")
        self.assertEqual(self.native.close_room("기요니")["reason"], "existing_draft_preserved")
        self.fake.set_text(composer, "")
        self.assertEqual(self.native.close_room("기요니")["status"], "ok")
        self.assertFalse(self.fake.is_window(hwnd))

    def test_room_list_needs_uiautomation_and_reads_names_when_present(self):
        self.assertEqual(self.native.rooms()["reason"], "room_list_requires_uiautomation_package")
        self.fake.uia = ["기요니", "큐닷 정찬미"]
        result = self.native.rooms("정찬미")
        self.assertEqual([item["name"] for item in result["items"]], ["큐닷 정찬미"])
        searched = self.native.search_rooms("기요")
        self.assertEqual(searched["searchQuery"], "기요")
        self.assertTrue(searched["itemsExposed"])


class WindowsSendTests(WindowsTestCase):
    def engine(self):
        return AXSendEngine(self.native, state_root=Path(self.directory.name) / "send")

    def test_send_opens_types_presses_enter_and_observes_once(self):
        result = self.engine().send("기요니", "안녕하세요\n두 번째 줄", "op-1", wait_seconds=0.2)
        self.assertEqual(result["status"], "observed_in_ax")
        self.assertEqual(result["source"], "windows_win32")
        self.assertIn("[큐닷 정찬미] [오후 3:00] 안녕하세요\n두 번째 줄", self.fake.history["기요니"])
        again = self.engine().send("기요니", "안녕하세요\n두 번째 줄", "op-1", wait_seconds=0.2)
        self.assertTrue(again["deduplicated"])
        self.assertEqual(self.fake.history["기요니"].count("두 번째 줄"), 1)

    def test_existing_draft_blocks_without_typing(self):
        hwnd = self.fake.open_room("기요니")
        composer = [h for h in self.fake.descendants(hwnd) if self.fake.class_name(h) == "RICHEDIT50W"][0]
        self.fake.set_text(composer, "쓰던 글")
        result = self.engine().send("기요니", "보낼 글", "op-2", wait_seconds=0)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "existing_draft_preserved")
        self.assertEqual(self.fake.control_text(composer), "쓰던 글")


class PlatformTests(unittest.TestCase):
    def test_win32_layer_refuses_to_load_off_windows(self):
        if kw.sys.platform != "win32":
            with self.assertRaises(ValueError):
                kw.Win32()

    def test_macos_keeps_native_ax_backend(self):
        import kakaotalk_automation
        if kakaotalk_automation.sys.platform == "darwin":
            self.assertIs(kakaotalk_automation.NativeAX, kakaotalk_automation.MacNativeAX)


if __name__ == "__main__":
    unittest.main()
