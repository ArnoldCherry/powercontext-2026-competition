"""不二客栈 · 记仇掌柜 —— seekdb 记账后端。

Dify Chatflow 负责理解玩家意图与生成掌柜台词；本服务负责持久化
玩家、旧事、赊账与江湖异闻，并在 seekdb 上做全文检索。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import pymysql
from fastapi import FastAPI, Header, HTTPException, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("inn-backend")

SEEKDB_HOST = os.getenv("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.getenv("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.getenv("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.getenv("SEEKDB_PASSWORD", "")
SEEKDB_DATABASE = os.getenv("SEEKDB_DATABASE", "inn")
SERVICE_TOKEN = os.getenv("INN_SERVICE_TOKEN", "").strip()
ADMIN_TOKEN = os.getenv("INN_ADMIN_TOKEN", "").strip()

if not re.fullmatch(r"[A-Za-z0-9_]+", SEEKDB_DATABASE):
    raise ValueError("SEEKDB_DATABASE must be a simple identifier")


def _is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().upper()
    return not normalized or any(
        marker in normalized
        for marker in ("CHANGE_ME", "CHANGEME", "CHANGE-ME", "PLACEHOLDER", "INN-LOCAL-ADMIN")
    )


def _require_startup_secret(name: str, value: str) -> None:
    if _is_placeholder_secret(value):
        raise RuntimeError(f"{name} must be a non-placeholder secret; set it in docker/.env and Dify app variables")


_require_startup_secret("SEEKDB_PASSWORD", SEEKDB_PASSWORD)
_require_startup_secret("INN_SERVICE_TOKEN", SERVICE_TOKEN)
_require_startup_secret("INN_ADMIN_TOKEN", ADMIN_TOKEN)

TIERS = (
    (60, "生死之交"),
    (25, "座上宾"),
    (5, "熟客"),
    (-15, "生客"),
    (-40, "不受欢迎"),
    (-101, "仇人"),
)

ACTION_DELTAS = {
    "请安": 1,
    "点菜": 2,
    "请客": 6,
    "吹牛": 2,
    "叙旧": 3,
    "打听": 2,
    "赊账": -5,
    "还账": 8,
    "捣乱": -10,
    "威胁": -15,
    "偷窃": -18,
}

HOSTILE_ACTIONS = {"捣乱", "威胁", "偷窃"}

TONE_BY_TIER = {
    "生死之交": "热络、豪爽，主动送酒送菜，像老朋友一样揭短",
    "座上宾": "客气里带熟稔，愿意透露一点后院秘密",
    "熟客": "嘴上嫌弃，实际上照顾，会主动翻旧账打趣",
    "生客": "客气但疏离，先打量再说话",
    "不受欢迎": "阴阳怪气、句句带刺，但还肯做生意",
    "仇人": "冷脸、敲打、翻旧账，随时准备赶人，却留一线",
}

LORE_ROWS: tuple[tuple[Any, ...], ...] = (
    (1, "断肠面", "招牌是一碗断肠面：不放葱，汤头用三条鲫鱼熬一夜。据说吃完会想起一个不该想的人。", "菜", "面 断肠 招牌 吃", -100),
    (2, "桂花酿", "后院老槐树下埋着三年前的桂花酿。掌柜说只给懂酒的人喝，其实是谁都舍不得给。", "酒", "桂花 酒 酿 后院 槐树", -100),
    (3, "青花瓷碗", "柜台上那只青花瓷碗是掌柜的命根子。摔了要赔，赊账也要赔，笑一声更要赔。", "器物", "瓷碗 青花 摔 赔", -100),
    (4, "后院老槐树", "老槐树比客栈还老。客人喜欢在树下许愿，掌柜喜欢在树下偷听。", "地方", "槐树 后院 许愿 树", -100),
    (5, "地窖", "地窖里藏着十八年女儿红，只有座上宾才摸得到钥匙。掌柜说钥匙不在身上，在心上。", "秘密", "地窖 钥匙 女儿红 藏酒", 40),
    (6, "燕十三", "每年霜降，一个叫燕十三的剑客会来吃一碗断肠面。他从不带剑，也从不笑。", "人物", "燕十三 剑客 剑 霜降", 0),
    (7, "招财", "后厨那只橘猫叫招财。掌柜嘴上说它偷鱼，实际上每天留半条小鱼干。", "动物", "猫 招财 鱼干 后厨", -100),
    (8, "阿绣", "掌柜年轻时有个相好的叫阿绣，最爱桂花酿。后来阿绣走了，桂花酿就再没开封。", "人物", "阿绣 老板娘 桂花 旧事", 20),
    (9, "灶王爷", "灶台第三块砖下压着一张黄纸，写着今日暗号。想知道，得先问掌柜。", "神", "灶王 暗号 口令 灶台 黄纸", -100),
    (10, "江湖悬赏", "城里贴了悬赏：采花贼「一阵风」昨夜又作案。掌柜说，最近住店的多留个心眼。", "事件", "悬赏 采花贼 一阵风 官府", -100),
    (11, "陈年赊账簿", "掌柜有本赊账簿，谁欠几文、欠了多久、当时说了什么，全记着。他说这不是记仇，是记性太好。", "事件", "赊账 账本 还钱 欠", -100),
    (12, "断剑听雨", "三十年前「听雨」剑断在客栈门口。掌柜把断剑收在柜台下，说等一个故人来取。", "器物", "断剑 听雨 剑冢 故人", 30),
    (13, "客栈规矩", "不二客栈三条规矩：不许打架，不许赊账，不许问掌柜的年纪。第三条最要紧。", "规矩", "规矩 打架 赊账 年纪", -100),
    (14, "女儿红", "地窖最里头那坛女儿红，是掌柜给未来女儿埋的。后来没等到女儿，酒也一直没开。", "秘密", "女儿红 地窖 酒 女儿", 60),
)

DAILY_PHRASES = (
    ("一碗阳春面不要面", "灶王爷说：面要阳春，人要多情。"),
    ("桂花酿里加三颗盐", "灶王爷说：甜里带咸，才是人间。"),
    ("今夜子时后门留灯", "灶王爷说：灯亮着，人就还没走远。"),
    ("赊账可以良心不行", "灶王爷说：账能还，良心还不了。"),
    ("槐树底下埋一坛酒", "灶王爷说：树记得比人久。"),
    ("招财今天吃小鱼干", "灶王爷说：猫比人诚实。"),
    ("断肠面里不放葱", "灶王爷说：有些人，不吃葱。"),
)


def connect(database: str | None = None) -> pymysql.Connection:
    return pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def init_database() -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            server = connect()
            try:
                with server.cursor() as cur:
                    cur.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{SEEKDB_DATABASE}` "
                        "DEFAULT CHARACTER SET utf8mb4"
                    )
                server.commit()
            finally:
                server.close()
            conn = connect(SEEKDB_DATABASE)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_players (
                          player_id VARCHAR(128) NOT NULL PRIMARY KEY,
                          name VARCHAR(64) NOT NULL,
                          reputation INT NOT NULL DEFAULT 0,
                          debt INT NOT NULL DEFAULT 0,
                          visits INT NOT NULL DEFAULT 0,
                          rounds INT NOT NULL DEFAULT 0,
                          last_conversation_id VARCHAR(128) NOT NULL DEFAULT '',
                          created_at DATETIME NOT NULL,
                          last_seen DATETIME NOT NULL
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_events (
                          id BIGINT AUTO_INCREMENT PRIMARY KEY,
                          player_id VARCHAR(128) NOT NULL,
                          conversation_id VARCHAR(128) NOT NULL DEFAULT '',
                          visit_no INT NOT NULL,
                          round_no INT NOT NULL,
                          action VARCHAR(32) NOT NULL,
                          utterance VARCHAR(1000) NOT NULL,
                          delta INT NOT NULL,
                          created_at DATETIME NOT NULL,
                          KEY idx_inn_events_player (player_id, id)
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_lore (
                          id INT NOT NULL PRIMARY KEY,
                          title VARCHAR(128) NOT NULL,
                          body TEXT NOT NULL,
                          kind VARCHAR(32) NOT NULL,
                          keywords VARCHAR(255) NOT NULL,
                          min_reputation INT NOT NULL DEFAULT -100,
                          FULLTEXT INDEX ft_inn_lore (title, body, keywords)
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_daily (
                          code_date DATE NOT NULL PRIMARY KEY,
                          passphrase VARCHAR(64) NOT NULL,
                          hint VARCHAR(255) NOT NULL
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_secrets (
                          player_id VARCHAR(128) NOT NULL,
                          code_date DATE NOT NULL,
                          PRIMARY KEY (player_id, code_date)
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS inn_requests (
                          player_id VARCHAR(128) NOT NULL,
                          request_id VARCHAR(128) NOT NULL,
                          response_json MEDIUMTEXT NOT NULL,
                          created_at DATETIME NOT NULL,
                          PRIMARY KEY (player_id, request_id)
                        ) DEFAULT CHARSET=utf8mb4
                        """
                    )
                    for row in LORE_ROWS:
                        cur.execute(
                            "INSERT IGNORE INTO inn_lore "
                            "(id, title, body, kind, keywords, min_reputation) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            row,
                        )
                    phrase, hint = daily_phrase(date.today())
                    cur.execute(
                        "INSERT IGNORE INTO inn_daily (code_date, passphrase, hint) VALUES (%s, %s, %s)",
                        (date.today(), phrase, hint),
                    )
                conn.commit()
            finally:
                conn.close()
            logger.info("seekdb ready at %s:%s, database=%s", SEEKDB_HOST, SEEKDB_PORT, SEEKDB_DATABASE)
            return
        except pymysql.MySQLError as error:
            last_error = error
            logger.warning("waiting for seekdb: %s", error)
            time.sleep(2)
    raise RuntimeError(f"seekdb is not ready: {last_error}")


