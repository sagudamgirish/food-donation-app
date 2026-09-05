import os
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "nourishnet_secret_key_123")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def notify_user(user_id, message):
    supabase.table("notifications").insert({
        "user_id": user_id,
        "message": message,
        "is_read": False
    }).execute()

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    role = session["role"]
    now_iso = datetime.now(timezone.utc).isoformat()

    notifs = supabase.table("notifications") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("is_read", False) \
        .order("created_at", desc=True) \
        .execute().data

    my_listings = []
    available_food = []
    shelter_requests = []
    my_requests = []

    if role == "donor":
        my_listings = supabase.table("food_listings").select("*").eq("donor_id", user_id).execute().data
        shelter_requests = supabase.table("shelter_requests").select("*").eq("status", "Pending").execute().data
    elif role == "receiver":
        available_food = supabase.table("food_listings") \
            .select("*") \
            .eq("status", "Available") \
            .gt("expiry_time", now_iso) \
            .execute().data
        my_listings = supabase.table("food_listings").select("*").eq("claimed_by_id", user_id).execute().data
        my_requests = supabase.table("shelter_requests").select("*").eq("shelter_id", user_id).execute().data

    return render_template(
        "dashboard.html",
        role=role,
        user=session,
        notifications=notifs,
        my_listings=my_listings,
        available_food=available_food,
        shelter_requests=shelter_requests,
        my_requests=my_requests
    )

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        existing = supabase.table("users").select("*").eq("email", email).execute().data
        if existing:
            flash("Email already registered!", "danger")
            return redirect(url_for("register"))

        supabase.table("users").insert({
            "name": request.form.get("name"),
            "email": email,
            "password": request.form.get("password"),
            "role": request.form.get("role"),
            "phone": request.form.get("phone"),
            "address": request.form.get("address")
        }).execute()

        flash("Registration successful! Please login.", "success")
        return redirect(url_for("login"))
        
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        res = supabase.table("users").select("*").eq("email", email).eq("password", password).execute().data

        if res:
            user = res[0]
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            session["phone"] = user["phone"]
            session["address"] = user["address"]
            return redirect(url_for("index"))

        flash("Invalid email or password!", "danger")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/add_food", methods=["POST"])
def add_food():
    if session.get("role") != "donor":
        return redirect(url_for("index"))

    time_str = request.form.get("expiry_time")
    now_utc = datetime.now(timezone.utc)
    parsed_time = datetime.strptime(time_str, "%H:%M").time()
    combined_dt = datetime.combine(now_utc.date(), parsed_time).replace(tzinfo=timezone.utc)
    
    if combined_dt < now_utc:
        combined_dt += timedelta(days=1)

    supabase.table("food_listings").insert({
        "donor_id": session["user_id"],
        "donor_name": session["name"],
        "food_item": request.form.get("food_item"),
        "quantity": request.form.get("quantity"),
        "pickup_location": session["address"],
        "contact_number": session["phone"],
        "expiry_time": combined_dt.isoformat(),
        "status": "Available"
    }).execute()

    flash("Surplus food posted successfully!", "success")
    return redirect(url_for("index"))

@app.route("/claim_food/<int:item_id>", methods=["POST"])
def claim_food(item_id):
    if session.get("role") != "receiver":
        return redirect(url_for("index"))

    item = supabase.table("food_listings").select("*").eq("id", item_id).execute().data[0]

    supabase.table("food_listings").update({
        "status": "Claimed",
        "claimed_by_id": session["user_id"],
        "claimed_by_name": session["name"],
        "claimed_by_contact": session["phone"],
        "claimed_by_location": session["address"]
    }).eq("id", item_id).execute()

    notify_user(
        item["donor_id"], 
        f"🎉 Your food post '{item['food_item']}' was claimed by: {session['name']} ({session['phone']})!"
    )

    flash("Food claimed successfully!", "success")
    return redirect(url_for("index"))

@app.route("/request_food", methods=["POST"])
def request_food():
    if session.get("role") != "receiver":
        return redirect(url_for("index"))

    supabase.table("shelter_requests").insert({
        "shelter_id": session["user_id"],
        "shelter_name": session["name"],
        "food_needed": request.form.get("food_needed"),
        "quantity_needed": request.form.get("quantity_needed"),
        "contact_number": session["phone"],
        "location": session["address"],
        "status": "Pending"
    }).execute()

    flash("Food request posted to donors!", "success")
    return redirect(url_for("index"))

@app.route("/accept_request/<int:req_id>", methods=["POST"])
def accept_request(req_id):
    if session.get("role") != "donor":
        return redirect(url_for("index"))

    req_data = supabase.table("shelter_requests").select("*").eq("id", req_id).execute().data[0]

    supabase.table("shelter_requests").update({
        "status": "Accepted",
        "accepted_by_id": session["user_id"],
        "accepted_by_name": session["name"],
        "accepted_by_contact": session["phone"]
    }).eq("id", req_id).execute()

    notify_user(
        req_data["shelter_id"],
        f"🍱 Donor '{session['name']}' ({session['phone']}) accepted your request for '{req_data['food_needed']}'!"
    )

    flash("Request accepted! Shelter has been notified.", "success")
    return redirect(url_for("index"))

@app.route("/clear_notifications")
def clear_notifications():
    if "user_id" in session:
        supabase.table("notifications").update({"is_read": True}).eq("user_id", session["user_id"]).execute()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)