from flask import Flask, render_template, request, Response, jsonify, session
import requests, json, os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

sys_path = os.path.join(os.path.dirname(__file__), 'backend')
sys.path.insert(0, sys_path)
from mongo_db import (
    create_session, get_session, get_all_sessions,
    add_message, delete_session as mongo_delete_session,
    rename_session as mongo_rename_session,
    log_login, log_logout, log_upload, log_reaction, log_event
)

import google.generativeai as genai

genai.configure(api_key=os.getenv('GEMINI_API_KEY', ''))

SYSTEM_PROMPT = """You are Smart Fairness Analyzer, a specialized AI assistant for Fairness and Bias Testing in AI systems.
Help users identify, measure, and mitigate algorithmic bias using frameworks like Equalized Odds, Demographic Parity, and Counterfactual Fairness.
When analyzing data, always provide specific percentages and metrics where possible.
Always respond in the same language the user writes in."""

app = Flask(__name__)
app.secret_key = os.urandom(24)

DJANGO_URL = 'http://127.0.0.1:8000'
MESSAGE_LIMIT = 5

def init_session():
    if 'is_logged_in' not in session:
        session['is_logged_in'] = False
    if 'user_name' not in session:
        session['user_name'] = ''

@app.route('/')
def welcome():
    init_session()
    sessions = get_all_sessions()
    if sessions:
        return render_template('chat.html',
            sessions=sessions,
            active_id=sessions[0]['id'],
            is_logged_in=session['is_logged_in'],
            user_name=session['user_name'])
    return render_template('welcome.html')

@app.route('/chat')
def chat():
    init_session()
    sessions = get_all_sessions()
    active_id = request.args.get('id', sessions[0]['id'] if sessions else None)
    return render_template('chat.html',
        sessions=sessions,
        active_id=active_id,
        is_logged_in=session['is_logged_in'],
        user_name=session['user_name'])

@app.route('/api/new_session', methods=['POST'])
def new_session():
    init_session()
    all_sessions = get_all_sessions()
    total_user_msgs = sum(
        len([m for m in s['messages'] if m['role'] == 'user'])
        for s in all_sessions
    )
    if not session['is_logged_in'] and total_user_msgs >= MESSAGE_LIMIT:
        return jsonify({'error': 'limit_reached'}), 403
    new_s = create_session()
    return jsonify(new_s)

@app.route('/api/delete_session/<sid>', methods=['DELETE'])
def delete_session_route(sid):
    mongo_delete_session(sid)
    return jsonify({'ok': True})

@app.route('/api/stream', methods=['POST'])
def stream_message():
    init_session()
    data = request.json
    sid = data.get('session_id')
    text = data.get('text', '')
    history = data.get('history', [])
    is_logged_in = session.get('is_logged_in', False)

    all_sessions = get_all_sessions()
    total_user_msgs = sum(
        len([m for m in s['messages'] if m['role'] == 'user'])
        for s in all_sessions
    )
    if not is_logged_in and total_user_msgs >= MESSAGE_LIMIT:
        return jsonify({'error': 'limit_reached'}), 403

    def generate():
        try:
            resp = requests.post(
                f"{DJANGO_URL}/stream/",
                json={'message': text, 'history': history},
                headers={'Content-Type': 'application/json'},
                stream=True,
                timeout=120
            )
            for line in resp.iter_lines(chunk_size=1):
                if line:
                    decoded = line.decode('utf-8') if isinstance(line, bytes) else line
                    yield decoded + '\n\n'
        except Exception as e:
            yield f"data: {json.dumps({'token': f'Error: {str(e)}'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'full': f'Error: {str(e)}'})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/charts')
def charts():
    init_session()
    return render_template('charts.html',
        is_logged_in=session['is_logged_in'],
        user_name=session['user_name'])