def daily_phrase(day: date) -> tuple[str, str]:
    digest = hashlib.sha256(day.isoformat().encode("utf-8")).hexdigest()
    return DAILY_PHRASES[int(digest, 16) % len(DAILY_PHRASES)]


def tier_for(reputation: int) -> str:
    for threshold, name in TIERS:
        if reputation >= threshold:
            return name
    return TIERS[-1][1]


def normalize_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    return value.strip()[:limit]


def classify_action(action: str | None, utterance: str) -> str:
    candidate = normalize_text(action, 32).replace(" ", "")
    if candidate in ACTION_DELTAS:
        return candidate
    rules = (
        ("还账", ("还钱", "还账", "结账", "把账结了", "欠款还")),
        ("赊账", ("赊账", "记账", "先欠", "欠着", "挂账")),
        ("偷窃", ("偷", "顺走", "摸走", "拿走")),
        ("威胁", ("威胁", "弄死", "砸店", "杀了", "滚出去")),
        ("捣乱", ("砸", "掀桌", "骂", "闹事", "打架", "捣乱")),
        ("请客", ("请客", "打赏", "赏钱", "我请")),
        ("点菜", ("点菜", "来一", "上酒", "来碗", "吃什么", "菜单")),
        ("打听", ("打听", "听说", "传闻", "暗号", "消息", "问一下")),
        ("吹牛", ("吹牛", "想当年", "厉害", "佩服", "我当年")),
        ("请安", ("请安", "问好", "掌柜好", "早上好")),
    )
    for name, words in rules:
        if any(word in utterance for word in words):
            return name
    return "叙旧"


