from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import openai
import os
from datetime import datetime
from dotenv import load_dotenv
import json
import sqlite3

load_dotenv()

app = Flask(__name__)

# Initialize clients
twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
openai.api_key = os.getenv('OPENAI_API_KEY')

# Database setup
def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect('voice_agent.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY, caller TEXT, message TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY, reminder TEXT, time_requested TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS call_logs
                 (id INTEGER PRIMARY KEY, caller TEXT, duration TEXT, timestamp TEXT, notes TEXT)''')
    conn.commit()
    conn.close()

init_db()

PERSONAL_ASSISTANT_SYSTEM_PROMPT = """You are a professional personal assistant voice agent. You answer on behalf of the user when they're unavailable.

Your responsibilities:
1. Greet callers politely and professionally
2. Ask who's calling and what they need
3. Take messages accurately
4. Handle simple tasks like:
   - Setting reminders
   - Logging appointments
   - Answering FAQs about the user
5. Offer to transfer to the user if urgent
6. Be helpful, professional, and concise

Keep responses short (1-2 sentences max) for natural voice conversation.
Be empathetic and professional.
When taking a message, confirm details before ending the call.
Never make promises on behalf of the user - only take messages."""

def store_message(caller, message, msg_type='message'):
    """Store message in database"""
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('INSERT INTO messages (caller, message, timestamp) VALUES (?, ?, ?)',
                  (caller, f"[{msg_type.upper()}] {message}", datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing message: {e}")
        return False

def store_reminder(reminder_text, time_requested):
    """Store reminder in database"""
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('INSERT INTO reminders (reminder, time_requested, timestamp) VALUES (?, ?, ?)',
                  (reminder_text, time_requested, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing reminder: {e}")
        return False

def get_ai_response(user_input, conversation_history=[]):
    """Get response from OpenAI"""
    try:
        messages = [{"role": "system", "content": PERSONAL_ASSISTANT_SYSTEM_PROMPT}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_input})
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=100,
            temperature=0.7
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        print(f"OpenAI error: {e}")
        return "I encountered an issue. Could you please repeat that?"

@app.route("/incoming-call", methods=['POST'])
def handle_incoming_call():
    """Initial greeting when call arrives"""
    caller_number = request.form.get('From', 'Unknown')
    
    # Log the call
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('INSERT INTO call_logs (caller, duration, timestamp, notes) VALUES (?, ?, ?, ?)',
                  (caller_number, '0', datetime.now().isoformat(), 'Incoming call'))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging call: {e}")
    
    response = VoiceResponse()
    
    # Initial greeting
    response.say("Hello! This is the personal assistant. How can I help you today?", 
                 voice='alice')
    
    # Gather initial input
    response.gather(
        input='speech',
        timeout=5,
        speechTimeout='auto',
        action='/process-input',
        method='POST'
    )
    
    return str(response)

@app.route("/process-input", methods=['POST'])
def process_input():
    """Process caller input with AI"""
    caller_input = request.form.get('SpeechResult', 'no input')
    caller_number = request.form.get('From', 'Unknown')
    
    # Store initial interaction
    store_message(caller_number, caller_input, 'caller_input')
    
    # Get AI response
    ai_response = get_ai_response(caller_input)
    
    response = VoiceResponse()
    response.say(ai_response, voice='alice')
    
    # Continue conversation with gather
    response.gather(
        input='speech',
        timeout=5,
        speechTimeout='auto',
        action='/process-follow-up',
        method='POST'
    )
    
    return str(response)

@app.route("/process-follow-up", methods=['POST'])
def process_follow_up():
    """Handle follow-up conversation"""
    caller_input = request.form.get('SpeechResult', '').lower()
    caller_number = request.form.get('From', 'Unknown')
    full_input = request.form.get('SpeechResult', '')
    
    # Check for specific commands
    if 'remind' in caller_input or 'reminder' in caller_input:
        response = VoiceResponse()
        response.say("I'll set a reminder. What should I remind you about?")
        response.gather(
            input='speech',
            timeout=5,
            action='/save-reminder',
            method='POST'
        )
        return str(response)
    
    elif 'message' in caller_input or 'tell' in caller_input or 'say' in caller_input:
        response = VoiceResponse()
        response.say("I'll take a message. Please go ahead.")
        response.gather(
            input='speech',
            timeout=8,
            action='/save-message',
            method='POST'
        )
        return str(response)
    
    elif 'urgent' in caller_input or 'emergency' in caller_input or 'transfer' in caller_input:
        return transfer_call(caller_number)
    
    elif 'goodbye' in caller_input or 'bye' in caller_input or 'thank' in caller_input:
        response = VoiceResponse()
        response.say("Thank you for calling. Goodbye!")
        return str(response)
    
    else:
        # General conversation
        ai_response = get_ai_response(full_input)
        
        response = VoiceResponse()
        response.say(ai_response, voice='alice')
        response.gather(
            input='speech',
            timeout=5,
            action='/process-follow-up',
            method='POST'
        )
        
        return str(response)

@app.route("/save-reminder", methods=['POST'])
def save_reminder():
    """Save reminder to database"""
    reminder_text = request.form.get('SpeechResult', 'No reminder text')
    
    store_reminder(reminder_text, datetime.now().isoformat())
    
    response = VoiceResponse()
    response.say("Reminder set. Is there anything else I can help with?")
    response.gather(
        input='speech',
        timeout=5,
        action='/process-follow-up',
        method='POST'
    )
    
    return str(response)

@app.route("/save-message", methods=['POST'])
def save_message():
    """Save message to database"""
    message_text = request.form.get('SpeechResult', 'No message')
    caller_number = request.form.get('From', 'Unknown')
    
    store_message(caller_number, message_text, 'message')
    
    response = VoiceResponse()
    response.say("Message saved. Is there anything else?")
    response.gather(
        input='speech',
        timeout=5,
        action='/process-follow-up',
        method='POST'
    )
    
    return str(response)

def transfer_call(caller_number):
    """Transfer call to user"""
    your_number = os.getenv('YOUR_PHONE_NUMBER')
    
    if not your_number:
        response = VoiceResponse()
        response.say("I'm unable to transfer right now. Please leave a message.")
        response.gather(
            input='speech',
            timeout=8,
            action='/save-message',
            method='POST'
        )
        return str(response)
    
    response = VoiceResponse()
    response.say("Transferring you now. Please hold.")
    
    response.dial(your_number)
    
    return str(response)

@app.route("/messages", methods=['GET'])
def get_messages():
    """Retrieve all messages"""
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('SELECT * FROM messages ORDER BY timestamp DESC')
        messages = c.fetchall()
        conn.close()
        
        return json.dumps({
            'messages': [
                {'id': m[0], 'caller': m[1], 'message': m[2], 'timestamp': m[3]}
                for m in messages
            ]
        })
    except Exception as e:
        return json.dumps({'error': str(e)})

@app.route("/reminders", methods=['GET'])
def get_reminders():
    """Retrieve all reminders"""
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('SELECT * FROM reminders ORDER BY timestamp DESC')
        reminders = c.fetchall()
        conn.close()
        
        return json.dumps({
            'reminders': [
                {'id': r[0], 'reminder': r[1], 'time_requested': r[2], 'timestamp': r[3]}
                for r in reminders
            ]
        })
    except Exception as e:
        return json.dumps({'error': str(e)})

@app.route("/call-logs", methods=['GET'])
def get_call_logs():
    """Retrieve all call logs"""
    try:
        conn = sqlite3.connect('voice_agent.db')
        c = conn.cursor()
        c.execute('SELECT * FROM call_logs ORDER BY timestamp DESC LIMIT 50')
        logs = c.fetchall()
        conn.close()
        
        return json.dumps({
            'call_logs': [
                {'id': l[0], 'caller': l[1], 'duration': l[2], 'timestamp': l[3], 'notes': l[4]}
                for l in logs
            ]
        })
    except Exception as e:
        return json.dumps({'error': str(e)})

@app.route("/health", methods=['GET'])
def health():
    """Health check endpoint"""
    return json.dumps({'status': 'healthy'})

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
