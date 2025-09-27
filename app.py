from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit
import speech_recognition as sr
import base64
import io
import os
from datetime import datetime
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
from typing import Dict, List, Any, Optional

# 导入角色系统
from character_system import (
    CharacterManager, ConversationMemory, CharacterConsistencyManager,
    CharacterCreationWizard, CharacterProfile
)

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ConversationAPI:
    def __init__(self, model_name: str, system_prompt: str, user_prompt: str, 
                 temperature: float = 0.7, conversation_id: Optional[str] = None, 
                 model_key: str = "", api_base: str = "", enable_history: bool = True):
        """
        综合的模型对话API，支持单轮/多轮对话和纯文本对话
        """
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.temperature = temperature
        self.conversation_id = conversation_id or "default"
        self.enable_history = enable_history
        self.conversation_history: Dict[str, List[Dict[str, Any]]] = {}
        
        # 设置API密钥和基础URL
        self.model_key = model_key or os.getenv('OPENAI_API_KEY', 'sk-fPz5uPZn2ubb9Qexx62yWcFl55Z46iRdBfdlvnjufQ6o0BVo')
        self.api_base = api_base or os.getenv('OPENAI_API_BASE', 'https://api.huatuogpt.cn/v1')
        
        # 验证API密钥是否有效
        if not self.model_key:
            raise ValueError("API密钥未提供: 请设置环境变量OPENAI_API_KEY或在调用时提供model_key参数")

    def generate_response(self) -> str:
        """
        生成回复，支持单轮/多轮对话和纯文本对话
        """
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                url = self.api_base + "/chat/completions"
                session = requests.Session()
                session.proxies = {'http': None, 'https': None}
                session.trust_env = False
                session.verify = False
                
                retry_strategy = Retry(
                    total=2,
                    backoff_factor=0.5,
                    status_forcelist=[429, 500, 502, 503, 504],
                )
                adapter = HTTPAdapter(max_retries=retry_strategy)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                
                # 构建消息列表
                messages = []
                
                if self.enable_history:
                    # 多轮对话：使用历史记录
                    if self.conversation_id not in self.conversation_history:
                        self.conversation_history[self.conversation_id] = [
                            {"role": "system", "content": self.system_prompt}
                        ]
                    messages = self.conversation_history[self.conversation_id].copy()
                    
                    # 限制历史消息长度以提高响应速度
                    if len(messages) > 20:  # 保留最近20条消息
                        messages = [messages[0]] + messages[-19:]  # 保留系统消息和最近19条消息
                    
                    # 添加当前用户消息
                    user_message = {"role": "user", "content": self.user_prompt}
                    messages.append(user_message)
                    self.conversation_history[self.conversation_id].append(user_message)
                else:
                    # 单轮对话：不使用历史记录
                    messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": self.user_prompt}
                    ]
                
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "stream": False,
                    "max_tokens": 2048,
                    "stop": None,
                    "temperature": self.temperature,
                    "top_p": 0.8,
                    "frequency_penalty": 0.3,
                    "n": 1,
                    "response_format": {"type": "text"}
                }
                
                headers = {
                    "Authorization": f"Bearer {self.model_key}",
                    "Content-Type": "application/json"
                }
                
                response = session.post(url, headers=headers, json=payload, timeout=30)
                
                if response.status_code == 200:
                    response_data = response.json()
                    if 'error' in response_data:
                        print("Error: ", response_data)
                        return "Neglected"
                    else:
                        assistant_message = response_data['choices'][0]['message']['content']
                        if assistant_message == None:
                            print("Assistant Message is None: ", response_data)
                            return "Neglected"
                        if self.enable_history:
                            # 多轮对话：保存助手回复到历史记录
                            self.conversation_history[self.conversation_id].append({
                                "role": "assistant", 
                                "content": assistant_message
                            })
                        
                        return assistant_message
                else:
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(1)
                    continue
                    
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, 
                   requests.exceptions.Timeout, Exception):
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(1)
                continue
        
        return "Neglected"

    def clear_conversation(self, conversation_id: Optional[str] = None):
        """清除指定会话的历史记录"""
        if conversation_id is None:
            conversation_id = self.conversation_id
        
        if conversation_id in self.conversation_history:
            del self.conversation_history[conversation_id]

    def get_conversation_history(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取指定会话的历史记录"""
        if conversation_id is None:
            conversation_id = self.conversation_id
        
        return self.conversation_history.get(conversation_id, [])

    def update_prompt(self, user_prompt: str):
        """更新提示词"""
        self.user_prompt = user_prompt

    def add_message(self, role: str, content: str):
        """手动添加消息到对话历史记录"""
        if self.conversation_id not in self.conversation_history:
            self.conversation_history[self.conversation_id] = []
        
        # 添加消息到指定会话的历史记录
        self.conversation_history[self.conversation_id].append({
            "role": role,
            "content": content
        })

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# 全局变量
conversations = {}
tts_engine = None

# 初始化角色系统（不使用Redis）
character_manager = CharacterManager(redis_url=None)
memory_manager = ConversationMemory(redis_url=None)
consistency_manager = CharacterConsistencyManager(character_manager, memory_manager)

class VoiceChatBot:
    def __init__(self):
        self.conversation_api = None
        self.recognizer = sr.Recognizer()
        self.current_character_id = "rumeng"  # 默认角色
        try:
            self.microphone = sr.Microphone()
        except OSError:
            print("警告: 未检测到音频输入设备，语音功能将受限")
            self.microphone = None
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
        # 使用新的ConversationAPI，模型仍为gpt-4o
        self.conversation_api = ConversationAPI(
            model_name="gpt-4o",
            system_prompt="你是一个友好的AI语音助手，用简洁、自然的中文回答用户问题。保持回答简短且对话性强。",
            user_prompt="",
            temperature=0.7,
            model_key=api_key,
            api_base="https://api.huatuogpt.cn/v1",
            enable_history=True
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
    
    def set_character(self, character_id: str):
        """设置当前角色"""
        character = character_manager.get_character(character_id)
        if character:
            self.current_character_id = character_id
            # 更新系统提示词
            if self.conversation_api:
                self.conversation_api.system_prompt = character.to_system_prompt()
            return True
        return False
    
    def get_ai_response(self, message, session_id):
        """获取AI回复（使用角色系统）"""
        try:
            if not self.conversation_api:
                return "API未初始化，请先设置API密钥"
            
            # 构建角色一致性的上下文消息
            messages = consistency_manager.build_context_messages(
                session_id, self.current_character_id, message
            )
            
            # 更新系统提示词和用户消息
            if messages:
                self.conversation_api.system_prompt = messages[0]["content"]
                self.conversation_api.update_prompt(message)
            
            # 生成回复
            response = self.conversation_api.generate_response()
            
            # 增强回复的角色一致性
            enhanced_response = consistency_manager.enhance_response_consistency(
                response, self.current_character_id, session_id
            )
            
            # 保存到记忆系统
            memory_manager.add_message(session_id, "user", message, self.current_character_id)
            memory_manager.add_message(session_id, "assistant", enhanced_response, self.current_character_id)
            
            return enhanced_response
            
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

# 角色管理API
@app.route('/api/characters', methods=['GET'])
def get_characters():
    """获取所有角色列表"""
    try:
        characters = character_manager.list_characters()
        return jsonify({"status": "success", "characters": characters})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters', methods=['POST'])
def create_character():
    """创建新角色"""
    try:
        data = request.json
        character = character_manager.create_character(data)
        return jsonify({
            "status": "success", 
            "message": "角色创建成功",
            "character": {
                "character_id": character.character_id,
                "name": character.name,
                "identity": character.identity,
                "avatar": character.avatar
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/<character_id>', methods=['GET'])
def get_character(character_id):
    """获取特定角色信息"""
    try:
        character = character_manager.get_character(character_id)
        if character:
            from dataclasses import asdict
            return jsonify({"status": "success", "character": asdict(character)})
        else:
            return jsonify({"status": "error", "message": "角色不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/<character_id>', methods=['PUT'])
def update_character(character_id):
    """更新角色信息"""
    try:
        data = request.json
        success = character_manager.update_character(character_id, data)
        if success:
            return jsonify({"status": "success", "message": "角色更新成功"})
        else:
            return jsonify({"status": "error", "message": "角色不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/<character_id>', methods=['DELETE'])
def delete_character(character_id):
    """删除角色"""
    try:
        success = character_manager.delete_character(character_id)
        if success:
            return jsonify({"status": "success", "message": "角色删除成功"})
        else:
            return jsonify({"status": "error", "message": "角色不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/templates', methods=['GET'])
def get_character_templates():
    """获取角色模板"""
    try:
        templates = character_manager.get_templates()
        template_list = [
            {
                "id": template_id,
                "name": template_data.get("name", template_id),
                "description": template_data.get("identity", ""),
                "avatar": template_data.get("avatar", "🤖")
            }
            for template_id, template_data in templates.items()
        ]
        return jsonify({"status": "success", "templates": template_list})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/from_template', methods=['POST'])
def create_character_from_template():
    """从模板创建角色"""
    try:
        data = request.json
        template_name = data.get('template')
        character_data = CharacterCreationWizard.create_from_template(template_name, character_manager)
        
        # 允许用户自定义名称
        if data.get('name'):
            character_data['name'] = data['name']
        
        character = character_manager.create_character(character_data)
        return jsonify({
            "status": "success",
            "message": "角色创建成功",
            "character": {
                "character_id": character.character_id,
                "name": character.name,
                "identity": character.identity,
                "avatar": character.avatar
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/characters/<character_id>/export_template', methods=['POST'])
def export_character_as_template(character_id):
    """将角色导出为模板"""
    try:
        data = request.json
        template_name = data.get('template_name')
        
        if not template_name:
            return jsonify({"status": "error", "message": "模板名称不能为空"})
        
        success = character_manager.export_character_as_template(character_id, template_name)
        if success:
            return jsonify({"status": "success", "message": f"角色已导出为模板: {template_name}"})
        else:
            return jsonify({"status": "error", "message": "角色不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/config/info', methods=['GET'])
def get_config_info():
    """获取配置信息"""
    try:
        config_info = character_manager.get_config_info()
        return jsonify({"status": "success", "config": config_info})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/config/save', methods=['POST'])
def save_config():
    """手动保存配置"""
    try:
        character_manager.save_to_config()
        return jsonify({"status": "success", "message": "配置已保存"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/config/reload', methods=['POST'])
def reload_config():
    """重新加载配置"""
    try:
        character_manager.reload_config()
        return jsonify({"status": "success", "message": "配置已重新加载"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/set_character', methods=['POST'])
def set_current_character():
    """设置当前对话角色"""
    try:
        data = request.json
        character_id = data.get('character_id')
        
        if chatbot.set_character(character_id):
            character = character_manager.get_character(character_id)
            return jsonify({
                "status": "success", 
                "message": f"已切换到角色: {character.name}",
                "character": {
                    "character_id": character.character_id,
                    "name": character.name,
                    "avatar": character.avatar
                }
            })
        else:
            return jsonify({"status": "error", "message": "角色不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/conversation/history/<session_id>', methods=['GET'])
def get_conversation_history(session_id):
    """获取对话历史"""
    try:
        limit = request.args.get('limit', type=int)
        history = memory_manager.get_history(session_id, limit)
        return jsonify({"status": "success", "history": history})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/conversation/history/<session_id>', methods=['DELETE'])
def clear_conversation_history(session_id):
    """清除对话历史"""
    try:
        memory_manager.clear_history(session_id)
        return jsonify({"status": "success", "message": "对话历史已清除"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
        # 设置当前会话ID到chatbot
        chatbot._current_session_id = session_id
        
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
            ai_response = chatbot.get_ai_response(user_text, session_id)
            
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
        # 设置当前会话ID到chatbot
        chatbot._current_session_id = session_id
        
        # 添加用户消息到会话历史
        conversations[session_id].append({"role": "user", "content": user_text})
        emit('user_message', {'text': user_text})
        
        # 获取AI回复
        emit('status', {'message': 'AI正在思考...'})
        ai_response = chatbot.get_ai_response(user_text, session_id)
        
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