def extract_name(player_name: str | None, utterance: str) -> str:
    candidate = normalize_text(player_name, 32)
    if candidate and candidate not in {"无", "none", "null", "无名"}:
        return candidate
    match = re.search(r"(?:我叫|叫我|江湖人称|名号是|名号叫)([^\s，。！？、,.!?]{1,8})", utterance)
    if match:
        return match.group(1)
    return ""


def parse_signals(raw: str | None) -> tuple[str, str, str]:
    text = normalize_text(raw, 600)
    if not text:
        return "", "", ""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return "", "", ""
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return "", "", ""
    if not isinstance(value, dict):
        return "", "", ""

    def as_text(item: object) -> str:
        if isinstance(item, list):
            return " ".join(str(part) for part in item)
        return str(item or "")

    return as_text(value.get("action")), as_text(value.get("keywords")), as_text(value.get("player_name"))


def split_terms(keywords: str | None, utterance: str) -> list[str]:
    terms: list[str] = []
    for chunk in re.split(r"[\s,，、;；/]+", normalize_text(keywords, 200)):
        chunk = chunk.strip()
        if len(chunk) >= 2 and chunk not in {"无", "没有", "none", "null"} and chunk not in terms:
            terms.append(chunk)
    if not terms:
        for row in LORE_ROWS:
            for token in str(row[4]).split():
                if token in utterance and token not in terms:
                    terms.append(token)
    return terms[:4]


