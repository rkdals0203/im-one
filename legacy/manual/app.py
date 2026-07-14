"""
bond_manual.md / meeting_room_manual.md 기반 질의응답 웹앱.
외부 API 없이, 질문의 단어(한글 단어 + TR번호/테이블명 등 식별자)가
얼마나 포함되는지로 섹션별 점수를 매겨 가장 관련 있는 섹션을 그대로 보여준다.
질문에 회의실 관련 키워드가 있으면 meeting_room_manual.md를, 그 외에는
bond_manual.md를 검색한다.
"""
import math
import re
from pathlib import Path

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
BOND_FILE = BASE_DIR / "bond_manual.md"
MEETING_ROOM_FILE = BASE_DIR / "meeting_room_manual.md"
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")
TOP_K = 3
# 질문에 이 키워드가 있으면 채권 매뉴얼 대신 회의실 예약 매뉴얼을 검색한다.
MEETING_ROOM_KEYWORDS = {"회의실", "회의", "미팅룸", "미팅"}
# 접미사가 붙어도(예: "언제야") 걸러지도록 접두일치로 제외하는 질문투 표현
STOPWORD_PREFIXES = {
    "관련", "내용", "알려줘", "설명", "대해", "대한", "무엇", "언제", "어떻게",
    "해줘", "인가요", "되나요", "인지", "좀", "있나요", "뭐야", "뭔가요",
}


def split_into_chunks(text):
    """'## ' 단위(주제 섹션)로 분할하고, 각 섹션은 하위 '###' 내용을 포함한다."""
    lines = text.split("\n")
    chunks = []
    current = []
    for line in lines:
        if line.startswith("## ") and current:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c.strip()]


def tokenize(text):
    return TOKEN_RE.findall(text)


TR_RE = re.compile(r"^TR(\d{3,5})$", re.IGNORECASE)

# 질문에는 조사가 붙어("화면번호가", "회의실은") 매뉴얼 원형("화면번호", "회의실")과
# 글자 그대로는 일치하지 않는 경우가 많으므로, 흔한 조사를 뗀 형태도 함께 인식한다.
# 뒤쪽부터 더 긴 조사가 먼저 매칭되도록 길이 내림차순으로 나열한다.
PARTICLE_SUFFIXES = (
    "으로는", "에서는", "이라서", "부터는", "까지는",
    "이라", "에서", "으로", "부터", "까지", "이나", "라도",
    "은", "는", "이", "가", "을", "를", "의", "도", "만", "에", "와", "과", "로", "라",
)


def strip_particle(tok):
    for suf in PARTICLE_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    return None


def relevant_tokens(question):
    tokens = tokenize(question)
    kept = [
        t for t in tokens
        if len(t) >= 2 and not any(t.startswith(sw) for sw in STOPWORD_PREFIXES)
    ]
    expanded = []
    for tok in kept:
        expanded.append(tok)
        # 매뉴얼에는 "TR4014"와 "4014"가 섞여 쓰이므로 둘 다 인식하도록 확장
        m = TR_RE.match(tok)
        if m:
            expanded.append(m.group(1))
        # 조사가 붙은 형태("화면번호가")도 원형("화면번호")과 매칭되도록 확장
        stripped = strip_particle(tok)
        if stripped:
            expanded.append(stripped)
    return expanded


HANGUL_ONLY_RE = re.compile(r"^[가-힣]+$")


def _edit_distance(a, b, cutoff=2):
    """a와 b 사이의 편집 거리. cutoff보다 커지면 더 정확히 셀 필요 없이 cutoff+1을 반환한다."""
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(b)]


def fuzzy_contains(tok, text, max_distance=None):
    """오타(글자 다름/빠짐/더함) 허용 매칭. 한글 3자 이상 토큰에만 적용해
    짧은 토큰에서 오탐이 늘어나는 것을 피한다. 토큰이 길수록 오타가 여러 글자에
    걸쳐 있어도(예: 6자 중 2글자) 허용되도록 max_distance를 길이에 비례해 키운다."""
    if len(tok) < 3 or not HANGUL_ONLY_RE.match(tok):
        return False
    tlen = len(tok)
    if max_distance is None:
        max_distance = 1 if tlen <= 4 else 2
    for wlen in range(max(1, tlen - max_distance), tlen + max_distance + 1):
        for i in range(0, len(text) - wlen + 1):
            if _edit_distance(tok, text[i:i + wlen], max_distance) <= max_distance:
                return True
    return False


def token_idf(tok, chunks):
    """여러 섹션에 흔하게 등장하는 단어(예: '처리')일수록 가중치를 낮춘다."""
    df = sum(1 for c in chunks if tok in c)
    return math.log((len(chunks) + 1) / (df + 1)) + 1


def _no_space(text):
    return re.sub(r"\s+", "", text)


