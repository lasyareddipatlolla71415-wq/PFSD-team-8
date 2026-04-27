from pymongo import MongoClient
from bson import ObjectId
import datetime
import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except Exception:
    pass

MONGO_URI = os.getenv('MONGO_URI', 'mongodb+srv://2410030226:lasya2007@cluster0.znwsg7u.mongodb.net/fairness_analyzer?retryWrites=true&w=majority')
DB_NAME   = os.getenv('DB_NAME', 'fairness_analyzer')

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

sessions_col  = db['chat_sessions']
analyses_col  = db['bias_analyses']
datasets_col  = db['datasets']
users_col     = db['users']
events_col    = db['app_events']      # tracks every action
reactions_col = db['message_reactions']
uploads_col   = db['file_uploads']


# ── Event Logger ───────────────────────────────────────────────

def log_event(event_type, data=None):
    """Log every app action to app_events collection."""
    events_col.insert_one({
        'type': event_type,
        'data': data or {},
        'timestamp': datetime.datetime.utcnow()
    })


# ── Chat Sessions ──────────────────────────────────────────────

def create_session(title='New Analysis'):
    doc = {
        'title': title,
        'messages': [{
            'id': 'initial',
            'role': 'model',
            'text': 'Smart Fairness Analyzer Initialized. I am ready to analyze your scenarios for potential bias and fairness violations.',
            'timestamp': datetime.datetime.utcnow().isoformat()
        }],
        'created_at': datetime.datetime.utcnow(),
        'message_count': 0,
        'last_active': datetime.datetime.utcnow()
    }
    result = sessions_col.insert_one(doc)
    doc['_id'] = str(result.inserted_id)
    log_event('session_created', {'session_id': str(result.inserted_id), 'title': title})
    return _serialize(doc)

def get_session(sid):
    try:
        doc = sessions_col.find_one({'_id': ObjectId(sid)})
        return _serialize(doc) if doc else None
    except Exception:
        return None

def get_all_sessions():
    docs = sessions_col.find().sort('created_at', -1).limit(50)
    return [_serialize(d) for d in docs]

def add_message(sid, user_text, bot_text):
    now = datetime.datetime.utcnow().isoformat()
    user_msg = {'id': f'u_{now}', 'role': 'user',  'text': user_text, 'timestamp': now}
    bot_msg  = {'id': f'b_{now}', 'role': 'model', 'text': bot_text,  'timestamp': now}
    sessions_col.update_one(
        {'_id': ObjectId(sid)},
        {
            '$push': {'messages': {'$each': [user_msg, bot_msg]}},
            '$inc':  {'message_count': 1},
            '$set':  {'last_active': datetime.datetime.utcnow()}
        }
    )
    # Auto-update title from first user message
    session = get_session(sid)
    if session and session.get('title') == 'New Analysis':
        title = user_text[:40] + ('...' if len(user_text) > 40 else '')
        sessions_col.update_one({'_id': ObjectId(sid)}, {'$set': {'title': title}})

    # Extract and store fairness scores
    scores = _extract_scores(bot_text)
    if scores:
        analyses_col.insert_one({
            'session_id': sid,
            'overall': scores['overall'],
            'demographic_parity': scores['dp'],
            'equalized_odds': scores['eo'],
            'disparate_impact': scores['di'],
            'fairness': scores['fa'],
            'risk_level': 'LOW' if scores['overall'] >= 75 else 'MEDIUM' if scores['overall'] >= 50 else 'HIGH',
            'message_preview': user_text[:100],
            'created_at': datetime.datetime.utcnow()
        })

    log_event('message_sent', {
        'session_id': sid,
        'user_text_length': len(user_text),
        'bot_text_length': len(bot_text),
        'has_scores': bool(scores)
    })

def delete_session(sid):
    try:
        session = get_session(sid)
        sessions_col.delete_one({'_id': ObjectId(sid)})
        log_event('session_deleted', {'session_id': sid, 'title': session.get('title') if session else ''})
    except Exception:
        pass