def retrieve_lore(cur, terms: list[str], reputation: int) -> tuple[list[dict[str, Any]], str]:
    if not terms:
        return [], "none"
    query = " ".join(terms)
    rows: list[dict[str, Any]] = []
    mode = "none"
    try:
        cur.execute(
            "SELECT id, title, body, kind, keywords, min_reputation, "
            "MATCH(title, body, keywords) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score "
            "FROM inn_lore WHERE min_reputation <= %s "
            "AND MATCH(title, body, keywords) AGAINST (%s IN NATURAL LANGUAGE MODE) "
            "ORDER BY score DESC LIMIT 4",
            (query, reputation, query),
        )
        rows = list(cur.fetchall())
        if rows:
            mode = "fulltext"
    except pymysql.MySQLError as error:
        logger.warning("fulltext query failed, falling back to LIKE: %s", error)
        rows = []
    if not rows:
        cur.execute(
            "SELECT id, title, body, kind, keywords, min_reputation, 0 AS score "
            "FROM inn_lore WHERE min_reputation <= %s ORDER BY id",
            (reputation,),
        )
        all_rows = list(cur.fetchall())
        for row in all_rows:
            haystack = f"{row['title']} {row['body']} {row['keywords']}"
            if any(term in haystack for term in terms):
                rows.append(row)
        rows = rows[:4]
        if rows:
            mode = "like"
    return rows, mode


def recent_events(cur, player_id: str, current_conversation: str, limit: int = 4) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT id, conversation_id, visit_no, round_no, action, utterance, delta, created_at "
        "FROM inn_events WHERE player_id = %s ORDER BY id DESC LIMIT %s",
        (player_id, limit),
    )
    return list(cur.fetchall())


def format_memory(event: dict[str, Any], current_conversation: str) -> str:
    when = "上次光临" if event["conversation_id"] != current_conversation else "方才"
    text = event["utterance"].replace("\n", " ")[:60]
    delta = event["delta"]
    flavor = "结了个善缘" if delta > 0 else ("记了一笔" if delta < 0 else "没起波澜")
    return f"{when}，你「{text}」（{event['action']}，{flavor}）"


def build_director_note(
    *,
    name: str,
    tier: str,
    reputation: int,
    debt: int,
    visit_no: int,
    round_no: int,
    action: str,
    delta: int,
    memories: list[str],
    lore: list[dict[str, Any]],
    daily_hint: tuple[str, str] | None,
    secret_hit: bool,
) -> str:
    parts = [
        f"客人名号：{name}；第 {visit_no} 次光临，第 {round_no} 回合；"
        f"人情值 {reputation}，称号「{tier}」；赊账 {debt} 文。",
        f"语气要求：{TONE_BY_TIER[tier]}。",
        f"本轮行为：{action}（人情 {delta:+d}）。",
    ]
    if memories:
        parts.append("必须自然提起的旧事：" + "；".join(memories) + "。")
    if lore:
        parts.append(
            "可织入的江湖异闻："
            + "；".join(f"《{row['title']}》{row['body']}" for row in lore)
            + "。"
        )
    if daily_hint:
        parts.append(f"客人问起暗号，今日暗号是「{daily_hint[0]}」，可神秘地告诉他。")
    if secret_hit:
        parts.append("客人说对了今日暗号：给他地窖里的十八年女儿红，态度要惊喜。")
    if debt > 0:
        parts.append(f"客人还欠着 {debt} 文酒钱，末尾要催一句，但不能真翻脸。")
    parts.append("结尾抛出一个问题或动作留住客人；不要输出人情数值或称号，系统会统一标注。")
    return "\n".join(parts)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    yield


