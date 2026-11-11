# kakaotalk-ax

macOS 카카오톡을 네이티브 Accessibility(AX) API로 읽고 조작하는 CLI·Python 헬퍼·MCP 서버입니다.
방 열기·닫기, 본문 읽기, 여러 방 병렬 읽기, 검색, 이전 대화 요청, 메시지 전송을 제공합니다.

모든 기능은 실행 중인 공식 카카오톡 앱의 AX 요소를 사용합니다.
실행 명령은 `kax`이며, MCP 서버는 `kax mcp`로 실행합니다.

## 설치

macOS, Python 3.10 이상, 로그인된 공식 카카오톡 앱, Swift 컴파일러가 필요합니다.
macOS 설정에서 실행하는 터미널 또는 에이전트에 손쉬운 사용 권한을 부여해야 합니다.

```bash
xcode-select --install
python3 -m venv .venv
.venv/bin/pip install -e '.[mcp,dev]'
.venv/bin/kax build --kind read
.venv/bin/kax build --kind control
.venv/bin/kax build --kind send
.venv/bin/kax status
```

`status`는 열린 창의 AX 상태를 읽습니다.
네이티브 브리지는 소스 해시에 따라 로컬 캐시에 컴파일합니다. Python 기본 기능에는 추가 런타임 의존성이 없고,
MCP에는 `mcp>=1.30,<2`, 개발용 스킬 검증에는 PyYAML을 사용합니다.

## Windows (실험)

> 이 포크에서 추가한 기능입니다. Windows PC에서 실제 카카오톡으로 아직 검증하지 않았습니다. 처음에는 본인에게만 보이는 방에서 읽기부터 확인하세요.

Windows에서도 같은 `kax` 명령과 13개 MCP 도구를 그대로 씁니다. macOS AX 대신 **실행 중인 카카오톡 PC 앱의 창**을 다룹니다.

| 동작 | Windows에서 하는 방법 |
| --- | --- |
| 열린 창 확인 | `KakaoTalk.exe` 프로세스의 창 중 입력창(`RICHEDIT50W`)이 있는 창을 대화방으로 봅니다 |
| 읽기 | 대화 목록(`EVA_VH_ListControl_Dblclk`)에 앱 기능인 전체 선택·복사(Ctrl+A, Ctrl+C)를 창 메시지로 보내고, 복사된 텍스트를 `[이름] [시각] 본문` 형식으로 나눕니다. 원래 클립보드 텍스트는 되돌려 놓습니다 |
| 방 열기 | 채팅 탭 검색창에 방 이름을 넣고 Enter. 다른 이름의 방이 열리면 그 창을 닫고 멈춥니다 |
| 보내기 | 입력창이 비어 있을 때만 본문을 넣고 Enter, 이후 다시 읽어서 새 메시지를 확인합니다 |
| 이전 대화 | 대화 목록에 마우스 휠 메시지를 보냅니다 |
| 방 목록 | `uiautomation` 패키지가 있을 때만 UI Automation으로 읽습니다 |

DB 파일, 카톡 서버 통신, 실제 마우스 이동은 쓰지 않습니다.

### 설치 (PowerShell)

Python 3.10 이상과 로그인된 카카오톡 PC 앱이 필요합니다. Swift 빌드는 필요 없습니다.

```
py -m venv .venv
.venv\Scripts\pip install -e ".[mcp,windows]"
.venv\Scripts\kax status
```

MCP 설정의 `command`는 `C:\\경로\\kakaotalk-ax\\.venv\\Scripts\\kax.exe`, `args`는 `["mcp"]`입니다.

### Windows에서 다른 점

- `sender`가 채워집니다. 복사된 텍스트의 `[이름]` 표기를 그대로 옮긴 값입니다(`senderSource=copied_text`).
- 읽기는 앱이 지금 불러온 메시지만 복사합니다. 더 이전 대화는 `kakaotalk_ax_load_more`로 먼저 불러오세요.
- 읽는 동안 클립보드를 잠깐 씁니다. 원래 클립보드가 텍스트가 아니면(이미지 등) 되돌리지 못하고 `clipboardRestored=false`로 알려줍니다.
- 지원하지 않음: 사진 캡처(`read_attachment`), 본인 방 확인(`require_self=True`), 앱 내 검색 이동. `kakaotalk_ax_search`는 복사된 본문에서 찾습니다.
- 카카오톡 PC 업데이트로 창 이름(클래스)이 바뀌면 `message_list_not_unique`, `room_search_field_not_found` 같은 이유 코드로 멈춥니다.
- 다른 사람의 대화를 모으는 데 쓰지 마세요. 계정 제재 가능성이 0%는 아닙니다.

## Python API

