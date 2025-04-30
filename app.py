from flask import Flask, render_template, request, redirect, url_for
import json
import os
from datetime import datetime

app = Flask(__name__)
TASKS_FILE = "tasks.json"

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r") as file:
            return json.load(file)
    return []

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as file:
        json.dump(tasks, file, indent=2)

tasks = load_tasks()

@app.route("/")
def index():
    return render_template("index.html", tasks=tasks, app_name="To Do List")

@app.route("/add", methods=["POST"])
def add():
    text = request.form.get("task", "").strip()
    priority = request.form.get("priority", "Medium")
    if text:
        task = {
            "text": text,
            "done": False,
            "priority": priority
        }
        tasks.append(task)
        save_tasks(tasks)
    return redirect("/")

@app.route("/toggle/<int:task_id>")
def toggle(task_id):
    if 0 <= task_id < len(tasks):
        tasks[task_id]["done"] = not tasks[task_id]["done"]
        save_tasks(tasks)
    return redirect("/")

@app.route("/remove/<int:task_id>")
def remove(task_id):
    if 0 <= task_id < len(tasks):
        tasks.pop(task_id)
        save_tasks(tasks)
    return redirect("/")

@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit(task_id):
    if request.method == "POST":
        text = request.form.get("task", "").strip()
        priority = request.form.get("priority", "Medium")
        if text and 0 <= task_id < len(tasks):
            tasks[task_id]["text"] = text
            tasks[task_id]["priority"] = priority
            save_tasks(tasks)
        return redirect("/")
    else:
        if 0 <= task_id < len(tasks):
            return render_template("edit.html", task=tasks[task_id], task_id=task_id)
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
