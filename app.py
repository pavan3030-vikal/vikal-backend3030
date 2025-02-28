from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import logging
import requests
from pymongo import MongoClient
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
import re
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS setup as a fallback (though we're handling manually below)
CORS(app, resources={r"/*": {"origins": "https://vikal-new-production.up.railway.app"}}, supports_credentials=True)

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = "your_openai_api_key_here"  # Replace with your actual OpenAI API key
client = MongoClient("mongodb://mongo:vEvIixiKtkFvKHuMkvTfzjVfCjYbZhGF@shortline.proxy.rlwy.net:42954")
db = client["vikal"]
chat_history = db["chat_history"]
exam_dates = db["exam_dates"]
users = db["users"]

# Log incoming requests
@app.before_request
def log_request():
    logger.info(f"Request: {request.method} {request.path} from {request.origin}")

def call_openai(prompt, max_tokens=300, model="gpt-3.5-turbo"):
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    try:
        response = requests.post(OPENAI_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        logger.error(f"OpenAI API error: {e}")
        raise Exception(f"Failed to generate response: {e}")

@app.route('/test-mongo', methods=['GET', 'OPTIONS'])
def test_mongo():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /test-mongo: {response.headers}")
        return response

    try:
        logger.info("Attempting MongoDB connection...")
        info = client.server_info()
        logger.info(f"MongoDB connection successful: {info}")
        response = jsonify({"message": "MongoDB connected successfully", "info": info})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"GET response headers for /test-mongo: {response.headers}")
        return response, 200
    except Exception as e:
        logger.error(f"MongoDB connection failed: {str(e)}")
        response = jsonify({"error": str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /test-mongo: {response.headers}")
        return response, 500

@app.route('/stats', methods=['GET', 'OPTIONS'])
def get_stats():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /stats: {response.headers}")
        return response

    try:
        logger.info("Fetching stats from MongoDB")
        active_users = users.count_documents({"chatCount": {"$gt": 0}})
        total_questions = chat_history.count_documents({"style": {"$in": ["smart", "step", "teacher", "research"]}})
        total_explanations = chat_history.count_documents({"style": "generic"})
        
        stats = {
            "active_users": active_users,
            "questions_solved": total_questions,
            "explanations_given": total_explanations
        }
        logger.info(f"Stats fetched: {stats}")
        response = jsonify(stats)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"GET response headers for /stats: {response.headers}")
        return response, 200
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        response = jsonify({"error": str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /stats: {response.headers}")
        return response, 500

@app.route('/explain', methods=['POST', 'OPTIONS'])
def explain():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /explain: {response.headers}")
        return response

    data = request.get_json()
    if not data or 'topic' not in data:
        response = jsonify({'error': 'No topic provided'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /explain: {response.headers}")
        return response, 400

    user_id = data.get('user_id', 'anonymous')
    subject = data.get('subject')
    exam = data.get('exam')
    style = data.get('explanation_style', 'teacher')
    category = exam if exam else subject if subject else "generic"

    try:
        logger.info(f"Fetching user {user_id} from MongoDB")
        user = users.find_one({"_id": user_id})
        logger.info(f"User fetch result: {user}")
        if not user:
            logger.info(f"Inserting new user {user_id}")
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            response = jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Limit reached response headers for /explain: {response.headers}")
            return response, 403

        prompt = f"""
        Explain {data['topic']} comprehensively using the following format:
        ### Simple explanation
        Provide a brief, easy-to-understand description of {data['topic']} in 2-3 sentences, using everyday language.

        ### In-depth explanation
        Offer a comprehensive explanation of {data['topic']}, including:
        - Key principles or components
        - Historical context or development (if applicable)
        - Main theories or schools of thought
        - How it functions or is applied in its field

        ### Key concepts or formulas
        List and briefly explain 3-5 essential concepts, formulas, or frameworks central to understanding {data['topic']}.

        ### Real-world applications or examples
        Describe at least three practical applications or notable examples of {data['topic']} in modern contexts.

        ### Flashcards
        Create 5 question-and-answer flashcards covering key aspects of {data['topic']}.

        ### Resources
        Recommend 3 reliable sources for further study on {data['topic']}, such as:
        - Books or academic papers
        - Reputable websites or online courses
        - Documentaries or educational videos

        Ensure all explanations are accurate, well-structured, and tailored for a student audience. Use clear language and provide examples where appropriate.
        """
        response_text = call_openai(prompt, max_tokens=700)
        parts = re.split(r'###\s', response_text)
        notes_parts = [part for part in parts if part.strip() and not part.startswith("Flashcards") and not part.startswith("Resources")]
        notes = "\n\n".join(notes_parts).strip()

        flashcards_part = next((part for part in parts if part.startswith("Flashcards")), "")
        flashcards = flashcards_part.replace("Flashcards", "").strip().split("\n")[:5] if flashcards_part else []

        resources_part = next((part for part in parts if part.startswith("Resources")), "")
        resources = resources_part.replace("Resources", "").strip().split("\n")[:3] if resources_part else []
        resource_list = [{"title": r.split(" - ")[0].strip(), "url": r.split(" - ")[1].strip() if " - " in r else r.strip()} for r in resources if r.strip()]

        if not user["isPro"]:
            logger.info(f"Incrementing chat count for user {user_id}")
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        logger.info(f"Inserting chat history for user {user_id}")
        chat_history.insert_one({
            "user_id": user_id,
            "question": data['topic'],
            "response": notes,
            "category": category,
            "style": style,
            "timestamp": datetime.utcnow()
        })

        response = jsonify({"notes": notes, "flashcards": flashcards, "resources": resource_list})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"POST response headers for /explain: {response.headers}")
        return response
    except Exception as e:
        logger.error(f"Explain endpoint error: {e}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /explain: {response.headers}")
        return response, 500

@app.route('/solve', methods=['POST', 'OPTIONS'])
def solve():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /solve: {response.headers}")
        return response

    data = request.get_json()
    if not data or 'problem' not in data:
        response = jsonify({'error': 'No problem provided'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /solve: {response.headers}")
        return response, 400

    user_id = data.get('user_id', 'anonymous')
    subject = data.get('subject')
    exam = data.get('exam')
    style = data.get('explanation_style', 'teacher')
    category = exam if exam else subject
    if not category:
        response = jsonify({'error': 'Subject or exam required'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /solve: {response.headers}")
        return response, 400

    try:
        logger.info(f"Fetching user {user_id} from MongoDB")
        user = users.find_one({"_id": user_id})
        logger.info(f"User fetch result: {user}")
        if not user:
            logger.info(f"Inserting new user {user_id}")
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            response = jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Limit reached response headers for /solve: {response.headers}")
            return response, 403

        max_tokens = 100 if style.lower() == "smart" else 300 if style.lower() == "research" else 200
        prompt = f"""
        Solve this problem: {data['problem']} using the following format based on the style '{style}':

        ### Solution
        Provide a detailed solution to {data['problem']} tailored to the '{style}' style:
        - For 'smart': Solve concisely with shortcuts, limit to 100 words.
        - For 'step': Use clear, numbered steps with a boxed answer, limit to 200 words.
        - For 'teacher': Explain like a teacher with simple steps and examples, limit to 200 words.
        - For 'research': Offer a detailed, research-style solution with context, limit to 300 words.

        ### Resources
        Recommend 5 reliable sources for further study on {data['problem']}, such as:
        - Books or academic papers
        - Reputable websites or online courses
        - Documentaries or educational videos

        Ensure the solution is accurate, well-structured, and tailored for a student audience. Use clear language and provide examples where appropriate.
        """
        response_text = call_openai(prompt, max_tokens=max_tokens, model="gpt-4")
        parts = re.split(r'###\s', response_text)
        notes_part = next((part for part in parts if part.startswith("Solution")), "")
        notes = notes_part.replace("Solution", "").strip() if notes_part else response_text

        resources_part = next((part for part in parts if part.startswith("Resources")), "")
        resources = resources_part.replace("Resources", "").strip().split("\n")[:5] if resources_part else []
        resource_list = [{"title": r.split(" - ")[0].strip(), "url": r.split(" - ")[1].strip() if " - " in r else r.strip()} for r in resources if r.strip()]

        if not user["isPro"]:
            logger.info(f"Incrementing chat count for user {user_id}")
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        logger.info(f"Inserting chat history for user {user_id}")
        chat_history.insert_one({
            "user_id": user_id,
            "question": data['problem'],
            "response": notes,
            "category": category,
            "style": style,
            "timestamp": datetime.utcnow()
        })

        response = jsonify({"notes": notes, "resources": resource_list})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"POST response headers for /solve: {response.headers}")
        return response
    except Exception as e:
        logger.error(f"Solve endpoint error: {e}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /solve: {response.headers}")
        return response, 500

@app.route('/summarize-youtube', methods=['POST', 'OPTIONS'])
def summarize_youtube():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /summarize-youtube: {response.headers}")
        return response

    data = request.get_json()
    video_url = data.get('videoUrl')
    user_id = data.get('user_id', 'anonymous')

    if not video_url:
        response = jsonify({'error': 'No video URL provided'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /summarize-youtube: {response.headers}")
        return response, 400

    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', video_url)
    if not video_id_match:
        response = jsonify({'error': 'Invalid YouTube video URL'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /summarize-youtube: {response.headers}")
        return response, 400
    
    video_id = video_id_match.group(1)
    
    try:
        logger.info(f"Fetching user {user_id} from MongoDB")
        user = users.find_one({"_id": user_id})
        logger.info(f"User fetch result: {user}")
        if not user:
            logger.info(f"Inserting new user {user_id}")
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            response = jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Limit reached response headers for /summarize-youtube: {response.headers}")
            return response, 403

        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        if not transcript:
            response = jsonify({'error': 'No transcript available for this video'})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Error response headers for /summarize-youtube: {response.headers}")
            return response, 400
        
        transcript_text = "\n".join([f"[{item['start']:.1f}s] {item['text']}" for item in transcript])
        prompt = f"""
        Your output should use the following template:
        ### Summary
        ### Analogy
        ### Notes
        - [Emoji] Bulletpoint
        ### Keywords
        - Explanation
        You have been tasked with creating a concise summary of a YouTube video using its transcription.
        Make a summary of the transcript.
        Additionally make a short complex analogy to give context and/or analogy from day-to-day life from the transcript.
        Create 10 bullet points (each with an appropriate emoji) that summarize the key points or important moments from the video's transcription.
        In addition to the bullet points, extract the most important keywords and any complex words not known to the average reader as well as any acronyms mentioned. For each keyword and complex word, provide an explanation and definition based on its occurrence in the transcription.
        Please ensure that the summary, bullet points, and explanations fit within the 330-word limit, while still offering a comprehensive and clear understanding of the video's content. Use the text above: Video Title {video_id} {transcript_text}.
        """
        response_text = call_openai(prompt, max_tokens=700)
        parts = re.split(r'###\s', response_text)
        summary_part = next((part for part in parts if part.startswith("Summary")), "")
        analogy_part = next((part for part in parts if part.startswith("Analogy")), "")
        notes_part = next((part for part in parts if part.startswith("Notes")), "")
        keywords_part = next((part for part in parts if part.startswith("Keywords")), "")

        summary = summary_part.replace("Summary", "").strip() if summary_part else ""
        analogy = analogy_part.replace("Analogy", "").strip() if analogy_part else ""
        notes = notes_part.replace("Notes", "").strip().split("\n")[:10] if notes_part else []
        keywords = keywords_part.replace("Keywords", "").strip().split("\n") if keywords_part else []

        combined_notes = f"{summary}\n\n**Analogy:** {analogy}\n\n**Key Points:**\n" + "\n".join(notes)
        flashcards = [f"{kw.split(' - ')[0]} - {kw.split(' - ')[1]}" for kw in keywords[:5] if " - " in kw]
        resources = [
            {"title": "YouTube Video", "url": video_url},
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/YouTube"},
            {"title": "Khan Academy", "url": "https://www.khanacademy.org"}
        ]

        if not user["isPro"]:
            logger.info(f"Incrementing chat count for user {user_id}")
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        logger.info(f"Inserting chat history for user {user_id}")
        chat_history.insert_one({
            "user_id": user_id,
            "question": f"Summarize YouTube video {video_url}",
            "response": combined_notes,
            "category": "youtube",
            "style": "summary",
            "timestamp": datetime.utcnow()
        })

        response = jsonify({"notes": combined_notes, "flashcards": flashcards, "resources": resources})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"POST response headers for /summarize-youtube: {response.headers}")
        return response
    except Exception as e:
        logger.error(f"Error summarizing YouTube video: {e}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /summarize-youtube: {response.headers}")
        return response, 500

@app.route('/chat-youtube', methods=['POST', 'OPTIONS'])
def chat_youtube():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /chat-youtube: {response.headers}")
        return response

    data = request.get_json()
    video_id = data.get('video_id')
    user_query = data.get('query')
    user_id = data.get('user_id', 'anonymous')

    if not video_id or not user_query:
        response = jsonify({'error': 'Missing video_id or query'})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /chat-youtube: {response.headers}")
        return response, 400

    try:
        logger.info(f"Fetching user {user_id} from MongoDB")
        user = users.find_one({"_id": user_id})
        logger.info(f"User fetch result: {user}")
        if not user:
            logger.info(f"Inserting new user {user_id}")
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            response = jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Limit reached response headers for /chat-youtube: {response.headers}")
            return response, 403

        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        if not transcript:
            response = jsonify({'error': 'No transcript available for this video'})
            response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
            logger.info(f"Error response headers for /chat-youtube: {response.headers}")
            return response, 400
        
        transcript_text = "\n".join([f"[{item['start']:.1f}s] {item['text']}" for item in transcript])
        prompt = f"Based on this YouTube video transcript: {transcript_text}, answer the following question: {user_query}"
        response_text = call_openai(prompt, max_tokens=500)

        if not user["isPro"]:
            logger.info(f"Incrementing chat count for user {user_id}")
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        logger.info(f"Inserting chat history for user {user_id}")
        chat_history.insert_one({
            "user_id": user_id,
            "question": user_query,
            "response": response_text,
            "category": "youtube",
            "style": "chat",
            "timestamp": datetime.utcnow()
        })

        response = jsonify({'response': response_text})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"POST response headers for /chat-youtube: {response.headers}")
        return response
    except Exception as e:
        logger.error(f"Error chatting with YouTube video: {e}")
        response = jsonify({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"Error response headers for /chat-youtube: {response.headers}")
        return response, 500

@app.route('/exam-dates', methods=['GET', 'PUT', 'OPTIONS'])
def manage_exam_dates():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'GET, PUT, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /exam-dates: {response.headers}")
        return response

    user_id = request.args.get('user_id', 'anonymous')
    if request.method == 'GET':
        dates = {doc['exam']: doc['date'] for doc in exam_dates.find({"user_id": user_id})}
        response = jsonify(dates)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"GET response headers for /exam-dates: {response.headers}")
        return response
    elif request.method == 'PUT':
        data = request.get_json()
        for exam, date in data.items():
            exam_dates.update_one(
                {"user_id": user_id, "exam": exam},
                {"$set": {"date": date}},
                upsert=True
            )
        response = jsonify({"message": "Exam dates updated"})
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        logger.info(f"PUT response headers for /exam-dates: {response.headers}")
        return response, 200

@app.route('/', methods=['GET', 'OPTIONS'])
def home():
    if request.method == 'OPTIONS':
        response = make_response('', 204)
        response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Max-Age'] = '86400'
        logger.info(f"Preflight response headers for /: {response.headers}")
        return response

    response = jsonify({"message": "API is running", "status": "ok"})
    response.headers['Access-Control-Allow-Origin'] = 'https://vikal-new-production.up.railway.app'
    logger.info(f"GET response headers for /: {response.headers}")
    return response

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5001))
    logger.info(f"Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
