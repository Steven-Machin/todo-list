To-Do List (Team Task Manager - Flask Web App)
This is a Flask-based web application for managing and delegating tasks within a small team. It supports multiple users with different roles (manager or member) and allows managers to assign tasks, while team members can view and complete only their assigned items.

Tasks and users are now managed through SQLAlchemy with PostgreSQL support. If a DATABASE_URL environment variable is not provided the app falls back to a local SQLite database for development.

Features
- User signup/login system
- Role-based access for managers vs. members
- Create tasks with priority, due date, and notes
- Mark tasks as complete/incomplete
- Sort tasks by due date, priority, creation date, or status
- Filter tasks by assignee
- Clean, browser-based UI with light CSS styling

How to Run
1. Clone the repository
2. Navigate to the project folder
3. Create a virtual environment (optional but recommended):
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   source .venv/bin/activate     # macOS/Linux
4. Install dependencies:
   pip install -r requirements.txt
5. Configure DATABASE_URL to point at your PostgreSQL instance (e.g. postgresql+psycopg://user:pass@localhost:5432/todo). Leaving it unset will use the SQLite fallback stored in data/todo.db.
6. Start the Flask app:
   python app.py
7. Open your browser and go to:
   http://localhost:5000

Add feature list:
- Different titles for members
- Group chats or "boards" for certain groups or titles
- Turn off/on scheduling systems
- Recurring tasks
