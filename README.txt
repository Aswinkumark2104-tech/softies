FACULTY GUIDE SELECTION SYSTEM
================================

Technology:
- Python
- Flask
- Flask-SQLAlchemy
- PostgreSQL online
- SQLite local fallback
- Pandas/OpenPyXL
- Gunicorn

LOCAL SETUP
-----------
1. Open Command Prompt in this project folder.
2. Create virtual environment:
   python -m venv venv
3. Activate:
   venv\Scripts\activate
4. Install:
   pip install -r requirements.txt
5. Run:
   python app.py
6. Open:
   http://127.0.0.1:5000

DEFAULT LOCAL ADMIN
-------------------
Username: admin
Password: admin123

For production, set:
ADMIN_USER
ADMIN_PASSWORD
SECRET_KEY
DATABASE_URL

GITHUB
------
Create a PRIVATE GitHub repository and upload the project.
Do not upload venv, .env, or the local SQLite database.

RENDER
------
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app

Use PostgreSQL and set:
DATABASE_URL
SECRET_KEY
ADMIN_USER
ADMIN_PASSWORD

STUDENT URL
-----------
Use the public Render URL.

ADMIN URL
---------
<your-render-url>/admin/login

EXCEL FILES
-----------
student_data.xlsx:
Register Number, Name, Email

staff_data.xlsx:
Faculty ID, Faculty Name, Email, Role, Specialization, Max Students, Status

Missing faculty columns are supported:
Faculty ID -> generated
Max Students -> 5
Status -> Active
Specialization -> blank

The database is updated rather than deleted during Excel refresh.
Existing allocations are preserved.

FCFS
----
Allocation is confirmed only after the student clicks CONFIRM FACULTY.
PostgreSQL row locking and database constraints protect the final seat.

STUDENT SELECTION TIMER
-----------------------
Admin > Student Selection Timer:
- Default timer is 2:00 (120 seconds).
- Admin can edit minutes and seconds.
- Admin clicks START STUDENT SELECTION to open the student form.
- All students see the same server-side countdown.
- When the timer reaches 0, student lookup, specialization, faculty selection and confirmation are blocked.
- STOP SELECTION immediately closes the round.
- The timer is stored in PostgreSQL/SQLite, so it is safe for Render deployment.

STUDENT MANAGEMENT
------------------
Admin > Student Management:
- Add a new student with Register Number, Name and Email.
- Edit existing student Register Number, Name and Email.
- Refresh Student Excel for bulk add/update.
- RESET STUDENT DATA / SELECTIONS deletes only faculty allocations. It does NOT delete the student master data.

IMPORTANT FIX FOR STUDENT FLOW
------------------------------
The Register Number page now POSTs the verified register number into the Flask session.
This fixes the previous issue where the student name appeared after lookup, but clicking OK caused the next page to lose the register number and show "Register number not found."

RESET
-----
Admin > RESET STUDENT DATA / SELECTIONS deletes allocation records only.
Students, faculty, specializations, capacity, status, timer settings and admin settings remain.

SPECIALIZATION MANAGEMENT
-------------------------
The admin dashboard contains an editable specialization master list.
Default choices include:
- Marketing
- Finance
- Human Resources (HR)
- Business Analytics
- Operations
- International Business
- Information Systems
- Entrepreneurship
- Supply Chain Management
- General Management

Admin can add, edit, or delete specializations. Faculty specialization is selected from this list.
If a specialization is changed, matching faculty/allocation records are updated to the new name.

EXCEL REFRESH BEHAVIOUR
-----------------------
Student Excel is an UPSERT import:
- Existing Register Number -> update the student's name/email.
- New Register Number -> add a new student.
- Existing students are NOT deleted when an Excel file is uploaded.

Example:
First upload: 30 students -> Dashboard Total Students = 30.
Later upload: same 30 + 10 new students -> Dashboard Total Students = 40.
Later uploads continue to add/update records in the same way.

Faculty Excel works the same way:
- Existing faculty is matched by Faculty ID, then email, then faculty name.
- New faculty is added.
- Existing faculty settings are updated from Excel when supplied.
- Existing allocations are preserved.

IMPORTANT:
If you upload an Excel file containing fewer rows, the missing old records are intentionally NOT deleted. This protects the master data and previous allocations. Use the Reset Collected Data button only when you want to clear allocations for a new selection round.


UPDATED FEATURES - 07 OCTOBER 2026
----------------------------------
1. Admin Excel replacement:
   - Student Excel upload now replaces the current student list.
   - Faculty Excel upload now replaces the current faculty list.
   - Example: 20 students uploaded -> dashboard shows 20.
     Upload a new file with 40 students -> dashboard shows 40.
   - Replacing student/faculty data clears old allocations so the new round starts cleanly.

2. Admin reset:
   - RESET STUDENT DATA / SELECTIONS clears all collected student-to-faculty allocations while keeping the current master student/faculty lists.

3. Admin-controlled timer:
   - Admin sets minutes and seconds.
   - START STUDENT SELECTION starts one shared server-side countdown.
   - Student register page, specialization page, and faculty page display the same timer.
   - When the timer ends or admin stops the round, student pages return to the start page.
   - Server-side checks prevent submissions after the timer ends.

Recommended Render environment variables:
   ADMIN_USER=your_admin_username
   ADMIN_PASSWORD=your_strong_password
   SECRET_KEY=your_random_secret
   DATABASE_URL=your_postgresql_connection_string
