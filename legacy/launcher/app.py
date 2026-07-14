"""
Automate-X 통합 테스트 런처.

manual(Flask)/nl2sql(순수 http.server)/rpa(Streamlit) 세 앱은 각자 다른 프레임워크와
포트에서 독립적으로 실행된다. 이 런처는 그 세 앱을 대체하거나 합치지 않고, 채팅 입력창에
1/2/3(또는 이름)을 입력하면 해당 앱을 필요시 백그라운드로 기동한 뒤 그 URL을 iframe으로
보여주는 얇은 진입점이다. PRD의 "하나의 채팅/대시보드 인터페이스" 최종 형태가 아니라,
지금 단계에서 세 모듈을 하나씩 순서대로 테스트하기 위한 임시 도구다.
"""

import socket
import subprocess
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent.parent

MODULES = {
    "1": {
        "name": "1. AI 지식정보 저장소 (manual)",
        "desc": "채권/회의실 매뉴얼 Q&A (Flask, 키워드 검색)",
        "cwd": ROOT / "manual",
        "cmd": [sys.executable, "app.py"],
        "port": 5000,
    },
    "2": {
        "name": "2. NL2SQL 데이터 추출 (nl2sql)",
        "desc": "자연어 질문 → SQL 생성/검증/실행 (LangGraph)",
        "cwd": ROOT / "nl2sql",
        "cmd": [sys.executable, "-m", "im_one_agent.web"],
        "port": 8765,
        # nl2sql의 CSP frame-ancestors는 이 런처의 출처(http://127.0.0.1:7000)만 명시적으로
        # 허용하도록 완화되어 있다 (nl2sql/src/im_one_agent/web.py의 ALLOWED_FRAME_ANCESTOR).
        # 다른 출처에서의 임베딩은 여전히 차단된다.
    },
    "3": {
        "name": "3. 지출품의 자동화 (rpa)",
        "desc": "가상 카드결제 감지 → 지출품의 초안/승인 (Streamlit)",
        "cwd": ROOT / "rpa",
        "cmd": [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.port", "8501", "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        "port": 8501,
    },
}

ALIASES = {
    "1": "1", "매뉴얼": "1", "지식": "1", "manual": "1", "faq": "1",
    "2": "2", "nl2sql": "2", "데이터": "2", "sql": "2",
    "3": "3", "rpa": "3", "지출": "3", "품의": "3", "스케줄": "3",
}

_processes = {}
_lock = threading.Lock()

app = Flask(__name__)


def is_port_open(port, host="127.0.0.1", timeout=0.3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def ensure_started(key):
    mod = MODULES[key]
    with _lock:
        if is_port_open(mod["port"]):
            return "already-running"
        proc = _processes.get(key)
        if proc and proc.poll() is None:
            return "starting"
        _processes[key] = subprocess.Popen(
            mod["cmd"],
            cwd=str(mod["cwd"]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return "starting"


@app.route("/")
def index():
    return render_template("index.html", modules=MODULES)


@app.route("/api/select", methods=["POST"])
def select():
    text = (request.get_json(silent=True) or {}).get("text", "").strip().lower()
    key = ALIASES.get(text)
    if not key:
        return jsonify({"error": f"'{text}'는 인식할 수 없어요. 1, 2, 3 중 하나를 입력해주세요."}), 400
    state = ensure_started(key)
    mod = MODULES[key]
    return jsonify({
        "key": key,
        "name": mod["name"],
        "desc": mod["desc"],
        "port": mod["port"],
        "state": state,
        "embeddable": mod.get("embeddable", True),
    })


@app.route("/api/status/<key>")
def status(key):
    mod = MODULES.get(key)
    if not mod:
        return jsonify({"error": "unknown module"}), 404
    return jsonify({"ready": is_port_open(mod["port"]), "port": mod["port"]})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7000, debug=False)
