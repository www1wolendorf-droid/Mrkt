"""Сервер для сайта «Звёзды от Джексана».

Запуск:
    pip install -r requirements.txt
    ADMIN_KEY=мойпароль python server.py
Сайт:   http://localhost:8000
Админка: http://localhost:8000/admin?key=мойпароль
"""
import hashlib
import json
import os
import threading
import time
import urllib.request

from flask import Flask, jsonify, request, send_from_directory, abort, redirect

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "orders.json")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "change-me")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")          # токен от @BotFather (только в переменных окружения!)
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # ваш Telegram ID (бот пришлёт его по /start)
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
HOOK = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:24] if BOT_TOKEN else ""
TOTAL_STOCK = 18_000_000
MIN_ORDER = 250_000
PRICE_PER_MILLION = 300  # рублей за 1 000 000 звёзд

app = Flask(__name__, static_folder="static")
lock = threading.Lock()


def load():
    if not os.path.exists(DB):
        return []
    with open(DB, encoding="utf-8") as f:
        return json.load(f)


def save(orders):
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def stock_left(orders):
    sold = sum(o["stars"] for o in orders if o["status"] == "paid")
    return TOTAL_STOCK - sold


def tg(method, payload):
    if not BOT_TOKEN:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        print("telegram error:", e)
        return None


def order_text(o):
    return (f"Заказ №{o['id']}\nНик: {o['nick']}\nЗвёзд: {o['stars']:,}\n"
            f"Сумма: {o['price']} ₽\nСтатус: {o['status']}")


def notify(order):
    if ADMIN_CHAT_ID:
        tg("sendMessage", {
            "chat_id": ADMIN_CHAT_ID,
            "text": order_text(order),
            "reply_markup": {"inline_keyboard": [[
                {"text": "Оплачен", "callback_data": f"paid:{order['id']}"}]]},
        })


def mark_paid(oid):
    with lock:
        orders = load()
        found = None
        for o in orders:
            if o["id"] == oid:
                o["status"] = "paid"
                found = o
        save(orders)
    return found


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/api/stock")
def api_stock():
    with lock:
        return jsonify(stock=stock_left(load()))


@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(silent=True) or {}
    nick = str(data.get("nick", "")).strip()[:64]
    try:
        stars = int(data.get("stars", 0))
    except (TypeError, ValueError):
        return jsonify(error="bad stars"), 400
    if not nick:
        return jsonify(error="nick required"), 400
    if stars < MIN_ORDER:
        return jsonify(error="min 250000"), 400
    with lock:
        orders = load()
        if stars > stock_left(orders):
            return jsonify(error="not enough stock"), 400
        order = {
            "id": len(orders) + 1,
            "nick": nick,
            "stars": stars,
            "price": round(stars * PRICE_PER_MILLION / 1_000_000, 2),
            "status": "new",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        orders.append(order)
        save(orders)
    notify(order)
    return jsonify(ok=True, order=order)


def check_key():
    if request.args.get("key") != ADMIN_KEY:
        abort(403)


@app.route("/admin")
def admin():
    check_key()
    with lock:
        orders = load()
    rows = "".join(
        f"<tr><td>{o['id']}</td><td>{o['time']}</td><td>{o['nick']}</td>"
        f"<td>{o['stars']:,}</td><td>{o['price']} ₽</td><td>{o['status']}</td>"
        f"<td><a href='/admin/paid/{o['id']}?key={ADMIN_KEY}'>оплачен</a></td></tr>"
        for o in reversed(orders)
    )
    return (
        "<meta charset=utf-8><body style='font-family:sans-serif;background:#000;color:#eee'>"
        f"<h2>Заказы. В наличии: {stock_left(orders):,}</h2>"
        "<table border=1 cellpadding=6 style='border-collapse:collapse'>"
        "<tr><th>№</th><th>Время</th><th>Ник</th><th>Звёзд</th><th>Сумма</th><th>Статус</th><th></th></tr>"
        f"{rows}</table>"
    )


@app.route("/admin/paid/<int:oid>")
def admin_paid(oid):
    check_key()
    mark_paid(oid)
    return redirect(f"/admin?key={ADMIN_KEY}")


@app.route("/tg/<h>", methods=["POST"])
def tg_webhook(h):
    if not HOOK or h != HOOK:
        abort(404)
    u = request.get_json(silent=True) or {}
    msg = u.get("message")
    if msg:
        chat = str(msg["chat"]["id"])
        text = msg.get("text", "")
        if text.startswith("/start"):
            tg("sendMessage", {"chat_id": chat, "text": f"Ваш chat id: {chat}\nДобавьте его в ADMIN_CHAT_ID на сервере."})
        elif chat == ADMIN_CHAT_ID and text.startswith("/orders"):
            with lock:
                orders = load()
            new = [o for o in orders if o["status"] == "new"][-10:]
            body = "\n\n".join(order_text(o) for o in new) or "Новых заказов нет"
            tg("sendMessage", {"chat_id": chat, "text": f"{body}\n\nВ наличии: {stock_left(orders):,}"})
    cb = u.get("callback_query")
    if cb and str(cb["from"]["id"]) == ADMIN_CHAT_ID and cb.get("data", "").startswith("paid:"):
        o = mark_paid(int(cb["data"].split(":")[1]))
        tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Отмечено как оплачен"})
        if o:
            tg("editMessageText", {"chat_id": cb["message"]["chat"]["id"],
                                   "message_id": cb["message"]["message_id"], "text": order_text(o)})
    return "ok"


if BOT_TOKEN and PUBLIC_URL:
    tg("setWebhook", {"url": f"{PUBLIC_URL}/tg/{HOOK}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
