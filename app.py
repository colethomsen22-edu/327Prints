import os
import ssl
import json
import threading
import paho.mqtt.client as mqtt
import psycopg2
import subprocess
from flask import Flask, jsonify, request, render_template, Response
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# Load environment variables from the .env file
load_dotenv()

app = Flask(__name__)

# ==========================================
# BACKGROUND MQTT PRINTER CONNECTION
# ==========================================
latest_telemetry = {}

def start_mqtt_client():
    host = os.getenv('BAMBU_HOST')
    serial = os.getenv('BAMBU_SERIAL')
    access_code = os.getenv('BAMBU_ACCESS_CODE')

    if not host or not access_code:
        print("Printer credentials missing in .env. MQTT disabled.")
        return

    def on_connect(client, userdata, flags, rc):
        print("Connected to Bambu Printer MQTT!")
        client.subscribe(f"device/{serial}/report")

    def on_message(client, userdata, msg):
        global latest_telemetry
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            if 'print' in payload:
                print_data = payload['print']
                latest_telemetry = {
                    "gcode_state": print_data.get('gcode_state', 'IDLE'),
                    "mc_percent": print_data.get('mc_percent', 0),
                    "mc_remaining_time": print_data.get('mc_remaining_time', 0),
                    "nozzle_temper": print_data.get('nozzle_temper', 0),
                    "bed_temper": print_data.get('bed_temper', 0)
                }
        except Exception as e:
            pass # Ignore malformed packets silently to keep server running

    client = mqtt.Client()
    client.username_pw_set("bblp", access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(host, 8883, 60)
        client.loop_start() # Runs continuously in the background
    except Exception as e:
        print(f"Failed to connect to printer: {e}")

# Start the MQTT background thread once when Flask boots up
start_mqtt_client()

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.environ.get('DB_NAME'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASS'),
        host=os.environ.get('DB_HOST'),
        port=os.environ.get('DB_PORT')
    )
    return conn

def send_completion_email(to_email, first_name, job_id):
    api_key = os.getenv('SENDGRID_API_KEY')
    sender = os.getenv('SENDER_EMAIL', 'noreply@327prints.cnu.edu')

    if not api_key:
        print("SendGrid API key missing; email skipped.")
        return

    html_content = f"""
    <h2>Good news, {first_name}!</h2>
    <p>Your 3D print job <strong>#{job_id}</strong> has been marked as <strong>Completed</strong>.</p>
    <p>Please pick up your item from Luter 327 within 48 hours.</p>
    """

    message = Mail(
        from_email=sender,
        to_emails=to_email,
        subject=f"327Prints: Print Job #{job_id} Complete!",
        html_content=html_content
    )

    try:
        sg = SendGridAPIClient(api_key)
        sg.send(message)
        print(f"Email sent successfully to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")

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
# LIVE CAMERA FEED (FFMPEG SUBPROCESS)
# ==========================================
import subprocess

def generate_frames():
    bambu_host = os.getenv('BAMBU_HOST')
    access_code = os.getenv('BAMBU_ACCESS_CODE')

    if not bambu_host or not access_code:
        print("Missing printer credentials for camera feed.")
        return

    stream_url = f"rtsps://bblp:{access_code}@{bambu_host}:322/streaming/live/1"

# Use system FFmpeg directly
    command = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', stream_url,
        '-f', 'image2pipe',      # Output format: raw images
        '-vcodec', 'mjpeg',      # Encoded as JPEG
        '-'                      # Pipe to standard output
    ]

    # Let FFmpeg print errors directly to your terminal so we can diagnose them
    process = subprocess.Popen(command, stdout=subprocess.PIPE)

    buffer = b''
    while True:
        # Read chunks of the raw video stream
        chunk = process.stdout.read(8192)
        if not chunk:
            break
        buffer += chunk

        # Look for the hex byte markers indicating the start and end of a JPEG image
        start_idx = buffer.find(b'\xff\xd8')
        end_idx = buffer.find(b'\xff\xd9')

        if start_idx != -1 and end_idx != -1:
            # Extract the complete image frame
            jpg_frame = buffer[start_idx:end_idx+2]

            # Remove the processed frame from the buffer
            buffer = buffer[end_idx+2:]

            # Yield the frame to the Flask webpage
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpg_frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')
# ==========================================
# API ENDPOINTS (Backend Logic)
# ==========================================

@app.route('/api/printer/status', methods=['GET'])
def get_printer_status():
    global latest_telemetry

    # If the background thread hasn't caught a message yet, return a safe fallback
    if not latest_telemetry:
        return jsonify({"status": "success", "telemetry": {
            "gcode_state": "CONNECTING...",
            "mc_percent": 0,
            "mc_remaining_time": 0,
            "nozzle_temper": "--",
            "bed_temper": "--"
        }})

    return jsonify({"status": "success", "telemetry": latest_telemetry})

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

    # Update job status
    cursor.execute(
        'UPDATE Print_Job SET Current_status = %s WHERE Job_ID = %s;',
        (new_status, job_id)
    )

    # Fetch the user's email and first name for the notification
    cursor.execute('''
        SELECT u.Email_address, u.First_name
        FROM Print_Job p
        JOIN "User" u ON p.Requester_ID = u.User_ID
        WHERE p.Job_ID = %s;
    ''', (job_id,))

    user_info = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()

    # Fire the SendGrid email dispatch if the new status is Completed
    if new_status == 'Completed' and user_info:
        send_completion_email(user_info[0], user_info[1], job_id)

    return jsonify({"status": "success", "message": f"Job #{job_id} updated to {new_status}"}), 200

# ==========================================
# SERVER EXECUTION
# ==========================================

if __name__ == '__main__':
    # debug=True automatically restarts the server when you save changes
    app.run(debug=True)
