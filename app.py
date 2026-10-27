import os
import psycopg2
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

app = Flask(__name__)

# Helper function to get a database connection
def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.environ.get('DB_NAME'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASS'),
        host=os.environ.get('DB_HOST'),
        port=os.environ.get('DB_PORT')
    )
    return conn

# ==========================================
# HTML ROUTING (User Interface)
# ==========================================

@app.route('/')
def public_queue():
    # Renders the Public Queue Dashboard
    return render_template('index.html')

@app.route('/intake')
def intake_view():
    # Renders the Intake Form
    return render_template('intake.html')

@app.route('/operator')
def operator_dashboard():
    # Renders the Operator Control Dashboard
    return render_template('operator.html')

@app.route('/email-preview/<int:job_id>')
def email_preview(job_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch the specific job and the associated user's name
    cursor.execute('''
        SELECT p.Job_ID, u.First_name, u.Last_name 
        FROM Print_Job p
        JOIN "User" u ON p.Requester_ID = u.User_ID
        WHERE p.Job_ID = %s;
    ''', (job_id,))
    
    job = cursor.fetchone()
    cursor.close()
    conn.close()

    # If someone types an invalid ID, show an error
    if job is None:
        return "Job not found in database", 404

    # Pass the database variables to the HTML template
    return render_template('email_preview.html', job_id=job[0], first_name=job[1], last_name=job[2])

# ==========================================
# API ENDPOINTS (Backend Logic)
# ==========================================

@app.route('/test-db')
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT version();')
        db_version = cursor.fetchone()
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "db_version": db_version[0]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/create', methods=['POST'])
def create_user():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'INSERT INTO "User" (First_name, Last_name, Email_address) VALUES (%s, %s, %s) RETURNING User_ID;',
        (data['first_name'], data['last_name'], data['email'])
    )
    new_user_id = cursor.fetchone()[0]
    conn.commit()

    cursor.close()
    conn.close()
    return jsonify({"status": "success", "User_ID": new_user_id}), 201

@app.route('/api/jobs/submit', methods=['POST'])
def submit_job():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Inserts a new job with a 'Pending' status
    cursor.execute(
        'INSERT INTO Print_Job (Current_status, Requester_ID) VALUES (%s, %s) RETURNING Job_ID;',
        ('Pending', data['requester_id'])
    )
    new_job_id = cursor.fetchone()[0]
    conn.commit()

    cursor.close()
    conn.close()
    return jsonify({"status": "success", "Job_ID": new_job_id}), 201

@app.route('/api/jobs/queue', methods=['GET'])
def get_queue():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetches jobs and joins the User table (getting all data HTML needs)
    cursor.execute('''
        SELECT p.Job_ID, p.Queue_position, p.Current_status, p.Time_remaining, u.First_name, u.Last_name  
        FROM Print_Job p
        JOIN "User" u ON p.Requester_ID = u.User_ID
        ORDER BY p.Job_ID ASC;
    ''')
    jobs = cursor.fetchall()

    cursor.close()
    conn.close()

    # Format the SQL results into the exact JSON list our frontend JS expects
    queue = []
    for job in jobs:
        queue.append({
            "Job_ID": job[0],
            "Queue_position": job[1] if job[1] is not None else '-',
            "Current_status": job[2],
            "Time_remaining": job[3] if job[3] is not None else 0,
            "First_name": job[4],
            "Last_name": job[5]
        })
        
    return jsonify(queue)

@app.route('/api/jobs/update-status', methods=['POST'])
def update_job_status():
    data = request.get_json()
    job_id = data.get('job_id')
    new_status = data.get('status')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'UPDATE Print_Job SET Current_status = %s WHERE Job_ID = %s;',
        (new_status, job_id)
    )
    conn.commit()

    cursor.close()
    conn.close()
    return jsonify({"status": "success", "message": f"Job #{job_id} updated to {new_status}"}), 200

# ==========================================
# SERVER EXECUTION
# ==========================================

if __name__ == '__main__':
    # debug=True automatically restarts the server when you save changes
    app.run(debug=True)
