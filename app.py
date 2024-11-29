from flask import Flask, jsonify, request, send_from_directory
from aws.cognito_utils import create_user_pool, create_app_client, get_user_pool_id, get_client_id
from aws.dynamodb_utils import create_appointments_table, put_appointment, update_appointment_status
from aws.s3_utils import get_s3_client, create_bucket, upload_car_image, configure_bucket_cors
from aws.sns_utils import send_notification
from aws.lambda_utils import invoke_lambda_function
import boto3
import uuid
import json
from datetime import datetime
from functools import wraps
from autocare_utils.validators import AppointmentValidator
import os

app = Flask(__name__, static_folder='frontend', static_url_path='')

# AWS Configuration
REGION = 'us-east-1'
USER_POOL_ID = None
CLIENT_ID = None
BUCKET_NAME = 'autocare-images1-' + str(uuid.uuid4())
PORT = 5555
SNS_TOPIC_ARN = None
APPOINTMENTS_TABLE = 'Appointments'

def init_aws_services():
    global USER_POOL_ID, CLIENT_ID, APPOINTMENTS_TABLE, SNS_TOPIC_ARN
    
    try:
        USER_POOL_ID = get_user_pool_id()
        CLIENT_ID = get_client_id()
        
        if not USER_POOL_ID or not CLIENT_ID:
            print("Failed to initialize Cognito: USER_POOL_ID or CLIENT_ID is None")
            return False
            
        print(f"Initialized with User Pool ID: {USER_POOL_ID}")
        print(f"Initialized with Client ID: {CLIENT_ID}")
        
        # Initialize S3 first
        s3_client = get_s3_client(REGION)
        if not s3_client:
            print("Failed to initialize S3 client")
            return False
        
        bucket = create_bucket(s3_client, BUCKET_NAME, REGION)
        if not bucket:
            print("Failed to create S3 bucket")
            return False
            
        # Configure CORS for the bucket
        if not configure_bucket_cors(s3_client, BUCKET_NAME):
            print("Failed to configure CORS for S3 bucket")
            return False
        
        print(f"Successfully created/verified bucket: {BUCKET_NAME}")
        
        # Initialize DynamoDB
        try:
            APPOINTMENTS_TABLE = create_appointments_table()
        except Exception as e:
            print(f"Error initializing DynamoDB: {str(e)}")
            return False
        
        # Initialize SNS
        sns_client = boto3.client('sns', region_name=REGION)
        response = sns_client.create_topic(Name='appointment-notifications')
        SNS_TOPIC_ARN = response['TopicArn']
        
        # Subscribe the user's email when they create an appointment
        print(f"Successfully created SNS topic: {SNS_TOPIC_ARN}")
        
        return True
    except Exception as e:
        print(f"Error initializing AWS services: {str(e)}")
        return False

# Call initialization before the first request
@app.before_request
def initialize():
    if not init_aws_services():
        print("WARNING: Failed to initialize AWS services")

# Authentication decorator
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'error': 'No authorization header'}), 401
        
        # Verify token with Cognito
        try:
            cognito = boto3.client('cognito-idp', region_name=REGION)
            response = cognito.get_user(AccessToken=auth_header)
            return f(*args, **kwargs, user=response)
        except Exception as e:
            return jsonify({'error': 'Invalid token'}), 401
    
    return decorated

