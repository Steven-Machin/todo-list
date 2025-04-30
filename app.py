from flask import Flask, render_template, request, redirect

app = Flask(__name__)
tasks = []  

@app.route("/")
def index():
    return render_template("index.html", tasks=tasks)

@app.route("/add", methods=["POST"])
def add():
    task = request.form.get("task", "").strip()
    if task:
        tasks.append(task)
    return redirect("/")

@app.route("/remove/<int:task_id>")
def remove(task_id):
    if 0 <= task_id < len(tasks):
        tasks.pop(task_id)
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)
