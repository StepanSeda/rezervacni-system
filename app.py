"""IronBox Fitness - rezervační systém (maturitní práce). Flask + SQLite."""
import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for, abort)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "zmen-me-v-produkci")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ironbox.db")
FMT = "%Y-%m-%d %H:%M"
DNY = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS classes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, description TEXT, emoji TEXT);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  class_id INTEGER NOT NULL REFERENCES classes(id),
  trainer TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  capacity INTEGER NOT NULL CHECK(capacity > 0));
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, session_id));
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM classes").fetchone():
        con.executemany("INSERT INTO classes(name,description,emoji) VALUES(?,?,?)", [
            ("CrossFit", "Intenzivní funkční trénink pro všechny úrovně.", "🏋️"),
            ("Silový trénink", "Základní cviky s velkými činkami pod vedením trenéra.", "💪"),
            ("HIIT", "Krátký intervalový trénink na 45 minut.", "🔥"),
            ("Jóga a mobilita", "Protažení, uvolnění a regenerace.", "🧘")])
        base = datetime.now().replace(minute=0, second=0, microsecond=0)
        plan = [(1, "Petr Novák", 17, 12), (3, "Jana Svobodová", 18, 12),
                (2, "Petr Novák", 17, 10), (4, "Eva Dvořáková", 19, 15)]
        for d in range(1, 8):
            cid, tr, h, cap = plan[d % 4]
            t = (base + timedelta(days=d)).replace(hour=h)
            con.execute("INSERT INTO sessions(class_id,trainer,starts_at,capacity) VALUES(?,?,?,?)",
                        (cid, tr, t.strftime(FMT), cap))
        con.execute("INSERT INTO users(name,email,password_hash,is_admin) VALUES(?,?,?,1)",
                    ("Admin", "admin@ironbox.cz", generate_password_hash("admin123")))
    con.commit()
    con.close()


@app.template_filter("fmt")
def fmt(s):
    d = datetime.strptime(s, FMT)
    return f"{DNY[d.weekday()]} {d.day}. {d.month}. {d:%H:%M}"


@app.context_processor
def inject_user():
    return {"user": current_user()}


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(admin=False):
    def deco(f):
        @wraps(f)
        def wrap(*a, **kw):
            u = current_user()
            if not u:
                flash("Nejdřív se přihlaš.", "err")
                return redirect(url_for("login"))
            if admin and not u["is_admin"]:
                abort(403)
            return f(*a, **kw)
        return wrap
    return deco


SESSION_SQL = """
SELECT s.*, c.name AS cname, c.emoji,
  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id) AS taken,
  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id AND b.user_id=?) AS mine
FROM sessions s JOIN classes c ON c.id=s.class_id
WHERE s.starts_at > ? {extra} ORDER BY s.starts_at"""


@app.route("/")
def index():
    uid = session.get("uid", 0)
    cid = request.args.get("lekce", type=int)
    extra, args = ("AND s.class_id=?", [uid, datetime.now().strftime(FMT), cid]) if cid \
        else ("", [uid, datetime.now().strftime(FMT)])
    sessions = db().execute(SESSION_SQL.format(extra=extra), args).fetchall()
    classes = db().execute("SELECT * FROM classes").fetchall()
    return render_template("index.html", sessions=sessions, classes=classes, cid=cid)


@app.route("/registrace", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        pw = request.form["password"]
        if not name or "@" not in email or len(pw) < 6:
            flash("Vyplň jméno, platný e-mail a heslo (min. 6 znaků).", "err")
        elif db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Tento e-mail už je registrovaný.", "err")
        else:
            cur = db().execute("INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                               (name, email, generate_password_hash(pw)))
            db().commit()
            session["uid"] = cur.lastrowid
            flash("Účet vytvořen, vítej!", "ok")
            return redirect(url_for("index"))
    return render_template("auth.html", register=True)


@app.route("/prihlaseni", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE email=?",
                         (request.form["email"].strip().lower(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session["uid"] = u["id"]
            return redirect(url_for("index"))
        flash("Špatný e-mail nebo heslo.", "err")
    return render_template("auth.html", register=False)


@app.route("/odhlasit")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/rezervovat/<int:sid>", methods=["POST"])
@login_required()
def book(sid):
    s = db().execute(SESSION_SQL.format(extra="AND s.id=?"),
                     (session["uid"], datetime.now().strftime(FMT), sid)).fetchone()
    if not s:
        flash("Lekce neexistuje nebo už proběhla.", "err")
    elif s["mine"]:
        flash("Na tuto lekci už jsi rezervovaný.", "err")
    elif s["taken"] >= s["capacity"]:
        flash("Lekce je bohužel plná.", "err")
    else:
        db().execute("INSERT INTO bookings(user_id,session_id,created_at) VALUES(?,?,?)",
                     (session["uid"], sid, datetime.now().strftime(FMT)))
        db().commit()
        flash(f"Rezervace potvrzena: {s['cname']}, {fmt(s['starts_at'])}.", "ok")
    return redirect(request.referrer or url_for("index"))


@app.route("/zrusit/<int:sid>", methods=["POST"])
@login_required()
def cancel(sid):
    db().execute("DELETE FROM bookings WHERE user_id=? AND session_id=?", (session["uid"], sid))
    db().commit()
    flash("Rezervace zrušena.", "ok")
    return redirect(request.referrer or url_for("my"))


@app.route("/moje")
@login_required()
def my():
    rows = db().execute("""SELECT s.*, c.name AS cname, c.emoji FROM bookings b
        JOIN sessions s ON s.id=b.session_id JOIN classes c ON c.id=s.class_id
        WHERE b.user_id=? ORDER BY s.starts_at""", (session["uid"],)).fetchall()
    now = datetime.now().strftime(FMT)
    return render_template("my.html", upcoming=[r for r in rows if r["starts_at"] > now],
                           past=[r for r in rows if r["starts_at"] <= now])


@app.route("/admin", methods=["GET", "POST"])
@login_required(admin=True)
def admin():
    if request.method == "POST":
        try:
            start = datetime.strptime(request.form["starts_at"], "%Y-%m-%dT%H:%M").strftime(FMT)
            cap = int(request.form["capacity"])
            assert cap > 0 and request.form["trainer"].strip()
        except (ValueError, AssertionError):
            flash("Neplatná data formuláře.", "err")
        else:
            db().execute("INSERT INTO sessions(class_id,trainer,starts_at,capacity) VALUES(?,?,?,?)",
                         (request.form["class_id"], request.form["trainer"].strip(), start, cap))
            db().commit()
            flash("Termín přidán.", "ok")
        return redirect(url_for("admin"))
    classes = db().execute("SELECT * FROM classes").fetchall()
    rows = db().execute("""SELECT s.*, c.name AS cname,
        (SELECT GROUP_CONCAT(u.name, ', ') FROM bookings b JOIN users u ON u.id=b.user_id
         WHERE b.session_id=s.id) AS people
        FROM sessions s JOIN classes c ON c.id=s.class_id ORDER BY s.starts_at""").fetchall()
    return render_template("admin.html", classes=classes, rows=rows)


@app.route("/admin/smazat/<int:sid>", methods=["POST"])
@login_required(admin=True)
def admin_delete(sid):
    db().execute("DELETE FROM sessions WHERE id=?", (sid,))
    db().commit()
    return redirect(url_for("admin"))


init_db()
if __name__ == "__main__":
    app.run(debug=True)