app = FastAPI(title="不二客栈账房", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    conn = connect(SEEKDB_DATABASE)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS version")
            row = cur.fetchone() or {}
    finally:
        conn.close()
    return {"ok": True, "seekdb": row.get("version"), "database": SEEKDB_DATABASE}


def require_service_token(value: str) -> None:
    if not SERVICE_TOKEN or not secrets.compare_digest(value or "", SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="service authentication required")


def require_admin_token(value: str) -> None:
    if not ADMIN_TOKEN or not secrets.compare_digest(value or "", ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="admin token required")


def require_player_id(value: str) -> str:
    player_id = normalize_text(value, 128)
    if not player_id:
        raise HTTPException(status_code=401, detail="trusted player identity required")
    return player_id


def _admin_token_ok(value: str) -> bool:
    return bool(ADMIN_TOKEN) and bool(value) and secrets.compare_digest(value, ADMIN_TOKEN)


def _form_text(form, key: str, limit: int, *, required: bool = False) -> str:
    value = form.get(key)
    if value is None:
        if required:
            raise HTTPException(status_code=422, detail=f"{key} is required")
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{key} must be text")
    if len(value) > limit:
        raise HTTPException(status_code=422, detail=f"{key} is too long")
    return value


@app.get("/api/daily")
def daily(x_inn_service_token: str = Header(default="")) -> dict[str, Any]:
    require_service_token(x_inn_service_token)
    phrase, hint = daily_phrase(date.today())
    return {"date": date.today().isoformat(), "passphrase": phrase, "hint": hint}


@app.get("/api/ledger/{player_id}")
def ledger(
    player_id: str,
    x_inn_service_token: str = Header(default=""),
    x_inn_player_id: str = Header(default=""),
    x_inn_admin_token: str = Header(default=""),
) -> dict[str, Any]:
    require_service_token(x_inn_service_token)
    if not _admin_token_ok(x_inn_admin_token) and not (
        x_inn_player_id and secrets.compare_digest(x_inn_player_id, player_id)
    ):
        raise HTTPException(status_code=403, detail="ledger access requires ownership or admin token")
    conn = connect(SEEKDB_DATABASE)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM inn_players WHERE player_id = %s", (player_id,))
            player = cur.fetchone()
            if player is None:
                raise HTTPException(status_code=404, detail="player not found")
            cur.execute(
                "SELECT id, visit_no, round_no, action, utterance, delta, created_at "
                "FROM inn_events WHERE player_id = %s ORDER BY id DESC LIMIT 10",
                (player_id,),
            )
            events = list(cur.fetchall())
    finally:
        conn.close()
    return {"player": player, "events": events}


@app.post("/api/players/{player_id}/reset")
def reset_player(player_id: str, x_admin_token: str = Header(default="")) -> dict[str, Any]:
    require_admin_token(x_admin_token)
    conn = connect(SEEKDB_DATABASE)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM inn_requests WHERE player_id = %s", (player_id,))
            cur.execute("DELETE FROM inn_events WHERE player_id = %s", (player_id,))
            cur.execute("DELETE FROM inn_secrets WHERE player_id = %s", (player_id,))
            cur.execute("DELETE FROM inn_players WHERE player_id = %s", (player_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "reset": player_id}


@app.post("/api/visit")
async def visit(
    request: Request,
    x_inn_service_token: str = Header(default=""),
    x_inn_player_id: str = Header(default=""),
    x_inn_request_id: str = Header(default=""),
) -> dict[str, Any]:
    require_service_token(x_inn_service_token)
    player_id = require_player_id(x_inn_player_id)
    request_id = normalize_text(x_inn_request_id, 128)
    if not request_id:
        raise HTTPException(status_code=400, detail="trusted request id required")

    form = await request.form()
    if form.get("player_id") is not None:
        raise HTTPException(status_code=403, detail="player identity must come from the trusted header")
    utterance = _form_text(form, "utterance", 1000, required=True).strip()
    if not utterance:
        raise HTTPException(status_code=422, detail="utterance is required")
    conversation_id = normalize_text(_form_text(form, "conversation_id", 128), 128) or "default"
    action = _form_text(form, "action", 32)
    keywords = _form_text(form, "keywords", 200)
    player_name = _form_text(form, "player_name", 32)
    signals = _form_text(form, "signals", 600)
    signal_action, signal_keywords, signal_name = parse_signals(signals)
    action = classify_action(signal_action or action, utterance)
    name_hint = extract_name(signal_name or player_name, utterance)
    terms = split_terms(signal_keywords or keywords, utterance)
    today = date.today()
    phrase, hint = daily_phrase(today)
    secret_hit = phrase in utterance and "暗号" not in utterance
    now = datetime.now()

    conn = connect(SEEKDB_DATABASE)
    try:
        with conn.cursor() as cur:
            inserted = cur.execute(
                "INSERT IGNORE INTO inn_players "
                "(player_id, name, reputation, debt, visits, rounds, last_conversation_id, created_at, last_seen) "
                "VALUES (%s, %s, 0, 0, 1, 0, %s, %s, %s)",
                (player_id, name_hint or "无名少侠", conversation_id, now, now),
            )
            cur.execute("SELECT * FROM inn_players WHERE player_id = %s FOR UPDATE", (player_id,))
            player_row = cur.fetchone()
            if player_row is None:
                raise RuntimeError("player row disappeared during visit")
            player = dict(player_row)
            cur.execute(
                "SELECT response_json FROM inn_requests WHERE player_id = %s AND request_id = %s",
                (player_id, request_id),
            )
            replay = cur.fetchone()
            if replay is not None:
                conn.commit()
                return json.loads(replay["response_json"])

            if not inserted:
                if name_hint:
                    player["name"] = name_hint
                if player["last_conversation_id"] != conversation_id:
                    player["visits"] = player["visits"] + 1
                    player["rounds"] = 0
            visit_no = player["visits"]
            round_no = player["rounds"] + 1
            previous = recent_events(cur, player_id, conversation_id)
            is_returning = bool(previous) and any(
                event["conversation_id"] != conversation_id for event in previous
            )
            delta = ACTION_DELTAS[action]
            if action in HOSTILE_ACTIONS:
                prior_hostile = sum(1 for event in previous if event["action"] in HOSTILE_ACTIONS)
                delta = max(-25, delta * (1 + min(prior_hostile, 2)))
            debt = player["debt"]
            if action == "赊账":
                debt += 20
            elif action == "还账":
                debt = max(0, debt - 20)
            if secret_hit:
                cur.execute(
                    "SELECT 1 FROM inn_secrets WHERE player_id = %s AND code_date = %s",
                    (player_id, today),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        "INSERT INTO inn_secrets (player_id, code_date) VALUES (%s, %s)",
                        (player_id, today),
                    )
                    delta += 15
                else:
                    secret_hit = False
            reputation = max(-100, min(100, player["reputation"] + delta))
            tier = tier_for(reputation)
            cur.execute(
                "UPDATE inn_players SET name = %s, reputation = %s, debt = %s, visits = %s, rounds = %s, "
                "last_conversation_id = %s, last_seen = %s WHERE player_id = %s",
                (player["name"], reputation, debt, visit_no, round_no, conversation_id, now, player_id),
            )
            cur.execute(
                "INSERT INTO inn_events "
                "(player_id, conversation_id, visit_no, round_no, action, utterance, delta, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (player_id, conversation_id, visit_no, round_no, action, utterance, delta, now),
            )
            lore, retrieval_mode = retrieve_lore(cur, terms, reputation)
            if secret_hit:
                cur.execute("SELECT id, title, body, kind, keywords, min_reputation FROM inn_lore WHERE id = 5")
                cellar = cur.fetchone()
                if cellar and all(row["id"] != cellar["id"] for row in lore):
                    lore.append(cellar)
            memories = []
            if is_returning:
                old = [event for event in previous if event["conversation_id"] != conversation_id]
                old.sort(key=lambda event: (abs(event["delta"]), event["id"]), reverse=True)
                memories = [format_memory(event, conversation_id) for event in old[:2]]
            elif previous:
                memories = [format_memory(previous[0], conversation_id)]
            if debt > 0:
                memories.append(f"赊账簿上记着 {debt} 文未还")
            daily_hint = (phrase, hint) if any(word in utterance for word in ("暗号", "口令", "灶王")) else None
            note = build_director_note(
                name=player["name"],
                tier=tier,
                reputation=reputation,
                debt=debt,
                visit_no=visit_no,
                round_no=round_no,
                action=action,
                delta=delta,
                memories=memories,
                lore=lore,
                daily_hint=daily_hint,
                secret_hit=secret_hit,
            )
            response = {
                "ok": True,
                "player": {
                    "name": player["name"],
                    "reputation": reputation,
                    "tier": tier,
                    "visit_no": visit_no,
                    "round": round_no,
                    "debt": debt,
                },
                "visit": {
                    "is_returning": is_returning,
                    "is_first": visit_no == 1 and round_no == 1,
                    "action": action,
                    "delta": delta,
                },
                "memories": memories,
                "lore": [
                    {"title": row["title"], "body": row["body"], "kind": row["kind"]}
                    for row in lore
                ],
                "daily_hint": daily_hint[0] if daily_hint else None,
                "secret_hit": secret_hit,
                "retrieval": retrieval_mode,
                "director_note": note,
                "status_line": f"【人情 {reputation} · {tier}】",
            }
            cur.execute(
                "INSERT INTO inn_requests (player_id, request_id, response_json, created_at) VALUES (%s, %s, %s, %s)",
                (player_id, request_id, json.dumps(response, ensure_ascii=False), now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "visit player=%s request=%s action=%s delta=%d rep=%d tier=%s terms=%s retrieval=%s lore=%s secret=%s",
        player_id,
        request_id,
        action,
        delta,
        reputation,
        tier,
        terms,
        retrieval_mode,
        [row["title"] for row in lore],
        secret_hit,
    )
    return response
