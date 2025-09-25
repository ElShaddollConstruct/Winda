from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import openai
import speech_recognition as sr
import base64
import io
import os
from datetime import datetime
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# 全局变量
conversations = {}
tts_engine = None

class VoiceChatBot:
    def __init__(self):
        self.openai_client = None
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.setup_tts()
        
    def setup_tts(self):
        global tts_engine
        try:
            import pyttsx3
            tts_engine = pyttsx3.init()
            # 设置中文语音
            voices = tts_engine.getProperty('voices')
            for voice in voices:
                if 'chinese' in voice.name.lower() or 'mandarin' in voice.name.lower():
                    tts_engine.setProperty('voice', voice.id)
                    break
            tts_engine.setProperty('rate', 180)
            tts_engine.setProperty('volume', 0.8)
        except Exception as e:
            print(f"TTS初始化失败，将跳过语音合成功能: {e}")
            tts_engine = None
    
    def set_api_key(self, api_key):
        # 使用自定义API接口地址，但模型仍为gpt-4o
        self.openai_client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.huatuogpt.cn/v1"
        )
    
    def recognize_speech_from_audio(self, audio_data):
        """从音频数据识别语音"""
        try:
            # 使用Google语音识别API
            text = self.recognizer.recognize_google(audio_data, language='zh-CN')
            return text
        except sr.UnknownValueError:
            return "无法识别语音内容"
        except sr.RequestError as e:
            return f"语音识别服务错误: {e}"
    
    def get_ai_response(self, message, conversation_history):
        """获取AI回复"""
        try:
            messages = [
                {"role": "system", "content": "你是一个友好的AI语音助手，用简洁、自然的中文回答用户问题。保持回答简短且对话性强。"}
            ]
            
            # 添加历史对话
            for msg in conversation_history[-10:]:  # 只保留最近10条对话
                messages.append(msg)
            
            messages.append({"role": "user", "content": message})
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=150,
                temperature=0.7
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"抱歉，AI服务出现错误: {str(e)}"
    
    def text_to_speech(self, text, session_id):
        """文本转语音"""
        try:
            if tts_engine:
                # 生成音频文件
                filename = f"static/audio/response_{session_id}_{int(datetime.now().timestamp())}.wav"
                os.makedirs(os.path.dirname(filename), exist_ok=True)
                
                tts_engine.save_to_file(text, filename)
                tts_engine.runAndWait()
                
                return filename
        except Exception as e:
            print(f"TTS错误: {e}")
        return None

# 初始化语音聊天机器人
chatbot = VoiceChatBot()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/set_api_key', methods=['POST'])
def set_api_key():
    data = request.json
    api_key = data.get('api_key')
    
    if api_key:
        chatbot.set_api_key(api_key)
        session['api_key'] = api_key
        return jsonify({"status": "success", "message": "API Key设置成功"})
    
    return jsonify({"status": "error", "message": "无效的API Key"})

@socketio.on('connect')
def handle_connect():
    session_id = request.sid
    conversations[session_id] = []
    emit('status', {'message': '连接成功，准备开始语音对话'})

@socketio.on('disconnect')
def handle_disconnect():
    session_id = request.sid
    if session_id in conversations:
        del conversations[session_id]

@socketio.on('audio_data')
def handle_audio_data(data):
    session_id = request.sid
    
    try:
        # 解码音频数据
        audio_bytes = base64.b64decode(data['audio'])
        
        # 创建音频文件对象
        audio_io = io.BytesIO(audio_bytes)
        
        # 使用speech_recognition处理音频
        with sr.AudioFile(audio_io) as source:
            audio_data = chatbot.recognizer.record(source)
        
        # 语音识别
        emit('status', {'message': '正在识别语音...'})
        user_text = chatbot.recognize_speech_from_audio(audio_data)
        
        if "无法识别" not in user_text and "错误" not in user_text:
            # 添加用户消息到会话历史
            conversations[session_id].append({"role": "user", "content": user_text})
            emit('user_message', {'text': user_text})
            
            # 获取AI回复
            emit('status', {'message': 'AI正在思考...'})
            ai_response = chatbot.get_ai_response(user_text, conversations[session_id])
            
            # 添加AI回复到会话历史
            conversations[session_id].append({"role": "assistant", "content": ai_response})
            emit('ai_message', {'text': ai_response})
            
            # 生成语音回复
            audio_file = chatbot.text_to_speech(ai_response, session_id)
            if audio_file:
                emit('ai_audio', {'audio_url': audio_file})
            
            emit('status', {'message': '准备下一次对话'})
        else:
            emit('error', {'message': user_text})
            
    except Exception as e:
        emit('error', {'message': f'处理音频时出错: {str(e)}'})

@socketio.on('text_message')
def handle_text_message(data):
    session_id = request.sid
    user_text = data['message']
    
    try:
        # 添加用户消息到会话历史
        conversations[session_id].append({"role": "user", "content": user_text})
        emit('user_message', {'text': user_text})
        
        # 获取AI回复
        emit('status', {'message': 'AI正在思考...'})
        ai_response = chatbot.get_ai_response(user_text, conversations[session_id])
        
        # 添加AI回复到会话历史
        conversations[session_id].append({"role": "assistant", "content": ai_response})
        emit('ai_message', {'text': ai_response})
        
        # 生成语音回复
        audio_file = chatbot.text_to_speech(ai_response, session_id)
        if audio_file:
            emit('ai_audio', {'audio_url': audio_file})
        
        emit('status', {'message': '准备下一次对话'})
        
    except Exception as e:
        emit('error', {'message': f'处理消息时出错: {str(e)}'})

if __name__ == '__main__':
    # 创建必要的目录
    os.makedirs('static/audio', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)