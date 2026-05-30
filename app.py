import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta
import json

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── Auth Helpers ────────────────────────────────────────────────
def get_user():
    return session.get("user")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─── Auth Routes ─────────────────────────────────────────────────
@app.route("/")
def index():
    if get_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        try:
            res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            session["user"] = {"id": res.user.id, "email": res.user.email, "token": res.session.access_token}
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash("Invalid credentials. Please try again.", "error")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        name = request.form["name"]
        try:
            res = supabase.auth.sign_up({"email": email, "password": password})
            uid = res.user.id
            try:
                supabase.table("users").insert({"id": uid, "email": email, "name": name}).execute()
            except:
                pass
            res2 = supabase.auth.sign_in_with_password({"email": email, "password": password})
            session["user"] = {"id": res2.user.id, "email": res2.user.email, "token": res2.session.access_token}
            return redirect(url_for("dashboard"))
        except Exception as e:
            flash(str(e), "error")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Dashboard ───────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    user = get_user()
    uid = user["id"]
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime("%Y-%m-%d")

    expenses = supabase.table("expenses").select("*").eq("user_id", uid).gte("date", start_of_month).execute().data or []
    incomes = supabase.table("income").select("*").eq("user_id", uid).gte("date", start_of_month).execute().data or []
    budgets = supabase.table("budgets").select("*").eq("user_id", uid).execute().data or []
    categories = supabase.table("categories").select("*").eq("user_id", uid).execute().data or []
    recent_expenses = supabase.table("expenses").select("*, categories(name, color)").eq("user_id", uid).order("date", desc=True).limit(5).execute().data or []

    total_expense = sum(float(e["amount"]) for e in expenses)
    total_income = sum(float(i["amount"]) for i in incomes)
    balance = total_income - total_expense
    savings_rate = (balance / total_income * 100) if total_income > 0 else 0

    # Category breakdown
    cat_totals = {}
    for e in expenses:
        cid = e.get("category_id", "Other")
        cat_totals[cid] = cat_totals.get(cid, 0) + float(e["amount"])

    # Budget progress
    budget_progress = []
    for b in budgets:
        spent = sum(float(e["amount"]) for e in expenses if e.get("category_id") == b.get("category_id"))
        limit = float(b["amount"])
        pct = min((spent / limit * 100), 100) if limit > 0 else 0
        budget_progress.append({**b, "spent": spent, "pct": round(pct, 1)})

    # 7-day trend
    daily = {}
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = 0
    for e in supabase.table("expenses").select("amount,date").eq("user_id", uid).gte("date", (now - timedelta(days=6)).strftime("%Y-%m-%d")).execute().data or []:
        d = e["date"][:10]
        if d in daily:
            daily[d] += float(e["amount"])

    return render_template("dashboard.html",
        user=user, total_expense=total_expense, total_income=total_income,
        balance=balance, savings_rate=round(savings_rate, 1),
        budget_progress=budget_progress, categories=categories,
        recent_expenses=recent_expenses, daily_trend=json.dumps(daily),
        cat_totals=json.dumps(cat_totals), month=now.strftime("%B %Y")
    )

# ─── Expenses ────────────────────────────────────────────────────
@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    user = get_user()
    uid = user["id"]
    categories = supabase.table("categories").select("*").eq("user_id", uid).execute().data or []

    if request.method == "POST":
        data = {
            "user_id": uid,
            "title": request.form["title"],
            "amount": float(request.form["amount"]),
            "category_id": request.form.get("category_id") or None,
            "date": request.form["date"],
            "notes": request.form.get("notes", ""),
        }
        supabase.table("expenses").insert(data).execute()
        flash("Expense added!", "success")
        return redirect(url_for("expenses"))

    filter_month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    start = filter_month + "-01"
    # end of month
    y, m = map(int, filter_month.split("-"))
    import calendar
    end = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"

    all_expenses = supabase.table("expenses").select("*, categories(name, color)").eq("user_id", uid).gte("date", start).lte("date", end).order("date", desc=True).execute().data or []
    total = sum(float(e["amount"]) for e in all_expenses)

    return render_template("expenses.html", expenses=all_expenses, categories=categories, total=total, filter_month=filter_month)

@app.route("/expenses/delete/<int:eid>", methods=["POST"])
@login_required
def delete_expense(eid):
    uid = get_user()["id"]
    supabase.table("expenses").delete().eq("id", eid).eq("user_id", uid).execute()
    flash("Expense deleted.", "info")
    return redirect(url_for("expenses"))

