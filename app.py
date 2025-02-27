from flask import Flask, request, jsonify
from flask_cors import CORS
import logging
import requests
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime
from youtube_transcript_api import YouTubeTranscriptApi
import re

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["http://localhost:3000", "https://*.cloudfront.net"], methods=["GET", "POST", "OPTIONS"])

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=False)
db = client["vikal"]
chat_history = db["chat_history"]
exam_dates = db["exam_dates"]
users = db["users"]

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

@app.route('/test-mongo', methods=['GET'])
def test_mongo():
    try:
        client.server_info()
        return jsonify({"message": "MongoDB connected successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/explain', methods=['POST'])
def explain():
    data = request.get_json()
    if not data or 'topic' not in data:
        return jsonify({'error': 'No topic provided'}), 400

    user_id = data.get('user_id', 'anonymous')
    subject = data.get('subject')
    exam = data.get('exam')
    style = data.get('explanation_style', 'teacher')
    category = exam if exam else subject if subject else "generic"

    try:
        user = users.find_one({"_id": user_id})
        if not user:
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            return jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"}), 403

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
        response = call_openai(prompt, max_tokens=700)
        parts = re.split(r'###\s', response)
        notes_parts = [part for part in parts if part.strip() and not part.startswith("Flashcards") and not part.startswith("Resources")]
        notes = "\n\n".join(notes_parts).strip()

        flashcards_part = next((part for part in parts if part.startswith("Flashcards")), "")
        flashcards = flashcards_part.replace("Flashcards", "").strip().split("\n")[:5] if flashcards_part else []

        resources_part = next((part for part in parts if part.startswith("Resources")), "")
        resources = resources_part.replace("Resources", "").strip().split("\n")[:3] if resources_part else []
        resource_list = [{"title": r.split(" - ")[0].strip(), "url": r.split(" - ")[1].strip() if " - " in r else r.strip()} for r in resources if r.strip()]

        if not user["isPro"]:
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        return jsonify({"notes": notes, "flashcards": flashcards, "resources": resource_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/solve', methods=['POST'])
def solve():
    data = request.get_json()
    if not data or 'problem' not in data:
        return jsonify({'error': 'No problem provided'}), 400

    user_id = data.get('user_id', 'anonymous')
    subject = data.get('subject')
    exam = data.get('exam')
    style = data.get('explanation_style', 'teacher')
    category = exam if exam else subject
    if not category:
        return jsonify({'error': 'Subject or exam required'}), 400

    try:
        user = users.find_one({"_id": user_id})
        if not user:
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            return jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"}), 403

        max_tokens = 100 if style.lower() == "100_words" else 300 if style.lower() == "research" else 200
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
        response = call_openai(prompt, max_tokens=300, model="gpt-4")  # GPT-4 for accuracy
        parts = re.split(r'###\s', response)
        notes_part = next((part for part in parts if part.startswith("Solution")), "")
        notes = notes_part.replace("Solution", "").strip() if notes_part else response

        resources_part = next((part for part in parts if part.startswith("Resources")), "")
        resources = resources_part.replace("Resources", "").strip().split("\n")[:5] if resources_part else []
        resource_list = [{"title": r.split(" - ")[0].strip(), "url": r.split(" - ")[1].strip() if " - " in r else r.strip()} for r in resources if r.strip()]

        if not user["isPro"]:
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        return jsonify({"notes": notes, "resources": resource_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/summarize-youtube', methods=['POST'])
def summarize_youtube():
    data = request.get_json()
    video_url = data.get('videoUrl')
    user_id = data.get('user_id', 'anonymous')

    if not video_url:
        return jsonify({'error': 'No video URL provided'}), 400

    video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', video_url)
    if not video_id_match:
        return jsonify({'error': 'Invalid YouTube video URL'}), 400
    
    video_id = video_id_match.group(1)
    
    try:
        user = users.find_one({"_id": user_id})
        if not user:
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            return jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"}), 403

        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        if not transcript:
            return jsonify({'error': 'No transcript available for this video'}), 400

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
        response = call_openai(prompt, max_tokens=700)
        parts = re.split(r'###\s', response)
        summary_part = next((part for part in parts if part.startswith("Summary")), "")
        analogy_part = next((part for part in parts if part.startswith("Analogy")), "")
        notes_part = next((part for part in parts if part.startswith("Notes")), "")
        keywords_part = next((part for part in parts if part.startswith("Keywords")), "")

        summary = summary_part.replace("Summary", "").strip() if summary_part else ""
        analogy = analogy_part.replace("Analogy", "").strip() if analogy_part else ""
        notes = notes_part.replace("Notes", "").strip().split("\n")[:10] if notes_part else []
        keywords = keywords_part.replace("Keywords", "").strip().split("\n") if keywords_part else []

        # Combine summary and analogy into notes for UI consistency
        combined_notes = f"{summary}\n\n**Analogy:** {analogy}\n\n**Key Points:**\n" + "\n".join(notes)
        flashcards = [f"{kw.split(' - ')[0]} - {kw.split(' - ')[1]}" for kw in keywords[:5] if " - " in kw]
        resources = [
            {"title": "YouTube Video", "url": video_url},
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/YouTube"},
            {"title": "Khan Academy", "url": "https://www.khanacademy.org"}
        ]

        if not user["isPro"]:
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        return jsonify({"notes": combined_notes, "flashcards": flashcards, "resources": resources})
    except Exception as e:
        logger.error(f"Error summarizing YouTube video: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/chat-youtube', methods=['POST'])
def chat_youtube():
    data = request.get_json()
    video_id = data.get('video_id')
    user_query = data.get('query')
    user_id = data.get('user_id', 'anonymous')

    if not video_id or not user_query:
        return jsonify({'error': 'Missing video_id or query'}), 400

    try:
        user = users.find_one({"_id": user_id})
        if not user:
            users.insert_one({
                "_id": user_id,
                "email": data.get("email", "unknown"),
                "chatCount": 0,
                "isPro": False,
                "createdAt": datetime.utcnow()
            })
            user = users.find_one({"_id": user_id})

        if not user["isPro"] and user["chatCount"] >= 3:
            return jsonify({"error": "Chat limit reached. Upgrade to Pro for unlimited chats!"}), 403

        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        if not transcript:
            return jsonify({'error': 'No transcript available for this video'}), 400
        
        transcript_text = "\n".join([f"[{item['start']:.1f}s] {item['text']}" for item in transcript])
        prompt = f"Based on this YouTube video transcript: {transcript_text}, answer the following question: {user_query}"
        response = call_openai(prompt, max_tokens=500)

        if not user["isPro"]:
            users.update_one({"_id": user_id}, {"$inc": {"chatCount": 1}})

        return jsonify({'response': response})
    except Exception as e:
        logger.error(f"Error chatting with YouTube video: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/exam-dates', methods=['GET', 'PUT'])
def manage_exam_dates():
    user_id = request.args.get('user_id', 'anonymous')
    if request.method == 'GET':
        dates = {doc['exam']: doc['date'] for doc in exam_dates.find({"user_id": user_id})}
        return jsonify(dates)
    elif request.method == 'PUT':
        data = request.get_json()
        for exam, date in data.items():
            exam_dates.update_one(
                {"user_id": user_id, "exam": exam},
                {"$set": {"date": date}},
                upsert=True
            )
        return jsonify({"message": "Exam dates updated"}), 200


@app.route('/')
def home():
    return jsonify({"message": "API is running", "status": "ok"})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5001))
    logger.info(f"Starting Flask server on port {port}")
    app.run(host='127.0.0.1', port=port, debug=True)