```python
from kakaotalk_automation import NativeAX

ax = NativeAX()
windows = ax.windows()
rooms = ax.rooms()
found = ax.search_rooms("방 이름")
opened = ax.open_room("정확한 방 이름")
page = ax.read_messages("정확한 방 이름", limit=50)
batch = ax.read_many(["방 A", "방 B"], close_after=True)
older = ax.load_more("정확한 방 이름", pages=1)
matching = ax.search_messages("정확한 방 이름", "검색어")
ax.close_search("정확한 방 이름")
ax.close_room("정확한 방 이름")
```

`read_messages`와 `windows`는 읽기 전용입니다. `rooms`는 채팅 탭을 선택할 수 있습니다.
방 열기·검색·이전 대화 요청 등은 앱 상태를 변경하며, 헬퍼가 AX 제어 동작을 직렬화합니다.
`read_many`는 필요한 창을 순서대로 열고 최대 4개 프로세스로 본문을 병렬 조회합니다.
`close_after=True`는 해당 호출이 새로 연 창만 닫으며 기존 창과 초안은 보존합니다.

자세한 인수와 도구 대응은 [AX API](skills/kakaotalk-ax/references/ax-api.md)를 참고하세요.

## 메시지 전송

명시적으로 요청받은 방과 본문에만 전송하세요. 요청별 `operation_id`를 한 번 정하고 보관합니다.

```python
result = ax.send_message(
    window_title="정확한 방 이름",
    text="요청받은 메시지",
    operation_id="request-unique-id",
    require_self=False,
)
# 결과가 불확실하면 원래 ID로 조회만 합니다.
result = ax.send_result("request-unique-id")
```

전송 헬퍼가 방 열기·기준 본문 조회·한 번의 AX 입력과 AXPress·결과 관찰을 묶습니다.
본인 채팅만 허용하려면 `require_self=True`를 지정합니다. 동명 방·기존 초안·확인할 수 없는 대상은 중단합니다.
전송 요청은 본문 해시와 함께 본인 전용 파일에 먼저 기록합니다. 같은 ID는 다시 보내지 않으며,
같은 ID를 다른 방이나 본문에 재사용하면 거부합니다.

`observed_in_ax`는 텍스트 행 증가와 빈 입력창을 관찰했다는 뜻입니다.
작성자·서버 ACK·상대방 읽음을 확인한 결과는 아닙니다. `outcome_unknown`, 응답 유실, 타임아웃에서는
새 ID로 재발송하지 말고 원래 ID로 `send_result`만 조회하세요. 불확실한 전송을 자동 재시도하지 않습니다.
파일 전송·메시지 수정·삭제는 제공하지 않습니다.

## CLI

```bash
.venv/bin/kax ax-windows
.venv/bin/kax ax-read --window-title '정확한 방 이름' --limit 50
.venv/bin/kax ax-call read_many --args-file /absolute/path/arguments.json
```

`ax-call`은 Python 메서드와 같은 이름과 JSON 인수를 사용합니다. 사용자 텍스트는 셸 코드 대신 JSON 파일로
전달하세요.
전송은 `ax-call send_message`, 결과 확인은 `ax-call send_result`입니다.

## MCP / 스킬

MCP 설정 예시입니다. 경로를 실제 체크아웃으로 바꾸세요.

```json
{
  "mcpServers": {
    "kakaotalk-ax": {
      "command": "/absolute/path/kakaotalk-ax/.venv/bin/kax",
      "args": ["mcp"]
    }
  }
}
```

| 기능 | 도구 |
|---|---|
| 열린 창 확인 | `kakaotalk_ax_windows` |
| 보이는 채팅방 목록 | `kakaotalk_ax_rooms` |
| 앱의 방 검색 | `kakaotalk_ax_search_rooms` |
| 방 열기 / 창 닫기 | `kakaotalk_ax_open` / `kakaotalk_ax_close` |
| 이미지 첨부 읽기 | `kakaotalk_ax_read_attachment` |
| 한 방 / 여러 방 읽기 | `kakaotalk_ax_read` / `kakaotalk_ax_read_many` |
| 이전 대화 요청 | `kakaotalk_ax_load_more` |
| 본문 검색 / 검색 닫기 | `kakaotalk_ax_search` / `kakaotalk_ax_close_search` |
| 전송 / 결과 확인 | `kakaotalk_ax_send` / `kakaotalk_ax_send_result` |

총 13개 AX 도구를 제공합니다.

저장소의 [카카오톡 스킬](skills/kakaotalk-ax/SKILL.md)은 이 AX 헬퍼를 사용합니다.
연결된 MCP가 없으면 스킬 클라이언트가 로컬 MCP 서버를 일회성으로 실행합니다.

```bash
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py status
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py tools
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py call kakaotalk_ax_read --args-file /absolute/path/arguments.json
```

스킬의 `status`도 AX 창 상태만 읽습니다. 별도 체크아웃은 `--root` 또는 `KAKAOTALK_AX_ROOT`로 지정합니다.
스킬은 저장소에 포함되며 자동으로 개인 스킬 폴더에 설치하지 않습니다.

## 읽기 범위와 제한

