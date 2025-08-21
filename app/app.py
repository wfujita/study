from flask import Flask, request, send_from_directory, jsonify
from datetime import datetime, timezone
import os, json

app = Flask(__name__, static_folder="static", static_url_path="")

# ===== 静的ページ =====
@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/admin")
def admin_page():
    # キャンバスの「Admin」のHTMLを static/admin.html として保存してください
    return send_from_directory("static", "admin.html")

# 出題ファイル（フロントは /data/questions.json を参照）
@app.get("/data/questions.json")
def get_questions():
    return send_from_directory("data", "questions.json")


# ===== 受信（結果保存） =====
@app.post("/api/results")
def save_results():
    # フロント（index.html）から送られる最小ペイロードをそのまま受領
    rec = request.get_json(force=True, silent=True) or {}
    # Python 3.11+ での警告回避：タイムゾーン付きUTC
    rec["receivedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", "results.ndjson")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return jsonify({"ok": True}), 201


# ====== 管理ダッシュボード用ユーティリティ ======
def load_questions_map():
    """
    questions.json を読み、id -> {jp,en,unit,...} の辞書にまとめる。
    並べ替え（questions）と単語（vocab）の両方をサポート。
    """
    qmap = {}
    path = os.path.join("data", "questions.json")
    if not os.path.exists(path):
        return qmap
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return qmap

    for q in data.get("questions", []) or []:
        qid = q.get("id")
        if qid:
            qmap[qid] = {
                "id": qid, "jp": q.get("jp"), "en": q.get("en"),
                "unit": q.get("unit"), "type": "reorder"
            }
    for v in data.get("vocab", []) or []:
        qid = v.get("id")
        if qid:
            qmap[qid] = {
                "id": qid, "jp": v.get("jp"), "en": v.get("en"),
                "unit": v.get("unit"), "type": "vocab"
            }
    return qmap


def iter_results():
    """
    保存済みの results.ndjson を配列で返す（1行=1セッション）。
    """
    path = os.path.join("data", "results.ndjson")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                # 壊れた行はスキップ
                continue
    return out


# ====== /admin 用 API ======
@app.get("/api/admin/users")
def admin_users():
    """
    ユーザ一覧＋学習回数などのサマリ。
    Admin（キャンバスのHTML）がこのエンドポイントを参照します。
    """
    res = iter_results()
    users = {}
    for r in res:
        u = (r.get("user") or "guest")
        users.setdefault(u, {"user": u, "sessions": 0, "lastAt": None, "answered": 0, "correct": 0})
        users[u]["sessions"] += 1
        # lastAt は endedAt 優先、なければ receivedAt
        last = r.get("endedAt") or r.get("receivedAt") or ""
        users[u]["lastAt"] = max(users[u]["lastAt"] or "", last)
        # answered 数は answered配列の長さを優先、無ければ total
        ans = r.get("answered") or []
        users[u]["answered"] += (len(ans) if isinstance(ans, list) else (r.get("total") or 0))
        users[u]["correct"]  += (r.get("correct") or sum(1 for a in ans if a.get("correct")))
    out = sorted(users.values(), key=lambda x: (x["lastAt"] or ""), reverse=True)
    return jsonify(out)


@app.get("/api/admin/summary")
def admin_summary():
    """
    ダッシュボード本体。
    ?user=__all__|<name>&unit=<unit or ''>&q=<free text>
    """
    user = request.args.get("user")  # "__all__" で全体
    unit = request.args.get("unit") or ""
    qtext = (request.args.get("q") or "").lower()

    qmap = load_questions_map()
    res = iter_results()

    def match_user(r):
        return (user in (None, "", "__all__")) or (r.get("user", "guest") == user)

    answered_all = []
    sessions = []
    for r in res:
        if not match_user(r):
            continue
        ans = (r.get("answered") or [])
        sessions.append({
            "user": r.get("user","guest"),
            "endedAt": r.get("endedAt"),
            "total": r.get("total", len(ans)),
            "correct": r.get("correct", sum(1 for a in ans if a.get("correct"))),
            "accuracy": r.get("accuracy"),
            "mode": r.get("mode") or "normal",
            "qType": r.get("qType"),        # 並べ替え/単語
            "setIndex": r.get("setIndex"),
            "seconds": r.get("seconds", 0)
        })
        for a in ans:
            qid = a.get("id")
            qm  = qmap.get(qid, {})  # idが無い/見つからない場合もある
            item = {
                "user": r.get("user","guest"),
                "id": qid,
                "unit": (a.get("unit") or qm.get("unit") or ""),
                "jp": qm.get("jp"),               # 送信は最小ペイロードなので qmap から復元
                "en": qm.get("en"),
                "type": (a.get("type") or qm.get("type") or ""),
                "correct": bool(a.get("correct")),
                "userAnswer": a.get("userAnswer"),
                "at": a.get("at") or r.get("endedAt") or r.get("receivedAt")
            }
            # 単元フィルタ
            if unit and item["unit"] != unit:
                continue
            # フリーテキスト検索
            if qtext:
                hay = " ".join(str(x or "") for x in [item["id"], item["jp"], item["en"], item["userAnswer"]]).lower()
                if qtext not in hay:
                    continue
            answered_all.append(item)

    totals = {
        "sessions": len(sessions),
        "answered": len(answered_all),
        "correct": sum(1 for a in answered_all if a["correct"]),
    }

    # 単元別集計
    by_unit = {}
    for a in answered_all:
        u = a.get("unit") or ""
        d = by_unit.setdefault(u, {"unit": u, "answered": 0, "correct": 0, "wrong": 0})
        d["answered"] += 1
        if a["correct"]:
            d["correct"] += 1
        else:
            d["wrong"] += 1
    by_unit_arr = sorted(by_unit.values(), key=lambda x: (-x["answered"], x["unit"]))

    # よく間違える問題（wrong降順、同数ならanswered降順）
    by_q = {}
    for a in answered_all:
        qid = a.get("id") or "(no-id)"
        d = by_q.setdefault(qid, {
            "id": qid, "unit": a.get("unit"), "jp": a.get("jp"), "en": a.get("en"),
            "answered": 0, "wrong": 0, "lastAt": None
        })
        d["answered"] += 1
        if not a["correct"]:
            d["wrong"] += 1
        d["lastAt"] = max(d["lastAt"] or "", a.get("at") or "")
    top_missed = sorted(by_q.values(), key=lambda x: (x["wrong"], x["answered"]), reverse=True)

    # 最近の解答（最大100件）
    recent = sorted(answered_all, key=lambda x: x.get("at") or "", reverse=True)[:100]

    return jsonify({
        "totals": totals,
        "byUnit": by_unit_arr,
        "topMissed": top_missed,
        "recentAnswers": recent,
        "sessions": sorted(sessions, key=lambda x: x["endedAt"] or "", reverse=True)[:100]
    })


# （任意）Chrome DevTools が叩くURLを空レスで静かにする
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def devtools_stub():
    return jsonify({}), 200


if __name__ == "__main__":
    # Windowsなら py app.py / Linux,Macなら python app.py
    app.run(host="0.0.0.0",port=80, debug=True)
