import os
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

import pandas as pd
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

database_url = os.environ.get("DATABASE_URL", "").strip()
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
# Force SQLAlchemy to use the installed psycopg2 driver on Render.
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)

if database_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///faculty_guide.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

db = SQLAlchemy(app)

UPLOAD_DIR = base_upload = os.path.join(app.instance_path, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reg_no = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    allocation = db.relationship("Allocation", back_populates="student", uselist=False,
                                  cascade="all, delete-orphan")


class Faculty(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    faculty_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    role = db.Column(db.String(150), nullable=True)
    specialization = db.Column(db.String(200), nullable=True)
    max_students = db.Column(db.Integer, nullable=False, default=5)
    status = db.Column(db.String(30), nullable=False, default="Active")
    allocations = db.relationship("Allocation", back_populates="faculty")


class Allocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False, unique=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey("faculty.id"), nullable=False)
    specialization = db.Column(db.String(200), nullable=True)
    allocated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    student = db.relationship("Student", back_populates="allocation")
    faculty = db.relationship("Faculty", back_populates="allocations")


class Specialization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)


class SelectionSettings(db.Model):
    """One persistent selection-round timer shared by every student/admin."""
    id = db.Column(db.Integer, primary_key=True, default=1)
    duration_seconds = db.Column(db.Integer, nullable=False, default=120)
    active = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)


