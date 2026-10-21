import os
import psycopg2
from flask import Flask, jsonify, request
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

@app.route('/')
def home():
    return "327Prints Server is running!"

@app.route('/test-db')
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query to verify the connection is active
        cursor.execute('SELECT version();')
        db_version = cursor.fetchone()

        # Close the connection
        cursor.close()
        conn.close()

        return jsonify({"status": "success", "db_version": db_version[0]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run()
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

    # Fetches all jobs and joins the User table to get the requester's name
    cursor.execute('''
        SELECT p.Job_ID, p.Current_status, u.First_name, u.Last_name 
        FROM Print_Job p
        JOIN "User" u ON p.Requester_ID = u.User_ID
        ORDER BY p.Job_ID ASC;
    ''')
    jobs = cursor.fetchall()

    cursor.close()
    conn.close()

    # Format the SQL results into a JSON list
    queue = [{"Job_ID": job[0], "Status": job[1], "Requester": f"{job[2]} {job[3]}"} for job in jobs]
    return jsonify({"queue": queue})