def rename_session(sid, new_title):
    try:
        sessions_col.update_one({'_id': ObjectId(sid)}, {'$set': {'title': new_title}})
        log_event('session_renamed', {'session_id': sid, 'new_title': new_title})
    except Exception:
        pass


# ── Users ──────────────────────────────────────────────────────

def log_login(name):
    users_col.update_one(
        {'name': name},
        {
            '$set': {'name': name, 'last_login': datetime.datetime.utcnow()},
            '$inc': {'login_count': 1},
            '$setOnInsert': {'created_at': datetime.datetime.utcnow()}
        },
        upsert=True
    )
    log_event('user_login', {'name': name})

def log_logout(name):
    log_event('user_logout', {'name': name})


# ── File Uploads ───────────────────────────────────────────────

def log_upload(session_id, filename, size_chars):
    uploads_col.insert_one({
        'session_id': session_id,
        'filename': filename,
        'size_chars': size_chars,
        'uploaded_at': datetime.datetime.utcnow()
    })
    log_event('file_uploaded', {'session_id': session_id, 'filename': filename})


# ── Reactions ──────────────────────────────────────────────────

def log_reaction(session_id, reaction_type):
    reactions_col.insert_one({
        'session_id': session_id,
        'type': reaction_type,  # 'up' or 'down'
        'created_at': datetime.datetime.utcnow()
    })
    log_event('reaction', {'session_id': session_id, 'type': reaction_type})


# ── Bias Analyses ──────────────────────────────────────────────

def save_analysis(data: dict):
    data['created_at'] = datetime.datetime.utcnow()
    result = analyses_col.insert_one(data)
    return str(result.inserted_id)

def get_analyses():
    return [_serialize(d) for d in analyses_col.find().sort('created_at', -1)]


# ── Helpers ────────────────────────────────────────────────────

def _extract_scores(text):
    import re
    def extract(patterns):
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                v = float(m.group(1))
                if 0 <= v <= 100:
                    return v
        return None

    dp = extract([r'demographic parity[^0-9]*(\d+\.?\d*)\s*%', r'parity[^0-9]*(\d+\.?\d*)\s*%'])
    eo = extract([r'equalized odds[^0-9]*(\d+\.?\d*)\s*%', r'equalized[^0-9]*(\d+\.?\d*)\s*%'])
    di = extract([r'disparate impact[^0-9]*(\d+\.?\d*)\s*%', r'disparate[^0-9]*(\d+\.?\d*)\s*%'])
    fa = extract([r'fairness[^0-9]*(\d+\.?\d*)\s*%', r'(\d+\.?\d*)\s*%\s*fair'])

    vals = [v for v in [dp, eo, di, fa] if v is not None]
    if not vals:
        lower = text.lower()
        bias_w = ['biased','unfair','discrimination','disparity','skewed']
        fair_w = ['unbiased','no bias','equitable','balanced','highly fair']
        bc = sum(1 for w in bias_w if w in lower)
        fc = sum(1 for w in fair_w if w in lower)
        has_ctx = any(w in lower for w in ['fairness','bias','parity','equalized','disparate'])
        if not has_ctx:
            return None
        overall = min(95, 80 + fc*3) if fc > bc else max(15, 50 - bc*10) if bc > fc else 70
    else:
        overall = round(sum(vals) / len(vals))

    return {'overall': overall, 'dp': dp, 'eo': eo, 'di': di, 'fa': fa}

def _serialize(doc):
    if doc is None:
        return None
    doc['id'] = str(doc.pop('_id'))
    if 'created_at' in doc and hasattr(doc['created_at'], 'isoformat'):
        doc['created_at'] = doc['created_at'].isoformat()
    if 'last_active' in doc and hasattr(doc['last_active'], 'isoformat'):
        doc['last_active'] = doc['last_active'].isoformat()
    return doc