- `rooms`는 현재 보이는 행을 읽습니다. `reportedRowCount`는 앱이 보고한 테이블 행 수이며 반환한 방 목록과 다릅니다.
- `read_messages`는 열린 창의 현재 AX 노출 행을 최대 1000행 읽습니다. `limit`은 1~500입니다.
  읽은 범위는 `coverage`에 표시하며, 계정 전체·기간 전체의 기록을 보장하지 않습니다.
- `hasMore`는 수집한 AX 스냅샷 안의 다음 페이지입니다. `nextOffset`과 `snapshotId`를 다음 호출에 전달하고,
  `snapshot_changed`면 첫 페이지부터 다시 읽으세요. `hasMore=false`도 과거 전체 기록의 끝을 뜻하지 않습니다.
- 이미지 행은 `kind=attachment`와 `attachments`로, 나머지 본문 없는 행은 `unclassified`와 `axParts`로 보존합니다. 작성자·생략된 시각은 추측하지 않습니다.
- `load_more`는 AX 스크롤로 이전 대화를 요청합니다. 실제 로딩량은 앱이 결정하며 `historyComplete=false`,
  `endOfHistory=null`입니다. 본문 검색은 이전 결과 버튼을 최대 `max_steps`번 순회하고 각 시점의 AX 노출 본문과 주변 대화를 반환합니다.
- 여러 방 조회는 같은 시점의 원자적 스냅샷이 아닙니다. 방별 실패와 `partial`을 확인해야 합니다.
- 열린 창의 백그라운드·최소화 상태에서도 조회가 관찰되었지만, 앱이 AX 요소를 노출해야 합니다.
  닫힌 방은 먼저 열어야 하며 앱 버전이나 창 상태에 따라 접근성 구조가 달라질 수 있습니다.

## 검증

테스트에는 Swift 컴파일러가 필요합니다. 네이티브 판정 테스트는 실제 Swift 소스의 해당 구간을
합성 AX 요소로 컴파일하며, 실행 중인 카카오톡이나 손쉬운 사용 권한을 사용하지 않습니다.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 이미지 첨부와 검색 결과

```python
page = ax.read_messages("정확한 방 이름", limit=100)
photo = next(item for item in page["items"] if item["kind"] == "attachment")
image = ax.read_attachment("정확한 방 이름", photo["rowIndex"], page["snapshotId"])
print(image["imagePath"])  # 직접 볼 수 있는 PNG 이미지. 기본값은 OCR 없음.
# 필요한 경우에만 ocr=True를 전달합니다. 결과는 별도의 ocr 필드에 들어갑니다.

results = ax.search_messages("정확한 방 이름", "검색어", max_steps=10, context_rows=4)
```

이미지 읽기는 macOS 14.2+와 화면 기록 권한이 필요합니다. AX로 식별한 한 사진을 창 내부에서
스크롤해 보이게 하고 해당 창의 사진 영역만 캡처합니다. 원본 첨부 파일 다운로드가 아니므로
해상도가 낮을 수 있습니다. 물리 마우스·키보드 입력, 클립보드, DB는 사용하지 않습니다.
스크롤은 다른 변경이 관찰되지 않을 때 복원합니다. `output_path`를 생략하면 권한 0600의
임시 PNG를 반환하며, 호출자가 사용 후 삭제할 수 있습니다. 현재 스냅샷과 맞지 않는 행은
거부하므로 `snapshot_changed`면 다시 읽으세요. 기본 읽기는 캡처나 OCR을 하지 않습니다.

검색은 `searchVerified`와 `stopReason`을 확인하세요. 검색 입력 후 실제 일치 본문이 보이고
상태가 안정된 경우만 검증된 검색으로 반환합니다. 이전 결과 이동이 확인되지 않으면
이미 얻은 항목을 보존하면서 `partial`로 반환합니다. 결과가 없다는 것을 확인하지 못한
경우도 성공으로 단정하지 않습니다. `max_steps`는 1..50, `context_rows`는 0..10입니다.
검색 도구의 네이티브 이전/다음 버튼 식별은 현재 관찰한 AX 툴바 구조에 의존하며,
앱 업데이트로 구조가 바뀌면 `search_toolbar_layout_unknown`으로 중단합니다.
`navigation_exhausted`도 앱이 노출한 검색 탐색의 끝이며 계정 전체 기록의 완전성을 보장하지 않습니다.
각 항목에 `snapshotId`와 `searchVisit`을 붙이며, 서로 겹치는 스냅샷은 같은 메시지를
다시 포함할 수 있습니다(`duplicatesPossible`). 동일 본문의 서로 다른 메시지를 임의로 합치지 않습니다.
`total`은 수집한 일치 항목 수이며 고유 메시지 총수가 아닙니다.

## 라이선스

[MIT](LICENSE). 카카오톡 이용약관을 지키는지, 다른 사람의 대화를 어떻게 다루는지는 쓰는 사람의 책임입니다.
