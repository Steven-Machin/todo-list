To-Do List (Team Task Manager - Flask Web App)
This is a Flask-based web application for managing and delegating tasks within a small team. It supports multiple users with different roles (manager or member) and allows managers to assign tasks, while team members can view and complete only their assigned items.

Tasks and users are stored in local JSON files for persistence between sessions.

Features
User signup/login system

Role-based access:

Managers can view, add, assign, edit, and remove tasks

Members can view and edit only their own tasks

Add new tasks with:

Priority (Low, Medium, High)

Due date

Notes

Mark tasks as complete/incomplete

Sort tasks by due date, priority, creation date, or status

Filter tasks by assignee

Data saved locally in tasks.json and users.json

Clean, browser-based UI with light CSS styling

How to Run
1.Clone the repository
2.Navigate to the project folder
3.Create a virtual environment (optional but recommended):
python -m venv .venv
.venv\\Scripts\\activate   # Windows
source .venv/bin/activate  # macOS/Linux
4.Install dependencies:
pip install flask werkzeug
5. Start the Flask app:
python app.py
6.Open your browser and go to:
http://localhost:5000

Add feature list:
Different titles for members
Group chats or "boards" for certain groups or titles
Turn off/on scheduling systems
Reoccuring tasks