def score_chunk(question_tokens, idf, header, chunk_text):
    score = 0
    header_ns = _no_space(header)
    chunk_ns = _no_space(chunk_text)
    for tok in question_tokens:
        base_weight = 3 if re.search(r"[0-9_]", tok) else 1
        weight = base_weight * idf[tok]
        count = chunk_text.count(tok)
        if not count and len(tok) >= 4:
            # 띄어쓰기 없이 붙여 쓴 질문(예: "회의실예약방법")도 매칭되도록 공백 제거 후 재확인
            count = 1 if tok in chunk_ns else 0
        if not count and fuzzy_contains(tok, chunk_text):
            # 오타(예: "회읭실")로 정확히 일치하는 곳이 없어도, 섹션 후보 자체에서
            # 제외되지 않도록 여기서도 편집거리 기반 매칭을 적용한다.
            # (기존에는 extract_snippets 단계에만 있어서, 상위 섹션 후보를 고르는
            #  이 단계에서 이미 점수가 0이 되면 오타 질문은 아예 "찾지 못함"으로 끝났다.)
            count = 1
        if count:
            score += weight * count
            header_match = tok in header or (len(tok) >= 4 and tok in header_ns)
            if not header_match and fuzzy_contains(tok, header_ns):
                # 오타가 섹션 제목 자체와 거의 일치하는 경우(예: "원촌세" vs "원천세")는
                # 본문에 그 표현이 몇 번 나오는지와 무관하게 그 섹션이 맞다는 강한 신호이므로,
                # 본문 어딘가에만 흔한 다른 단어가 많이 나오는 엉뚱한 섹션에 밀리지 않게 한다.
                header_match = True
            if header_match:
                score += weight * 5
    return score


def score_line(question_tokens, idf, line):
    score = 0
    for tok in question_tokens:
        weight = (3 if re.search(r"[0-9_]", tok) else 1) * idf.get(tok, 1)
        count = line.count(tok)
        if not count and fuzzy_contains(tok, line):
            count = 1
        if count:
            score += weight * count
    return score


def find_table_header(lines, idx):
    """idx번째 줄이 표의 데이터 행이면, 바로 위 헤더 행을 찾아 함께 반환한다."""
    for j in range(idx - 1, -1, -1):
        if re.match(r"^\|?[\s:-]+\|", lines[j]):
            return lines[j - 1] if j - 1 >= 0 else None
        if not lines[j].strip().startswith("|"):
            return None
    return None


def extract_snippets(question_tokens, idf, sections, max_lines=5):
    candidates = []
    for section in sections:
        header = section.split("\n", 1)[0].lstrip("# ").strip()
        lines = section.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            s = score_line(question_tokens, idf, line)
            if s <= 0:
                continue
            table_header = None
            if stripped.startswith("|"):
                table_header = find_table_header(lines, i)
            candidates.append((s, header, table_header, stripped))

    candidates.sort(key=lambda x: -x[0])
    top = candidates[:max_lines]

    seen_headers = []
    grouped = {}
    for _, header, table_header, line in top:
        if header not in grouped:
            grouped[header] = []
            seen_headers.append(header)
        if table_header and table_header not in grouped[header]:
            grouped[header].append(table_header)
        grouped[header].append(line)

    # seen_headers is already in best-score-first order (top is score-sorted,
    # and each header is recorded the first time it's encountered), so the
    # most relevant section's snippet is shown first instead of whichever
    # section happens to sit earlier in the source document.
    parts = []
    for header in seen_headers:
        body = "\n".join(grouped[header])
        parts.append(f"[{header}]\n{body}")
    return "\n\n".join(parts)


def load_chunks(path):
    text = path.read_text(encoding="utf-8")
    return split_into_chunks(text)


def is_meeting_room_question(question):
    if any(kw in question for kw in MEETING_ROOM_KEYWORDS):
        return True
    # 오타(예: "훼의실", "회읭실")로 인해 키워드가 글자 그대로 없어도 라우팅되도록 허용
    return any(fuzzy_contains(kw, question) for kw in MEETING_ROOM_KEYWORDS if len(kw) >= 3)


BOND_CHUNKS = load_chunks(BOND_FILE)
MEETING_ROOM_CHUNKS = load_chunks(MEETING_ROOM_FILE)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    question = (request.get_json(silent=True) or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "질문을 입력하세요."}), 400

    chunks = MEETING_ROOM_CHUNKS if is_meeting_room_question(question) else BOND_CHUNKS

    question_tokens = relevant_tokens(question)
    idf = {tok: token_idf(tok, chunks) for tok in set(question_tokens)}
    scored = [
        (score_chunk(question_tokens, idf, chunk.split("\n", 1)[0], chunk), chunk)
        for chunk in chunks
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    top = [c for s, c in scored if s > 0][:TOP_K]
    if not top:
        return jsonify({"answer": "관련 내용을 찾지 못했습니다.", "sections": []})

    sections = [c.split("\n", 1)[0].lstrip("# ").strip() for c in top]
    answer = extract_snippets(question_tokens, idf, top)
    if not answer:
        # 줄 단위로는 못 골랐으면(예: 매우 포괄적인 질문) 최상위 섹션 전체를 보여준다
        answer = top[0]
    return jsonify({"answer": answer, "sections": sections})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
