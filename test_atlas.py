import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv('.env')

from pymongo import MongoClient

uri = os.getenv('MONGO_URI')
print(f"Connecting to: {uri[:50]}...")

try:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ Connected to MongoDB Atlas!")
    
    db = client['fairness_analyzer']
    # Insert a test document to force DB creation
    result = db['chat_sessions'].insert_one({'test': True, 'msg': 'connection test'})
    print(f"✅ Test document inserted: {result.inserted_id}")
    db['chat_sessions'].delete_one({'_id': result.inserted_id})
    print("✅ Test document cleaned up")
    print("✅ Database 'fairness_analyzer' is now live on Atlas!")
except Exception as e:
    print(f"❌ Error: {e}")
