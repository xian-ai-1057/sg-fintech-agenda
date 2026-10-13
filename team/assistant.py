"""共編頁的議程小幫手。兩種回答來源，與原本行事曆的對話框相同：

1. 關鍵字推薦 recommend()：純本機計分，不需要網路或 API key。
   計分規則對應 index.html 的 scoreSession()，差兩點：
   - 主題同義詞表是 2025 年的主題代碼，對 2026 年的資料不會命中，沒有搬過來。
   - 「AI」這類兩個字母的詞以整個單字比對（index.html 會直接忽略，查不到東西）。
2. AI 模式 ask_agent()：把問題轉給議程 Agent（agent/api.py 的 POST /ask）。
   由共編伺服器代為轉送，所以 Agent 可以只綁本機，API key 也不會進到瀏覽器。

只使用 Python 標準函式庫。
"""
from __future__ import annotations
import json
import re
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_AGENT_URL = 'http://127.0.0.1:8765'
ASK_TIMEOUT = 120      # 秒；Agent 會呼叫 LLM，可能要等一陣子
HEALTH_TIMEOUT = 4

# 泛詞：不參與標題／說明計分，避免「fintech」這類字讓所有場次都上榜
STOP_WORDS = {'finance', 'financial', 'session', 'sessions', 'singapore', 'fintech', 'future',
              'digital', 'global', 'world', 'the', 'and', 'for', 'with', '金融', '未來', '場次', '活動'}

# 主題中文名，讓「人才」「政策」這類中文查詢也能對到主題
TRACK_ZH = {'Technology Rewiring': '科技重塑', 'Technological Rewiring': '科技重塑',
            'Policy Rewiring': '政策重塑', 'Talent Rewiring': '人才重塑',
            'Geopolitical Rewiring': '地緣政治重塑', 'Geoeconomic Rewiring': '地緣經濟重塑',
            'Capital Rewiring': '資本重塑', 'Others': '其他'}

_CJK = re.compile(r'[㐀-鿿]')
# 直連，不走環境變數裡的 HTTP proxy：Agent 通常在本機或同一個內網
_opener = build_opener(ProxyHandler({}))


class AgentError(Exception):
    """Agent 連不上或回了無法使用的內容。"""


# ---------------------------------------------------------------- 關鍵字推薦

def _hits(words, text, skip_stop=True):
    """text 裡出現幾個查詢詞。三個字母以上用子字串；兩個字母（AI、ML）要是完整單字。"""
    count = 0
    for w in words:
        if skip_stop and w in STOP_WORDS:
            continue
        if len(w) >= 3 and w in text:
            count += 1
        elif len(w) == 2 and re.search(r'\b' + re.escape(w) + r'\b', text):
            count += 1
    return count


def _score(session, raw, words):
    """單一場次的分數；0 代表不相關。words 是查詢裡的英數詞。"""
    has_cjk = bool(_CJK.search(raw)) and raw not in STOP_WORDS
    score = 0

    track = session.get('track', '').lower()
    track_zh = TRACK_ZH.get(session.get('track', ''), '')
    if track and track != 'others':
        if _hits(words, track, skip_stop=False) or (has_cjk and track_zh and (raw in track_zh or track_zh in raw)):
            score += 20

    title = session.get('title', '').lower()
    score += min(36, 12 * _hits(words, title))
    if has_cjk and raw in title:
        score += 12

    desc = (session.get('description', '') + '\n' + session.get('description_zh', '')).lower()
    score += min(20, 5 * _hits(words, desc))
    if has_cjk and raw in desc:
        score += 5

    speakers = session.get('speakers', '').lower()
    speaker_hits = _hits(words, speakers, skip_stop=False)
    if _CJK.search(raw) and raw in speakers:
        speaker_hits += 1
    score += min(24, 8 * speaker_hits)

    if score and session.get('access') == 'Open':
        score += 1  # 同分時讓免費場次排前面
    return score


def recommend(sessions, query, limit=6):
    """回傳最相關的前 limit 場（session dict），依分數、日期、開始時間排序。"""
    raw = query.lower().strip()
    if not raw:
        return []
    words = sorted({w for w in re.split(r'[^a-z0-9]+', raw) if len(w) >= 2})
    scored = [(_score(s, raw, words), s) for s in sessions]
    scored = [(sc, s) for sc, s in scored if sc > 0]
    scored.sort(key=lambda x: (-x[0], x[1]['date'], x[1]['start']))
    return [s for _, s in scored[:limit]]


# ---------------------------------------------------------------- AI 模式

def agent_ok(agent_url):
    """Agent 的 /health 有回應就算可用。"""
    try:
        with _opener.open(agent_url + '/health', timeout=HEALTH_TIMEOUT) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


def ask_agent(agent_url, question, thread_id):
    """把問題轉給 Agent，回傳純文字答案。失敗一律丟 AgentError。"""
    body = json.dumps({'question': question, 'thread_id': thread_id}).encode()
    request = Request(agent_url + '/ask', data=body, headers={'Content-Type': 'application/json'})
    try:
        with _opener.open(request, timeout=ASK_TIMEOUT) as response:
            answer = str(json.load(response).get('answer', '')).strip()
    except (URLError, OSError, ValueError, AttributeError) as exc:
        raise AgentError(str(exc)) from exc
    if not answer:
        raise AgentError('empty answer')
    return answer
