"""IronBox Fitness - rezervační systém (maturitní práce). Flask + SQLite."""
import csv
import io
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Response, Flask, flash, g, redirect, render_template, request,
                   session, url_for, abort)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "zmen-me-v-produkci")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ironbox.db")
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)
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
CREATE TABLE IF NOT EXISTS waitlist(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  UNIQUE(user_id, session_id));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf"] = csrf


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf", "").encode()
        if not session.get("csrf") or not secrets.compare_digest(sent, session["csrf"].encode()):
            abort(400)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def error(e):
    return render_template("error.html", e=e), e.code


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
    try:  # migrace starších databází
        con.execute("ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 5")
    except sqlite3.OperationalError:
        pass
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
  (SELECT COUNT(*) FROM bookings b WHERE b.session_id=s.id AND b.user_id=?1) AS mine,
  (SELECT COUNT(*) FROM waitlist w WHERE w.session_id=s.id AND w.user_id=?1) AS wl
FROM sessions s JOIN classes c ON c.id=s.class_id
WHERE s.starts_at > ?2 {extra} ORDER BY s.starts_at"""


@app.route("/")
def index():
    uid = session.get("uid", 0)
    cid = request.args.get("lekce", type=int)
    extra, args = ("AND s.class_id=?3", [uid, datetime.now().strftime(FMT), cid]) if cid \
        else ("", [uid, datetime.now().strftime(FMT)])
    sessions = db().execute(SESSION_SQL.format(extra=extra), args).fetchall()
    classes = db().execute("SELECT * FROM classes").fetchall()
    q = lambda sql: db().execute(sql).fetchone()[0]
    stats = dict(classes=q("SELECT COUNT(*) FROM classes"),
                 trainers=q("SELECT COUNT(DISTINCT trainer) FROM sessions"),
                 sessions=q("SELECT COUNT(*) FROM sessions WHERE starts_at > '%s'" % datetime.now().strftime(FMT)),
                 members=q("SELECT COUNT(*) FROM users"))
    return render_template("index.html", sessions=sessions, classes=classes, cid=cid, stats=stats)


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
    s = db().execute(SESSION_SQL.format(extra="AND s.id=?3"),
                     (session["uid"], datetime.now().strftime(FMT), sid)).fetchone()
    if not s:
        flash("Lekce neexistuje nebo už proběhla.", "err")
    elif s["mine"]:
        flash("Na tuto lekci už jsi rezervovaný.", "err")
    elif s["taken"] >= s["capacity"]:
        flash("Lekce je plná – můžeš se zapsat na čekací listinu.", "err")
    elif current_user()["credits"] < 1:
        flash("Nemáš dostatek kreditů. Kredity ti dobije recepce.", "err")
    else:
        db().execute("INSERT INTO bookings(user_id,session_id,created_at) VALUES(?,?,?)",
                     (session["uid"], sid, datetime.now().strftime(FMT)))
        db().execute("UPDATE users SET credits=credits-1 WHERE id=?", (session["uid"],))
        db().execute("DELETE FROM waitlist WHERE user_id=? AND session_id=?", (session["uid"], sid))
        db().commit()
        flash(f"Rezervace potvrzena: {s['cname']}, {fmt(s['starts_at'])}.", "ok")
    return redirect(request.referrer or url_for("index"))


@app.route("/zrusit/<int:sid>", methods=["POST"])
@login_required()
def cancel(sid):
    s = db().execute("SELECT starts_at FROM sessions WHERE id=?", (sid,)).fetchone()
    if s and datetime.strptime(s["starts_at"], FMT) - datetime.now() < timedelta(hours=2):
        flash("Rezervaci lze zrušit nejpozději 2 hodiny před začátkem.", "err")
        return redirect(request.referrer or url_for("my"))
    cur = db().execute("DELETE FROM bookings WHERE user_id=? AND session_id=?", (session["uid"], sid))
    if cur.rowcount:
        db().execute("UPDATE users SET credits=credits+1 WHERE id=?", (session["uid"],))
        promote(sid)
    db().commit()
    flash("Rezervace zrušena, kredit vrácen.", "ok")
    return redirect(request.referrer or url_for("my"))


@app.route("/moje")
@login_required()
def my():
    rows = db().execute("""SELECT s.*, c.name AS cname, c.emoji FROM bookings b
        JOIN sessions s ON s.id=b.session_id JOIN classes c ON c.id=s.class_id
        WHERE b.user_id=? ORDER BY s.starts_at""", (session["uid"],)).fetchall()
    now = datetime.now().strftime(FMT)
    wait = db().execute("""SELECT s.*, c.name AS cname, c.emoji FROM waitlist w
        JOIN sessions s ON s.id=w.session_id JOIN classes c ON c.id=s.class_id
        WHERE w.user_id=? AND s.starts_at>? ORDER BY s.starts_at""",
                        (session["uid"], now)).fetchall()
    return render_template("my.html", wait=wait, upcoming=[r for r in rows if r["starts_at"] > now],
                           past=[r for r in rows if r["starts_at"] <= now])


@app.route("/admin", methods=["GET", "POST"])
@login_required(admin=True)
def admin():
    if request.method == "POST":
        try:
            start = datetime.strptime(request.form["starts_at"], "%Y-%m-%dT%H:%M")
            cap = int(request.form["capacity"])
            weeks = min(max(int(request.form.get("repeat", 1)), 1), 12)
            assert cap > 0 and request.form["trainer"].strip()
        except (ValueError, AssertionError):
            flash("Neplatná data formuláře.", "err")
        else:
            for i in range(weeks):
                db().execute("INSERT INTO sessions(class_id,trainer,starts_at,capacity) VALUES(?,?,?,?)",
                             (request.form["class_id"], request.form["trainer"].strip(),
                              (start + timedelta(weeks=i)).strftime(FMT), cap))
            db().commit()
            flash(f"Přidáno termínů: {weeks}.", "ok")
        return redirect(url_for("admin"))
    classes = db().execute("SELECT * FROM classes").fetchall()
    rows = db().execute("""SELECT s.*, c.name AS cname,
        (SELECT GROUP_CONCAT(u.name, ', ') FROM bookings b JOIN users u ON u.id=b.user_id
         WHERE b.session_id=s.id) AS people
        FROM sessions s JOIN classes c ON c.id=s.class_id ORDER BY s.starts_at""").fetchall()
    users = db().execute("""SELECT u.*, (SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id) AS n
        FROM users u ORDER BY u.id""").fetchall()
    msgs = db().execute("SELECT * FROM messages ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("admin.html", classes=classes, rows=rows, users=users, msgs=msgs)


@app.route("/admin/smazat/<int:sid>", methods=["POST"])
@login_required(admin=True)
def admin_delete(sid):
    db().execute("UPDATE users SET credits=credits+1 WHERE id IN (SELECT user_id FROM bookings WHERE session_id=?)", (sid,))
    db().execute("DELETE FROM sessions WHERE id=?", (sid,))
    db().commit()
    return redirect(url_for("admin"))


@app.route("/kontakt", methods=["POST"])
def contact():
    n, e, m = (request.form.get(k, "").strip() for k in ("name", "email", "message"))
    if not n or "@" not in e or len(m) < 5:
        flash("Vyplň jméno, e-mail a zprávu.", "err")
    else:
        db().execute("INSERT INTO messages(name,email,body,created_at) VALUES(?,?,?,?)",
                     (n, e, m, datetime.now().strftime(FMT)))
        db().commit()
        flash("Zpráva odeslána, ozveme se.", "ok")
    return redirect(url_for("index") + "#kontakt")


@app.route("/profil", methods=["GET", "POST"])
@login_required()
def profile():
    u = current_user()
    if request.method == "POST":
        name, old, new = request.form["name"].strip(), request.form["old"], request.form["new"]
        if not name:
            flash("Jméno nesmí být prázdné.", "err")
        elif new and not check_password_hash(u["password_hash"], old):
            flash("Staré heslo nesouhlasí.", "err")
        elif new and len(new) < 6:
            flash("Nové heslo musí mít aspoň 6 znaků.", "err")
        else:
            db().execute("UPDATE users SET name=? WHERE id=?", (name, u["id"]))
            if new:
                db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new), u["id"]))
            db().commit()
            flash("Profil uložen.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/admin/lekce", methods=["POST"])
@login_required(admin=True)
def admin_class():
    n = request.form["name"].strip()
    if n:
        db().execute("INSERT INTO classes(name,description,emoji) VALUES(?,?,?)",
                     (n, request.form["description"].strip(), request.form["emoji"].strip() or "🏃"))
        db().commit()
        flash("Lekce přidána.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/lekce/smazat/<int:cid>", methods=["POST"])
@login_required(admin=True)
def admin_class_delete(cid):
    if db().execute("SELECT 1 FROM sessions WHERE class_id=?", (cid,)).fetchone():
        flash("Lekce má termíny, nejdřív je smaž.", "err")
    else:
        db().execute("DELETE FROM classes WHERE id=?", (cid,))
        db().commit()
        flash("Lekce smazána.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/upravit/<int:sid>", methods=["GET", "POST"])
@login_required(admin=True)
def admin_edit(sid):
    s = db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() or abort(404)
    taken = db().execute("SELECT COUNT(*) FROM bookings WHERE session_id=?", (sid,)).fetchone()[0]
    if request.method == "POST":
        try:
            start = datetime.strptime(request.form["starts_at"], "%Y-%m-%dT%H:%M").strftime(FMT)
            cap = int(request.form["capacity"])
            assert cap >= max(taken, 1) and request.form["trainer"].strip()
        except (ValueError, AssertionError):
            flash(f"Neplatná data (kapacita nesmí být menší než počet rezervací: {taken}).", "err")
            return redirect(url_for("admin_edit", sid=sid))
        db().execute("UPDATE sessions SET class_id=?, trainer=?, starts_at=?, capacity=? WHERE id=?",
                     (request.form["class_id"], request.form["trainer"].strip(), start, cap, sid))
        db().commit()
        promote(sid)
        flash("Termín upraven.", "ok")
        return redirect(url_for("admin"))
    classes = db().execute("SELECT * FROM classes").fetchall()
    return render_template("edit.html", s=s, classes=classes,
                           dt=datetime.strptime(s["starts_at"], FMT).strftime("%Y-%m-%dT%H:%M"))


def promote(sid):
    """Uvolněné místo dostane první z čekací listiny, který má kredit."""
    d = db()
    while True:
        cap = d.execute("SELECT capacity FROM sessions WHERE id=?", (sid,)).fetchone()
        taken = d.execute("SELECT COUNT(*) FROM bookings WHERE session_id=?", (sid,)).fetchone()[0]
        if not cap or taken >= cap["capacity"]:
            break
        w = d.execute("""SELECT w.id, w.user_id FROM waitlist w JOIN users u ON u.id=w.user_id
            WHERE w.session_id=? AND u.credits>=1 ORDER BY w.id LIMIT 1""", (sid,)).fetchone()
        if not w:
            break
        d.execute("DELETE FROM waitlist WHERE id=?", (w["id"],))
        d.execute("INSERT OR IGNORE INTO bookings(user_id,session_id,created_at) VALUES(?,?,?)",
                  (w["user_id"], sid, datetime.now().strftime(FMT)))
        d.execute("UPDATE users SET credits=credits-1 WHERE id=?", (w["user_id"],))
    d.commit()


@app.route("/cekacka/<int:sid>", methods=["POST"])
@login_required()
def waitlist_join(sid):
    uid = session["uid"]
    s = db().execute("SELECT starts_at FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s or s["starts_at"] <= datetime.now().strftime(FMT):
        flash("Lekce neexistuje nebo už proběhla.", "err")
    elif db().execute("SELECT 1 FROM bookings WHERE user_id=? AND session_id=?", (uid, sid)).fetchone():
        flash("Na tuto lekci už jsi rezervovaný.", "err")
    else:
        db().execute("INSERT OR IGNORE INTO waitlist(user_id,session_id) VALUES(?,?)", (uid, sid))
        db().commit()
        flash("Jsi na čekací listině. Uvolní-li se místo, přihlásíme tě automaticky (je třeba mít kredit).", "ok")
    return redirect(request.referrer or url_for("index"))


@app.route("/cekacka/zrusit/<int:sid>", methods=["POST"])
@login_required()
def waitlist_leave(sid):
    db().execute("DELETE FROM waitlist WHERE user_id=? AND session_id=?", (session["uid"], sid))
    db().commit()
    flash("Odebrán z čekací listiny.", "ok")
    return redirect(request.referrer or url_for("my"))


@app.route("/admin/kredity/<int:uid>", methods=["POST"])
@login_required(admin=True)
def admin_credits(uid):
    try:
        n = int(request.form["amount"])
    except ValueError:
        n = 0
    db().execute("UPDATE users SET credits=MAX(credits+?,0) WHERE id=?", (n, uid))
    db().commit()
    flash("Kredity upraveny.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/export/<int:sid>")
@login_required(admin=True)
def admin_export(sid):
    rows = db().execute("""SELECT u.name, u.email, b.created_at FROM bookings b
        JOIN users u ON u.id=b.user_id WHERE b.session_id=? ORDER BY b.id""", (sid,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Jméno", "E-mail", "Rezervováno"])
    w.writerows([tuple(r) for r in rows])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=prezentka-{sid}.csv"})


init_db()
if __name__ == "__main__":
    app.run(debug=True)