def clean_value(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_columns(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_column(df, *names):
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def generate_faculty_id():
    existing = {f.faculty_id for f in Faculty.query.all()}
    i = 1
    while f"F{i:02d}" in existing:
        i += 1
    return f"F{i:02d}"


DEFAULT_SPECIALIZATIONS = [
    "Marketing", "Finance", "Human Resources (HR)", "Business Analytics",
    "Operations", "International Business", "Information Systems",
    "Entrepreneurship", "Supply Chain Management", "General Management"
]


def seed_excel_data():
    """Load bundled Excel files and create useful default specialization choices on first run."""
    if Student.query.count() == 0 and os.path.exists("student_data.xlsx"):
        import_students("student_data.xlsx")
    if Faculty.query.count() == 0 and os.path.exists("staff_data.xlsx"):
        import_faculty("staff_data.xlsx")
    existing_specs = {s.name.strip().lower() for s in Specialization.query.all()}
    source_specs = {
        f.specialization.strip()
        for f in Faculty.query.all()
        if f.specialization and f.specialization.strip()
    }
    for name in DEFAULT_SPECIALIZATIONS + sorted(source_specs):
        if name.strip().lower() not in existing_specs:
            db.session.add(Specialization(name=name.strip()))
            existing_specs.add(name.strip().lower())
    db.session.commit()


def import_students(path, replace=False):
    """Import student Excel data. In replace mode, the uploaded file becomes the full student list."""
    df = normalize_columns(pd.read_excel(path))
    reg_col = find_column(df, "Register Number", "Register No", "Reg No", "Registration Number")
    name_col = find_column(df, "Name", "Student Name")
    email_col = find_column(df, "Email", "Email ID", "E-mail")
    if not reg_col or not name_col:
        raise ValueError("Student Excel must contain Register Number and Name columns.")

    rows = []
    seen = set()
    skipped = 0
    for _, row in df.iterrows():
        reg_no = clean_value(row[reg_col])
        name = clean_value(row[name_col])
        email = clean_value(row[email_col]) if email_col else ""
        key = reg_no.lower()
        if not reg_no or not name or key in seen:
            skipped += 1
            continue
        seen.add(key)
        rows.append((reg_no, name, email))

    if replace:
        # Allocations belong to the old student set, so clear them before replacing.
        Allocation.query.delete(synchronize_session=False)
        Student.query.delete(synchronize_session=False)
        db.session.commit()
        for reg_no, name, email in rows:
            db.session.add(Student(reg_no=reg_no, name=name, email=email))
        db.session.commit()
        return len(rows), 0, skipped

    added = updated = 0
    for reg_no, name, email in rows:
        student = Student.query.filter(func.lower(Student.reg_no) == reg_no.lower()).first()
        if student:
            student.name = name
            student.email = email
            updated += 1
        else:
            db.session.add(Student(reg_no=reg_no, name=name, email=email))
            added += 1
    db.session.commit()
    return added, updated, skipped


def import_faculty(path, replace=False):
    """Import faculty Excel data. In replace mode, the uploaded file becomes the full faculty list."""
    df = normalize_columns(pd.read_excel(path))
    name_col = find_column(df, "Faculty Name", "Name", "Staff Name")
    email_col = find_column(df, "Email", "Email ID", "E-mail")
    role_col = find_column(df, "Role", "Designation")
    fid_col = find_column(df, "Faculty ID", "Faculty Id", "Staff ID")
    spec_col = find_column(df, "Specialization", "Specialisation")
    max_col = find_column(df, "Max Students", "Maximum Students", "Capacity")
    status_col = find_column(df, "Status")
    if not name_col:
        raise ValueError("Faculty Excel must contain Faculty Name (or Name).")

    rows = []
    seen = set()
    skipped = 0
    for _, row in df.iterrows():
        name = clean_value(row[name_col])
        if not name:
            skipped += 1
            continue
        email = clean_value(row[email_col]) if email_col else ""
        role = clean_value(row[role_col]) if role_col else ""
        fid = clean_value(row[fid_col]) if fid_col else ""
        spec = clean_value(row[spec_col]) if spec_col else ""
        raw_max = clean_value(row[max_col]) if max_col else ""
        raw_status = clean_value(row[status_col]) if status_col else ""
        try:
            max_students = int(float(raw_max)) if raw_max else 5
        except ValueError:
            max_students = 5
        if max_students < 1:
            max_students = 5
        status = raw_status or "Active"
        key = (fid.lower() if fid else name.lower())
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        rows.append((name, email, role, fid, spec, max_students, status))

    if replace:
        Allocation.query.delete(synchronize_session=False)
        Faculty.query.delete(synchronize_session=False)
        db.session.commit()
        for name, email, role, fid, spec, max_students, status in rows:
            db.session.add(Faculty(
                faculty_id=fid or generate_faculty_id(),
                faculty_name=name,
                email=email,
                role=role,
                specialization=spec or None,
                max_students=max_students,
                status=status
            ))
            if spec and not Specialization.query.filter(func.lower(Specialization.name) == spec.lower()).first():
                db.session.add(Specialization(name=spec))
        db.session.commit()
        return len(rows), 0, skipped

    added = updated = 0
    for name, email, role, fid, spec, max_students, status in rows:
        faculty = None
        if fid:
            faculty = Faculty.query.filter_by(faculty_id=fid).first()
        if not faculty and email:
            faculty = Faculty.query.filter(func.lower(Faculty.email) == email.lower()).first()
        if not faculty:
            faculty = Faculty.query.filter(func.lower(Faculty.faculty_name) == name.lower()).first()

        if faculty:
            assigned = Allocation.query.filter_by(faculty_id=faculty.id).count()
            faculty.faculty_name = name
            faculty.email = email
            faculty.role = role
            if spec:
                faculty.specialization = spec
            if max_students >= assigned:
                faculty.max_students = max_students
            faculty.status = status
            if fid:
                faculty.faculty_id = fid
            updated += 1
        else:
            db.session.add(Faculty(
                faculty_id=fid or generate_faculty_id(),
                faculty_name=name,
                email=email,
                role=role,
                specialization=spec or None,
                max_students=max_students,
                status=status
            ))
            added += 1
        if spec and not Specialization.query.filter(func.lower(Specialization.name) == spec.lower()).first():
            db.session.add(Specialization(name=spec))
    db.session.commit()
    return added, updated, skipped

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


@app.before_request
def initialize():
    if not hasattr(app, "_db_initialized"):
        with app.app_context():
            db.create_all()
            try:
                seed_excel_data()
            except Exception:
                db.session.rollback()
        app._db_initialized = True


def get_round_settings():
    settings = SelectionSettings.query.get(1)
    if not settings:
        settings = SelectionSettings(id=1, duration_seconds=120, active=False)
        db.session.add(settings)
        db.session.commit()
    return settings


def round_is_active():
    settings = get_round_settings()
    if not settings.active or not settings.ends_at:
        return False
    now = datetime.utcnow()
    if now >= settings.ends_at:
        if settings.active:
            settings.active = False
            db.session.commit()
        return False
    return True


@app.route("/")
def index():
    settings = get_round_settings()
    active = round_is_active()
    remaining = 0
    if active and settings.ends_at:
        remaining = max(0, int((settings.ends_at - datetime.utcnow()).total_seconds()))
    return render_template(
        "index.html",
        timer_active=active,
        remaining_seconds=remaining,
        duration_seconds=settings.duration_seconds
    )


@app.route("/api/round-status")
def round_status():
    settings = get_round_settings()
    active = round_is_active()
    remaining = 0
    if active and settings.ends_at:
        remaining = max(0, int((settings.ends_at - datetime.utcnow()).total_seconds()))
    return jsonify({
        "active": active,
        "remaining_seconds": remaining,
        "duration_seconds": settings.duration_seconds
    })


@app.route("/api/student/<path:reg_no>")
def lookup_student(reg_no):
    if not round_is_active():
        return jsonify({"valid": False, "round_active": False, "message": "Student selection has not been started by the administrator."})

    normalized = reg_no.strip()
    student = Student.query.filter(func.lower(Student.reg_no) == normalized.lower()).first()
    if not student:
        return jsonify({"valid": False, "round_active": True, "message": "Register number not found."})
    if student.allocation:
        return jsonify({
            "valid": True,
            "already_allocated": True,
            "name": student.name,
            "reg_no": student.reg_no,
            "faculty": student.allocation.faculty.faculty_name,
            "date_time": student.allocation.allocated_at.strftime("%d-%m-%Y %H:%M:%S")
        })
    return jsonify({"valid": True, "already_allocated": False, "name": student.name, "reg_no": student.reg_no, "email": student.email or ""})


@app.route("/specialization", methods=["GET", "POST"])
def specialization():
    if not round_is_active():
        session.pop("reg_no", None)
        session.pop("specialization", None)
        session.pop("selected_faculty_id", None)
        flash("Student selection is closed. Please wait for the administrator to start the round.", "error")
        return redirect(url_for("index"))

    # First POST from the register-number screen establishes the student session.
    # The second POST comes from this specialization form and must NOT require reg_no again.
    if request.method == "POST":
        posted_reg_no = request.form.get("reg_no", "").strip()
        if posted_reg_no:
            student = Student.query.filter(func.lower(Student.reg_no) == posted_reg_no.lower()).first()
            if not student:
                flash("Register number not found. Please enter a valid register number.", "error")
                return redirect(url_for("index"))
            if student.allocation:
                return render_template("already_allocated.html", student=student, allocation=student.allocation)
            session["reg_no"] = student.reg_no
            session.pop("specialization", None)
        else:
            # Specialization was submitted from the already-authenticated student session.
            reg_no = session.get("reg_no")
            student = Student.query.filter_by(reg_no=reg_no).first() if reg_no else None
            if not student:
                flash("Register number not found. Please start again.", "error")
                return redirect(url_for("index"))
            if student.allocation:
                return render_template("already_allocated.html", student=student, allocation=student.allocation)
            selected = request.form.get("specialization", "").strip()
            valid_spec = Specialization.query.filter(func.lower(Specialization.name) == selected.lower()).first()
            if not valid_spec:
                flash("Please select a specialization.", "error")
                return redirect(url_for("specialization"))
            session["specialization"] = valid_spec.name
            session.pop("selected_faculty_id", None)

    reg_no = session.get("reg_no")
    if not reg_no:
        return redirect(url_for("index"))
    student = Student.query.filter_by(reg_no=reg_no).first()
    if not student:
        session.pop("reg_no", None)
        return redirect(url_for("index"))
    if student.allocation:
        return render_template("already_allocated.html", student=student, allocation=student.allocation)

    specs = [s.name for s in Specialization.query.order_by(Specialization.name).all()]

    # Step 2 also shows ALL available guides after a specialization is chosen.
    # Guide availability is based only on status/capacity, never on specialization.
    specialization_name = session.get("specialization")
    faculties = []
    if specialization_name:
        for faculty in Faculty.query.filter_by(status="Active").order_by(Faculty.faculty_name).all():
            assigned = Allocation.query.filter_by(faculty_id=faculty.id).count()
            available = max(faculty.max_students - assigned, 0)
            if available > 0:
                faculties.append({
                    "faculty": faculty,
                    "assigned": assigned,
                    "available": available,
                })

    round_settings = get_round_settings()
    remaining_seconds = max(0, int((round_settings.ends_at - datetime.utcnow()).total_seconds())) if round_settings.ends_at else 0
    return render_template(
        "specialization.html",
        student=student,
        specializations=specs,
        selected_specialization=specialization_name,
        faculties=faculties,
        timer_active=round_is_active(),
        remaining_seconds=remaining_seconds
    )


@app.route("/faculty", methods=["GET"])
def faculty_selection():
    # Kept for backwards compatibility with older bookmarks/links.
    return redirect(url_for("specialization"))


@app.route("/review", methods=["POST"])
def review_allocation():
    """Show guide confirmation details on the same selection page; do not navigate to a separate page."""
    if not round_is_active():
        session.pop("reg_no", None)
        session.pop("specialization", None)
        session.pop("selected_faculty_id", None)
        flash("The selection timer has ended. Please wait for the next round.", "error")
        return redirect(url_for("index"))

    reg_no = session.get("reg_no")
    specialization_name = session.get("specialization")
    faculty_pk = request.form.get("faculty_id", type=int)
    if not reg_no or not specialization_name or not faculty_pk:
        flash("Invalid selection. Please start again.", "error")
        return redirect(url_for("index"))

    student = Student.query.filter_by(reg_no=reg_no).first()
    faculty = Faculty.query.get(faculty_pk)
    if not student or not faculty or faculty.status != "Active":
        flash("The selected guide is no longer available. Please select another guide.", "error")
        return redirect(url_for("specialization"))

    assigned = Allocation.query.filter_by(faculty_id=faculty.id).count()
    if assigned >= faculty.max_students:
        flash("This guide has reached maximum capacity. Please select another guide.", "error")
        return redirect(url_for("specialization"))

    session["selected_faculty_id"] = faculty.id
    settings = get_round_settings()
    remaining_seconds = max(0, int((settings.ends_at - datetime.utcnow()).total_seconds())) if settings.ends_at else 0
    specs = [s.name for s in Specialization.query.order_by(Specialization.name).all()]
    faculties = []
    for item_faculty in Faculty.query.filter_by(status="Active").order_by(Faculty.faculty_name).all():
        assigned_count = Allocation.query.filter_by(faculty_id=item_faculty.id).count()
        available = max(item_faculty.max_students - assigned_count, 0)
        if available > 0:
            faculties.append({"faculty": item_faculty, "assigned": assigned_count, "available": available})

    return render_template(
        "specialization.html",
        student=student,
        specializations=specs,
        selected_specialization=specialization_name,
        faculties=faculties,
        selected_faculty=faculty,
        show_confirmation=True,
        confirmation_time=datetime.utcnow().strftime("%d-%m-%Y %H:%M:%S"),
        saved_confirmation=False,
        timer_active=round_is_active(),
        remaining_seconds=remaining_seconds,
    )


@app.route("/confirm", methods=["POST"])
def confirm_allocation():
    if not round_is_active():
        session.pop("reg_no", None)
        session.pop("specialization", None)
        session.pop("selected_faculty_id", None)
        flash("The selection timer has ended. Your selection was not submitted.", "error")
        return redirect(url_for("index"))

    reg_no = session.get("reg_no")
    specialization_name = session.get("specialization")
    faculty_pk = request.form.get("faculty_id", type=int) or session.get("selected_faculty_id")

    if not reg_no or not specialization_name or not faculty_pk:
        flash("Invalid selection. Please start again.", "error")
        return redirect(url_for("index"))

    try:
        # A previous SELECT can autobegin a SQLAlchemy transaction. Roll it back
        # before explicitly starting the allocation transaction.
        db.session.rollback()
        with db.session.begin():
            student = db.session.execute(
                select(Student).where(func.lower(Student.reg_no) == reg_no.lower()).with_for_update()
            ).scalar_one_or_none()
            if not student:
                raise ValueError("Student not found.")
            if student.allocation:
                existing = student.allocation
                # The transaction remains valid; exit normally, then show the same page.
                existing_faculty = existing.faculty
                return render_template(
                    "specialization.html",
                    student=student,
                    specializations=[s.name for s in Specialization.query.order_by(Specialization.name).all()],
                    selected_specialization=existing.specialization,
                    faculties=[],
                    selected_faculty=existing_faculty,
                    show_confirmation=True,
                    confirmation_time=existing.allocated_at.strftime("%d-%m-%Y %H:%M:%S"),
                    saved_confirmation=True,
                    timer_active=round_is_active(),
                    remaining_seconds=0,
                )

            faculty = db.session.execute(
                select(Faculty).where(Faculty.id == faculty_pk).with_for_update()
            ).scalar_one_or_none()
            if not faculty or faculty.status != "Active":
                raise ValueError("This faculty is not currently available.")

            assigned = db.session.execute(
                select(func.count(Allocation.id)).where(Allocation.faculty_id == faculty.id)
            ).scalar_one()
            if assigned >= faculty.max_students:
                raise ValueError("This faculty just reached maximum capacity. Please select another faculty.")

            allocation = Allocation(
                student_id=student.id,
                faculty_id=faculty.id,
                specialization=specialization_name,
                allocated_at=datetime.utcnow()
            )
            db.session.add(allocation)

        # Keep the student on the same page after saving. Do not redirect to a
        # separate success page, and keep the saved allocation details visible.
        session.pop("selected_faculty_id", None)
        return render_template(
            "specialization.html",
            student=student,
            specializations=[s.name for s in Specialization.query.order_by(Specialization.name).all()],
            selected_specialization=specialization_name,
            faculties=[],
            selected_faculty=faculty,
            show_confirmation=True,
            confirmation_time=allocation.allocated_at.strftime("%d-%m-%Y %H:%M:%S"),
            saved_confirmation=True,
            timer_active=round_is_active(),
            remaining_seconds=0,
        )
    except IntegrityError:
        db.session.rollback()
        flash("This student has already been allocated. Please check the allocation status.", "error")
        return redirect(url_for("specialization"))
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("specialization"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = request.form.get("username", "")
        password = request.form.get("password", "")
        valid_user = os.environ.get("ADMIN_USER", "admin")
        valid_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        if user == valid_user and password == valid_password:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    total_students = Student.query.count()
    total_allocated = Allocation.query.count()
    total_faculty = Faculty.query.count()
    faculties = []
    for faculty in Faculty.query.order_by(Faculty.faculty_name).all():
        assigned = Allocation.query.filter_by(faculty_id=faculty.id).count()
        faculties.append({
            "faculty": faculty,
            "assigned": assigned,
            "available": max(faculty.max_students - assigned, 0)
        })
    specializations = Specialization.query.order_by(Specialization.name).all()
    students = Student.query.order_by(Student.reg_no).all()
    round_settings = get_round_settings()
    round_active = round_is_active()
    remaining_seconds = 0
    if round_active and round_settings.ends_at:
        remaining_seconds = max(0, int((round_settings.ends_at - datetime.utcnow()).total_seconds()))
    return render_template(
        "admin.html",
        total_students=total_students,
        total_allocated=total_allocated,
        students_remaining=max(total_students - total_allocated, 0),
        total_faculty=total_faculty,
        faculties=faculties,
        specializations=specializations,
        students=students,
        round_settings=round_settings,
        round_active=round_active,
        remaining_seconds=remaining_seconds
    )


@app.route("/admin/round/settings", methods=["POST"])
@admin_required
def save_round_settings():
    settings = get_round_settings()
    try:
        minutes = int(request.form.get("minutes", "2"))
        seconds = int(request.form.get("seconds", "0"))
        if minutes < 0 or seconds < 0 or seconds > 59:
            raise ValueError
        duration = minutes * 60 + seconds
        if duration < 10 or duration > 3600:
            flash("Timer must be between 10 seconds and 60 minutes.", "error")
            return redirect(url_for("admin_dashboard"))
        settings.duration_seconds = duration
        db.session.commit()
        flash(f"Timer saved as {duration // 60}:{duration % 60:02d}.", "success")
    except ValueError:
        flash("Enter a valid timer value.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/round/start", methods=["POST"])
@admin_required
def start_round():
    settings = get_round_settings()
    now = datetime.utcnow()
    settings.active = True
    settings.started_at = now
    settings.ends_at = now + timedelta(seconds=settings.duration_seconds)
    db.session.commit()
    flash(f"Student selection started for {settings.duration_seconds // 60}:{settings.duration_seconds % 60:02d}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/round/stop", methods=["POST"])
@admin_required
def stop_round():
    settings = get_round_settings()
    settings.active = False
    settings.ends_at = None
    db.session.commit()
    flash("Student selection has been stopped.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/student/add", methods=["POST"])
@admin_required
def add_student():
    reg_no = request.form.get("reg_no", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if not reg_no or not name:
        flash("Register number and student name are required.", "error")
        return redirect(url_for("admin_dashboard"))
    existing = Student.query.filter(func.lower(Student.reg_no) == reg_no.lower()).first()
    if existing:
        flash("A student with this register number already exists.", "error")
        return redirect(url_for("admin_dashboard"))
    db.session.add(Student(reg_no=reg_no, name=name, email=email))
    db.session.commit()
    flash(f"Student {name} added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/student/<int:student_id>/edit", methods=["POST"])
@admin_required
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)
    reg_no = request.form.get("reg_no", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    duplicate = Student.query.filter(
        func.lower(Student.reg_no) == reg_no.lower(),
        Student.id != student.id
    ).first()
    if not reg_no or not name:
        flash("Register number and student name are required.", "error")
    elif duplicate:
        flash("That register number is already used by another student.", "error")
    else:
        student.reg_no = reg_no
        student.name = name
        student.email = email
        db.session.commit()
        flash("Student updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/faculty/<int:faculty_pk>/save", methods=["POST"])
@admin_required
def save_faculty(faculty_pk):
    faculty = Faculty.query.get_or_404(faculty_pk)
    assigned = Allocation.query.filter_by(faculty_id=faculty.id).count()
    name = request.form.get("faculty_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "").strip()
    spec = request.form.get("specialization", "").strip()
    status = request.form.get("status", "Active").strip()
    max_students = request.form.get("max_students", type=int)

    if not name or not max_students or max_students < assigned:
        flash(f"Capacity cannot be below the {assigned} students already allocated.", "error")
        return redirect(url_for("admin_dashboard"))

    faculty.faculty_name = name
    faculty.email = email
    faculty.role = role
    faculty.specialization = spec or None
    faculty.status = "Active" if status == "Active" else "Inactive"
    faculty.max_students = max_students

    if spec and not Specialization.query.filter(func.lower(Specialization.name) == spec.lower()).first():
        db.session.add(Specialization(name=spec))
    db.session.commit()
    flash("Faculty updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/specialization/add", methods=["POST"])
@admin_required
def add_specialization():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Specialization name is required.", "error")
    elif Specialization.query.filter(func.lower(Specialization.name) == name.lower()).first():
        flash("Specialization already exists.", "error")
    else:
        db.session.add(Specialization(name=name))
        db.session.commit()
        flash("Specialization added.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/specialization/<int:spec_id>/edit", methods=["POST"])
@admin_required
def edit_specialization(spec_id):
    spec = Specialization.query.get_or_404(spec_id)
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("Specialization name is required.", "error")
        return redirect(url_for("admin_dashboard"))
    duplicate = Specialization.query.filter(func.lower(Specialization.name) == new_name.lower(), Specialization.id != spec.id).first()
    if duplicate:
        flash("That specialization already exists.", "error")
        return redirect(url_for("admin_dashboard"))
    old_name = spec.name
    spec.name = new_name
    for faculty in Faculty.query.filter(func.lower(Faculty.specialization) == old_name.lower()).all():
        faculty.specialization = new_name
    for allocation in Allocation.query.filter(func.lower(Allocation.specialization) == old_name.lower()).all():
        allocation.specialization = new_name
    db.session.commit()
    flash("Specialization updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/specialization/<int:spec_id>/delete", methods=["POST"])
@admin_required
def delete_specialization(spec_id):
    spec = Specialization.query.get_or_404(spec_id)
    used = Faculty.query.filter(func.lower(Faculty.specialization) == spec.name.lower()).count()
    if used:
        flash(f"Cannot delete {spec.name} because it is assigned to {used} faculty member(s). Edit the faculty specialization first.", "error")
        return redirect(url_for("admin_dashboard"))
    db.session.delete(spec)
    db.session.commit()
    flash("Specialization deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/import/students", methods=["POST"])
@admin_required
def refresh_students():
    file = request.files.get("student_file")
    if not file or not file.filename:
        flash("Please choose a student Excel file.", "error")
        return redirect(url_for("admin_dashboard"))
    path = os.path.join(UPLOAD_DIR, secure_filename(file.filename))
    file.save(path)
    try:
        added, updated, skipped = import_students(path, replace=True)
        total = Student.query.count()
        flash(f"Student data replaced successfully: {total} students loaded. {skipped} invalid/duplicate rows skipped. Previous student selections were cleared.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Student import failed: {exc}", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/import/faculty", methods=["POST"])
@admin_required
def refresh_faculty():
    file = request.files.get("faculty_file")
    if not file or not file.filename:
        flash("Please choose a faculty Excel file.", "error")
        return redirect(url_for("admin_dashboard"))
    path = os.path.join(UPLOAD_DIR, secure_filename(file.filename))
    file.save(path)
    try:
        added, updated, skipped = import_faculty(path, replace=True)
        total = Faculty.query.count()
        flash(f"Faculty data replaced successfully: {total} faculty loaded. {skipped} invalid/duplicate rows skipped. Previous student selections were cleared.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Faculty import failed: {exc}", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reset", methods=["POST"])
@admin_required
def reset_allocations():
    Allocation.query.delete(synchronize_session=False)
    db.session.commit()
    flash("All collected allocations have been reset successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/export")
@admin_required
def export_allocations():
    rows = []
    allocations = Allocation.query.order_by(Allocation.allocated_at).all()
    for a in allocations:
        rows.append({
            "Register Number": a.student.reg_no,
            "Student Name": a.student.name,
            "Email": a.student.email or "",
            "Specialization": a.specialization or "",
            "Faculty Name": a.faculty.faculty_name,
            "Faculty ID": a.faculty.faculty_id,
            "Allocated Date": a.allocated_at.strftime("%d-%m-%Y"),
            "Allocated Time": a.allocated_at.strftime("%H:%M:%S"),
        })
    df = pd.DataFrame(rows, columns=[
        "Register Number", "Student Name", "Email", "Specialization",
        "Faculty Name", "Faculty ID", "Allocated Date", "Allocated Time"
    ])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Allocations")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="faculty_allocations.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