@app.route('/api/stats')
def stats():
    from mongo_db import sessions_col, analyses_col
    import datetime
    # messages per day (last 14 days)
    pipeline_daily = [
        {'$unwind': '$messages'},
        {'$match': {'messages.role': 'user'}},
        {'$group': {
            '_id': {'$substr': ['$messages.timestamp', 0, 10]},
            'count': {'$sum': 1}
        }},
        {'$sort': {'_id': 1}},
        {'$limit': 14}
    ]
    daily = list(sessions_col.aggregate(pipeline_daily))

    # total counts
    total_sessions = sessions_col.count_documents({})
    total_msgs = sum(
        len([m for m in s.get('messages', []) if m['role'] == 'user'])
        for s in sessions_col.find({}, {'messages': 1})
    )

    # risk distribution from analyses
    risk_dist = {'LOW': 0, 'MEDIUM': 0, 'HIGH': 0}
    for s in sessions_col.find({}, {'messages': 1}):
        for m in s.get('messages', []):
            if m['role'] == 'model' and m.get('text'):
                t = m['text'].lower()
                bias_w = ['biased','unfair','discrimination','disparity','skewed']
                fair_w = ['unbiased','no bias','equitable','balanced','highly fair']
                bc = sum(1 for w in bias_w if w in t)
                fc = sum(1 for w in fair_w if w in t)
                if fc > bc: risk_dist['LOW'] += 1
                elif bc > fc: risk_dist['HIGH'] += 1
                else: risk_dist['MEDIUM'] += 1

    # top keywords across all bot messages
    from collections import Counter
    stop = {'the','a','an','and','or','but','in','on','at','to','for','of','with','is','are','was','were','be','been','has','have','had','it','this','that','as','by','from','not','can','will','would','should','could','may','might','do','does','did','its','their','they','we','you','i','he','she','if','so','than','then','when','which','who','what','how','all','any','each','more','also','about','into','such','these','those','there','here'}
    word_freq = Counter()
    import re
    for s in sessions_col.find({}, {'messages': 1}):
        for m in s.get('messages', []):
            if m['role'] == 'model':
                words = re.findall(r'\b[a-z]{4,}\b', m.get('text','').lower())
                word_freq.update(w for w in words if w not in stop)
    top_keywords = [{'word': w, 'count': c} for w, c in word_freq.most_common(10)]

    # recent sessions
    recent_sessions = []
    for s in sessions_col.find().sort('created_at', -1).limit(8):
        msgs = len([m for m in s.get('messages', []) if m['role'] == 'user'])
        recent_sessions.append({'title': s.get('title','Untitled')[:35], 'msgs': msgs})

    # recent events
    from mongo_db import events_col
    recent_events = []
    for e in events_col.find().sort('timestamp', -1).limit(20):
        ts = e.get('timestamp')
        time_str = ts.strftime('%H:%M') if hasattr(ts, 'strftime') else ''
        recent_events.append({'type': e.get('type',''), 'time': time_str})
    recent_events.reverse()

    return jsonify({
        'total_sessions': total_sessions,
        'total_messages': total_msgs,
        'daily': [{'date': d['_id'], 'count': d['count']} for d in daily],
        'risk_dist': risk_dist,
        'top_keywords': top_keywords,
        'recent_sessions': recent_sessions,
        'recent_events': recent_events
    })

@app.route('/api/rename_session', methods=['POST'])
def rename_session():
    data = request.json
    sid = data.get('session_id')
    title = data.get('title', '').strip()
    if sid and title:
        mongo_rename_session(sid, title)
    return jsonify({'ok': True})

@app.route('/api/reaction', methods=['POST'])
def reaction():
    data = request.json
    log_reaction(data.get('session_id'), data.get('type'))
    return jsonify({'ok': True})

@app.route('/api/log_upload', methods=['POST'])
def log_upload_route():
    data = request.json
    log_upload(data.get('session_id'), data.get('filename'), data.get('size', 0))
    return jsonify({'ok': True})

@app.route('/api/save_message', methods=['POST'])
def save_message():
    data = request.json
    sid = data.get('session_id')
    text = data.get('text', '')
    response_text = data.get('response', '')
    add_message(sid, text, response_text)
    return jsonify({'ok': True})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    name = data.get('name', '').strip()
    if name:
        session['is_logged_in'] = True
        session['user_name'] = name
        session.modified = True
        log_login(name)
        return jsonify({'ok': True, 'name': name})
    return jsonify({'error': 'Name required'}), 400

@app.route('/api/logout', methods=['POST'])
def logout():
    name = session.get('user_name', '')
    session['is_logged_in'] = False
    session['user_name'] = ''
    session.modified = True
    log_logout(name)
    return jsonify({'ok': True})

@app.route('/api/sessions')
def get_sessions():
    init_session()
    return jsonify({
        'sessions': get_all_sessions(),
        'is_logged_in': session['is_logged_in'],
        'user_name': session['user_name']
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=False, threaded=True)