# ─── Income ──────────────────────────────────────────────────────
@app.route("/income", methods=["GET", "POST"])
@login_required
def income():
    user = get_user()
    uid = user["id"]

    if request.method == "POST":
        data = {
            "user_id": uid,
            "source": request.form["source"],
            "amount": float(request.form["amount"]),
            "date": request.form["date"],
            "notes": request.form.get("notes", ""),
        }
        supabase.table("income").insert(data).execute()
        flash("Income added!", "success")
        return redirect(url_for("income"))

    filter_month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    start = filter_month + "-01"
    import calendar
    y, m = map(int, filter_month.split("-"))
    end = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"

    all_income = supabase.table("income").select("*").eq("user_id", uid).gte("date", start).lte("date", end).order("date", desc=True).execute().data or []
    total = sum(float(i["amount"]) for i in all_income)

    return render_template("income.html", incomes=all_income, total=total, filter_month=filter_month)

@app.route("/income/delete/<int:iid>", methods=["POST"])
@login_required
def delete_income(iid):
    uid = get_user()["id"]
    supabase.table("income").delete().eq("id", iid).eq("user_id", uid).execute()
    flash("Income record deleted.", "info")
    return redirect(url_for("income"))

# ─── Budget ──────────────────────────────────────────────────────
@app.route("/budget", methods=["GET", "POST"])
@login_required
def budget():
    user = get_user()
    uid = user["id"]
    categories = supabase.table("categories").select("*").eq("user_id", uid).execute().data or []

    if request.method == "POST":
        data = {
            "user_id": uid,
            "category_id": request.form.get("category_id") or None,
            "amount": float(request.form["amount"]),
            "month": request.form["month"],
        }
        supabase.table("budgets").insert(data).execute()
        flash("Budget set!", "success")
        return redirect(url_for("budget"))

    budgets = supabase.table("budgets").select("*, categories(name, color)").eq("user_id", uid).execute().data or []
    now = datetime.now()
    start_of_month = now.replace(day=1).strftime("%Y-%m-%d")
    expenses = supabase.table("expenses").select("amount, category_id").eq("user_id", uid).gte("date", start_of_month).execute().data or []

    budget_data = []
    for b in budgets:
        spent = sum(float(e["amount"]) for e in expenses if e.get("category_id") == b.get("category_id"))
        limit = float(b["amount"])
        pct = min((spent / limit * 100), 100) if limit > 0 else 0
        budget_data.append({**b, "spent": round(spent, 2), "remaining": round(limit - spent, 2), "pct": round(pct, 1)})

    return render_template("budget.html", budgets=budget_data, categories=categories)

@app.route("/budget/delete/<int:bid>", methods=["POST"])
@login_required
def delete_budget(bid):
    uid = get_user()["id"]
    supabase.table("budgets").delete().eq("id", bid).eq("user_id", uid).execute()
    return redirect(url_for("budget"))

# ─── Categories ──────────────────────────────────────────────────
@app.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    user = get_user()
    uid = user["id"]

    if request.method == "POST":
        data = {
            "user_id": uid,
            "name": request.form["name"],
            "color": request.form.get("color", "#6366f1"),
            "icon": request.form.get("icon", "💳"),
        }
        supabase.table("categories").insert(data).execute()
        flash("Category added!", "success")
        return redirect(url_for("categories"))

    all_cats = supabase.table("categories").select("*").eq("user_id", uid).execute().data or []
    return render_template("categories.html", categories=all_cats)

@app.route("/categories/delete/<int:cid>", methods=["POST"])
@login_required
def delete_category(cid):
    uid = get_user()["id"]
    supabase.table("categories").delete().eq("id", cid).eq("user_id", uid).execute()
    return redirect(url_for("categories"))

# ─── API endpoints for charts ────────────────────────────────────
@app.route("/api/monthly-summary")
@login_required
def monthly_summary():
    uid = get_user()["id"]
    months = []
    for i in range(5, -1, -1):
        d = datetime.now().replace(day=1) - timedelta(days=i*28)
        m = d.strftime("%Y-%m")
        months.append(m)

    result = []
    for m in months:
        import calendar
        y, mo = map(int, m.split("-"))
        start = f"{y}-{mo:02d}-01"
        end = f"{y}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
        exp = supabase.table("expenses").select("amount").eq("user_id", uid).gte("date", start).lte("date", end).execute().data or []
        inc = supabase.table("income").select("amount").eq("user_id", uid).gte("date", start).lte("date", end).execute().data or []
        result.append({
            "month": m,
            "expenses": round(sum(float(e["amount"]) for e in exp), 2),
            "income": round(sum(float(i["amount"]) for i in inc), 2),
        })
    return jsonify(result)

if __name__ == "__main__":
    app.run(debug=True)