# Routes
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    try:
        print("Received signup request")
        print("Request headers:", dict(request.headers))
        print("Request data:", request.get_data())
        
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
            
        data = request.get_json()
        print("Parsed JSON data:", data)
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        if not data.get('email'):
            return jsonify({'error': 'Email is required'}), 400
            
        if not data.get('password'):
            return jsonify({'error': 'Password is required'}), 400
            
        if len(data['password']) < 8:
            return jsonify({'error': 'Password must be at least 8 characters long'}), 400
            
        cognito = boto3.client('cognito-idp', region_name=REGION)
        
        try:
            # Sign up the user
            response = cognito.sign_up(
                ClientId=CLIENT_ID,
                Username=data['email'],
                Password=data['password'],
                UserAttributes=[
                    {'Name': 'email', 'Value': data['email']}
                ]
            )
            
            # Auto confirm the user (for testing only - remove in production)
            cognito.admin_confirm_sign_up(
                UserPoolId=USER_POOL_ID,
                Username=data['email']
            )
            
            print(f"Signup response: {response}")
            return jsonify({'message': 'User registered and confirmed successfully'}), 201
            
        except cognito.exceptions.UsernameExistsException:
            return jsonify({'error': 'User already exists'}), 400
        except cognito.exceptions.InvalidPasswordException as e:
            return jsonify({'error': str(e)}), 400
        except cognito.exceptions.InvalidParameterException as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            print(f"Cognito signup error: {str(e)}")
            return jsonify({'error': str(e)}), 400
            
    except Exception as e:
        print(f"General signup error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.json
        if not data or 'email' not in data or 'password' not in data:
            return jsonify({'error': 'Email and password are required'}), 400

        cognito = boto3.client('cognito-idp', region_name=REGION)
        
        try:
            response = cognito.initiate_auth(
                ClientId=CLIENT_ID,
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={
                    'USERNAME': data['email'],
                    'PASSWORD': data['password']
                }
            )
            
            return jsonify({
                'token': response['AuthenticationResult']['AccessToken'],
                'user': {'email': data['email']}
            })
        except cognito.exceptions.UserNotFoundException:
            return jsonify({'error': 'User not found. Please sign up first.'}), 404
        except cognito.exceptions.NotAuthorizedException:
            return jsonify({'error': 'Incorrect username or password'}), 401
        except cognito.exceptions.UserNotConfirmedException:
            return jsonify({'error': 'Please verify your email before logging in'}), 403
        except Exception as e:
            print(f"Cognito login error: {str(e)}")
            return jsonify({'error': str(e)}), 401
            
    except Exception as e:
        print(f"General login error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/auth/logout', methods=['POST'])
@require_auth
def logout(user):
    try:
        cognito = boto3.client('cognito-idp', region_name=REGION)
        auth_header = request.headers.get('Authorization')
        cognito.global_sign_out(AccessToken=auth_header)
        return jsonify({'message': 'Logged out successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/upload-url', methods=['POST'])
@require_auth
def get_upload_url(user):
    try:
        s3_client = get_s3_client(REGION)
        if not s3_client:
            return jsonify({'error': 'S3 client initialization failed'}), 500
            
        data = request.json
        file_name = f"{uuid.uuid4()}-{data['fileName']}"
        
        # Verify bucket exists
        try:
            s3_client.head_bucket(Bucket=BUCKET_NAME)
        except Exception as e:
            return jsonify({'error': f'S3 bucket not found: {str(e)}'}), 500
            
        url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': BUCKET_NAME,
                'Key': file_name,
                'ContentType': data['fileType'],
                'ACL': 'public-read'
            },
            ExpiresIn=3600
        )
        
        # Use the correct S3 URL format
        image_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{file_name}"
        
        return jsonify({
            'uploadUrl': url,
            'imageUrl': image_url
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/appointments', methods=['GET'])
@require_auth
def get_appointments(user):
    try:
        dynamodb = boto3.resource('dynamodb', region_name=REGION)
        table = dynamodb.Table('Appointments')
        
        # Query using the GSI
        response = table.query(
            IndexName='UserEmailIndex',
            KeyConditionExpression='userEmail = :email',
            ExpressionAttributeValues={
                ':email': user['Username']
            }
        )
        
        appointments = response.get('Items', [])
        # Sort appointments by date and time
        appointments.sort(key=lambda x: (x['date'], x['time']))
        
        return jsonify(appointments)
    except Exception as e:
        print(f"Error fetching appointments: {str(e)}")
        return jsonify({'error': str(e)}), 400

# Add this function for appointment validation
def validate_appointment(appointment_data):
    try:
        # Validate car information
        car_valid, car_message = AppointmentValidator.validate_car_info(
            appointment_data['carMake'],
            appointment_data['carModel'],
            appointment_data['carYear']
        )
        if not car_valid:
            return {'isValid': False, 'message': car_message}

        # Validate appointment time
        time_valid, time_message = AppointmentValidator.validate_appointment_time(
            appointment_data['date'],
            appointment_data['time']
        )
        if not time_valid:
            return {'isValid': False, 'message': time_message}

        # Validate service type
        service_valid, service_message = AppointmentValidator.validate_service_type(
            appointment_data['serviceType']
        )
        if not service_valid:
            return {'isValid': False, 'message': service_message}

        return {'isValid': True}
    except Exception as e:
        return {
            'isValid': False,
            'message': f'Validation error: {str(e)}'
        }

# Add this new route to handle SNS confirmation
@app.route('/api/confirm-appointment/<appointment_id>', methods=['GET', 'POST'])
def confirm_appointment(appointment_id):
    try:
        print(f"Starting confirmation process for appointment: {appointment_id}")  # Debug log
        print(f"Request method: {request.method}")  # Debug log
        
        # Update status using the new function
        appointment = update_appointment_status(appointment_id, 'Confirmed')
        
        if not appointment:
            print(f"No appointment found with ID: {appointment_id}")  # Debug log
            return jsonify({'error': 'Appointment not found'}), 404
            
        print(f"Successfully updated appointment: {appointment}")  # Debug log
        
        if appointment.get('notificationPreference'):
            message = f"""
            Your appointment has been confirmed!
            
            Service: {appointment['serviceType']}
            Date: {appointment['date']}
            Time: {appointment['time']}
            Vehicle: {appointment['carYear']} {appointment['carMake']} {appointment['carModel']}
            
            Thank you for choosing our service.
            """
            
            send_notification(
                SNS_TOPIC_ARN,
                message,
                'Appointment Confirmed'
            )
        
        # If it's a GET request, return a simple HTML response
        if request.method == 'GET':
            return """
            <html>
                <body>
                    <h1>Appointment Confirmed!</h1>
                    <p>Your appointment has been successfully confirmed.</p>
                </body>
            </html>
            """
            
        return jsonify({
            'message': 'Appointment confirmed successfully', 
            'appointment': appointment
        }), 200
        
    except Exception as e:
        print(f"Error confirming appointment: {str(e)}")  # Debug log
        return jsonify({'error': str(e)}), 400

# Add this new route to handle SNS notifications
@app.route('/api/sns-notification', methods=['POST'])
def handle_sns_notification():
    try:
        # Parse the SNS message
        sns_message = json.loads(request.data)
        print(f"Received SNS message: {sns_message}")  # Debug log

        # Handle subscription confirmation
        if sns_message.get('Type') == 'SubscriptionConfirmation':
            subscription_url = sns_message.get('SubscribeURL')
            import urllib.request
            urllib.request.urlopen(subscription_url).read()
            print("SNS subscription confirmed")
            return jsonify({'message': 'Subscription confirmed'}), 200

        # Handle notification
        if sns_message.get('Type') == 'Notification':
            message = json.loads(sns_message.get('Message', '{}'))
            if message.get('event') == 'email_confirmed':
                appointment_id = message.get('appointment_id')
                if appointment_id:
                    # Update the appointment status
                    appointment = update_appointment_status(appointment_id, 'Confirmed')
                    print(f"Updated appointment status: {appointment}")
                    return jsonify({'message': 'Status updated successfully'}), 200

        return jsonify({'message': 'Notification processed'}), 200

    except Exception as e:
        print(f"Error handling SNS notification: {str(e)}")  # Debug log
        return jsonify({'error': str(e)}), 400

# Update the create_appointment function to include SNS notification
@app.route('/api/appointments', methods=['POST'])
@require_auth
def create_appointment(user):
    try:
        data = request.json
        appointment_id = str(uuid.uuid4())
        
        # Use the updated validation with all appointment data
        validation_result = validate_appointment(data)
        
        if not validation_result.get('isValid', False):
            return jsonify({'error': validation_result.get('message', 'Invalid appointment')}), 400
            
        # Store appointment in DynamoDB
        appointment_data = {
            'appointment_id': appointment_id,
            'userEmail': user['Username'],
            'carMake': data['carMake'],
            'carModel': data['carModel'],
            'carYear': data['carYear'],
            'serviceType': data['serviceType'],
            'date': data['date'],
            'time': data['time'],
            'description': data.get('description', ''),
            'imageUrl': data.get('imageUrl', ''),
            'status': 'Pending',
            'createdAt': datetime.utcnow().isoformat(),
            'notificationPreference': data.get('notificationPreference', True),
        }
        
        put_appointment(appointment_id, appointment_data)
        
        if appointment_data['notificationPreference']:
            try:
                sns_client = boto3.client('sns', region_name=REGION)
                
                # Create a message that includes the appointment ID
                message = {
                    'event': 'email_confirmation',
                    'appointment_id': appointment_id,
                    'details': f"""
                    Thank you for booking an appointment with AutoCare Service Manager!
                    
                    Appointment Details:
                    Service: {data['serviceType']}
                    Date: {data['date']}
                    Time: {data['time']}
                    Vehicle: {data['carYear']} {data['carMake']} {data['carModel']}
                    
                    Please confirm your email to confirm the appointment.
                    """
                }
                
                # Subscribe to SNS topic with HTTP endpoint for confirmation
                sns_client.subscribe(
                    TopicArn=SNS_TOPIC_ARN,
                    Protocol='email',
                    Endpoint=user['Username'],
                    Attributes={
                        'FilterPolicy': json.dumps({
                            'event': ['email_confirmation']
                        })
                    }
                )
                
                # Send the notification
                send_notification(
                    SNS_TOPIC_ARN,
                    json.dumps(message),
                    'Appointment Confirmation Required'
                )
                
            except Exception as e:
                print(f"Error setting up SNS notification: {str(e)}")
                
        return jsonify(appointment_data), 201
        
    except Exception as e:
        print(f"Error creating appointment: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/<path:path>')
def serve_static_files(path):
    return send_from_directory(app.static_folder, path)

# Example of sending notification when appointment status changes
def update_appointment_status(appointment_id, new_status):
    try:
        dynamodb = boto3.resource('dynamodb', region_name=REGION)
        table = dynamodb.Table('Appointments')
        
        # Update the appointment status
        response = table.update_item(
            Key={'appointment_id': appointment_id},
            UpdateExpression='SET #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': new_status},
            ReturnValues='ALL_NEW'
        )
        
        # Send notification about status change
        appointment = response['Attributes']
        if appointment.get('notificationPreference'):
            message = f"""
            Your appointment status has been updated.
            
            New Status: {new_status}
            Service: {appointment['serviceType']}
            Date: {appointment['date']}
            Time: {appointment['time']}
            """
            
            send_notification(
                SNS_TOPIC_ARN,
                message,
                f'Appointment Status Update: {new_status}'
            )
            
        return response['Attributes']
    except Exception as e:
        print(f"Error updating appointment status: {str(e)}")
        raise e

if __name__ == '__main__':
    # Update for production
    port = int(os.environ.get('PORT', 5555))
    app.run(host='0.0.0.0', port=port)
