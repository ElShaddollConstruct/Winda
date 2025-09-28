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
import random
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

# 导入角色系统
from character_system import (
    CharacterManager, ConversationMemory, CharacterConsistencyManager,
    CharacterCreationWizard, CharacterProfile
)

# 评估系统已移至独立的命令行工具 cli_evaluation.py

@dataclass
class GamePlayer:
    """游戏玩家"""
    character_id: str
    character_name: str
    character_avatar: str
    role: str  # 游戏中的角色（如：村民、狼人、预言家等）
    is_alive: bool = True
    vote_target: Optional[str] = None
    
@dataclass
class ScenarioState:
    """多角色场景状态"""
    scenario_type: str  # 场景类型（werewolf, debate, etc.）
    phase: str  # 当前阶段
    round_count: int = 1
    players: List[GamePlayer] = None
    eliminated_players: List[str] = None
    scenario_log: List[Dict] = None
    is_active: bool = True
    
    def __post_init__(self):
        if self.players is None:
            self.players = []
        if self.eliminated_players is None:
            self.eliminated_players = []
        if self.scenario_log is None:
            self.scenario_log = []

class MultiCharacterEngine:
    """多角色对话引擎"""
    
    def __init__(self):
        # 狼人杀角色配置
        self.werewolf_roles = {
            "村民": {"team": "village", "description": "普通村民，白天参与投票"},
            "狼人": {"team": "werewolf", "description": "夜晚杀人，白天伪装"},
            "预言家": {"team": "village", "description": "夜晚可以查验一人身份"},
            "女巫": {"team": "village", "description": "拥有毒药和解药各一瓶"},
            "猎人": {"team": "village", "description": "被淘汰时可以开枪带走一人"}
        }
    
    def create_werewolf_scenario(self, session_id: str, character_ids: List[str]) -> bool:
        """创建狼人杀场景"""
        
        if len(character_ids) < 4 or len(character_ids) > 8:
            return False
        
        # 获取角色信息
        players = []
        for char_id in character_ids:
            char = character_manager.get_character(char_id)
            if not char:
                return False
            
            # 分配狼人杀角色
            werewolf_role = self._assign_single_werewolf_role(len(character_ids), len(players))
            
            player = GamePlayer(
                character_id=char.character_id,
                character_name=char.name,
                character_avatar=char.avatar,
                role=werewolf_role
            )
            players.append(player)
        
        # 随机打乱角色分配
        werewolf_roles = [p.role for p in players]
        random.shuffle(werewolf_roles)
        for i, player in enumerate(players):
            player.role = werewolf_roles[i]
        
        # 创建场景状态
        scenario = ScenarioState(
            scenario_type="werewolf",
            phase="day_discussion",
            players=players
        )
        
        multi_scenarios[session_id] = scenario
        return True
    
    def _assign_single_werewolf_role(self, total_players: int, current_index: int) -> str:
        """为单个玩家分配狼人杀角色"""
        
        role_configs = {
            4: ["村民", "村民", "狼人", "预言家"],
            5: ["村民", "村民", "村民", "狼人", "预言家"],
            6: ["村民", "村民", "村民", "狼人", "狼人", "预言家"],
            7: ["村民", "村民", "村民", "狼人", "狼人", "预言家", "女巫"],
            8: ["村民", "村民", "村民", "狼人", "狼人", "预言家", "女巫", "猎人"]
        }
        
        roles = role_configs.get(total_players, role_configs[4])
        return roles[current_index % len(roles)]
    
    def get_next_speaker(self, session_id: str) -> Optional[GamePlayer]:
        """获取下一个发言的玩家"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        alive_players = [p for p in scenario.players if p.is_alive]
        
        if not alive_players:
            return None
        
        # 根据当前阶段决定发言顺序
        if scenario.phase == "day_discussion":
            # 白天讨论：按顺序发言
            current_round = len([log for log in scenario.scenario_log 
                               if log.get('phase') == 'day_discussion' 
                               and log.get('round') == scenario.round_count])
            
            if current_round < len(alive_players):
                return alive_players[current_round]
        
        return None
    
    def process_player_message(self, session_id: str, player: GamePlayer, auto_generate: bool = True) -> Optional[str]:
        """处理玩家消息（自动生成或用户输入）"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        
        if not auto_generate:
            return None  # 等待用户输入
        
        # 自动生成AI回复
        prompt = self._build_scenario_prompt(player, scenario)
        
        try:
            # 使用现有的chatbot获取AI回复
            response = chatbot.get_ai_response(prompt, session_id)
            
            # 记录到场景日志
            scenario.scenario_log.append({
                "phase": scenario.phase,
                "round": scenario.round_count,
                "player": player.character_name,
                "role": player.role,
                "message": response,
                "timestamp": datetime.now().isoformat()
            })
            
            return response
            
        except Exception as e:
            return f"AI回复出错: {str(e)}"
    
    def _build_scenario_prompt(self, player: GamePlayer, scenario: ScenarioState) -> str:
        """构建场景提示词"""
        
        if scenario.scenario_type == "werewolf":
            return self._build_werewolf_prompt(player, scenario)
        
        return "请发言。"
    
    def _build_werewolf_prompt(self, player: GamePlayer, scenario: ScenarioState) -> str:
        """构建狼人杀提示词"""
        
        alive_players = [p for p in scenario.players if p.is_alive]
        alive_names = [p.character_name for p in alive_players]
        
        if scenario.phase == "day_discussion":
            prompt = f"""你正在参与一场狼人杀游戏。

当前情况：
- 游戏第{scenario.round_count}天的白天讨论阶段
- 你的身份是：{player.role}
- 你的队伍：{self.werewolf_roles[player.role]['team']}
- 存活玩家：{', '.join(alive_names)}
- 已淘汰玩家：{', '.join(scenario.eliminated_players) if scenario.eliminated_players else '无'}

游戏规则：
- 如果你是村民阵营，目标是找出所有狼人
- 如果你是狼人，目标是伪装身份，误导村民
- 白天所有人讨论，然后投票淘汰一人

请根据你的角色身份和当前局势，发表你的看法和推理。保持角色的性格特点，但要融入狼人杀的游戏思维。发言要简洁有力，不超过100字。"""
        
        elif scenario.phase == "voting":
            other_players = [p for p in alive_players if p.character_id != player.character_id]
            prompt = f"""现在是投票阶段，你需要选择一个人投票淘汰。

你的身份：{player.role}
可投票的玩家：{', '.join([p.character_name for p in other_players])}

请根据刚才的讨论内容和你的角色身份，选择一个最可疑的人投票。

回复格式：我投票给【玩家姓名】，理由是...

保持你的角色性格，但要体现狼人杀的思维逻辑。"""
        
        else:
            prompt = "请发言。"
        
        return prompt
    
    def advance_phase(self, session_id: str) -> bool:
        """推进游戏阶段"""
        
        if session_id not in multi_scenarios:
            return False
        
        scenario = multi_scenarios[session_id]
        
        if scenario.scenario_type == "werewolf":
            return self._advance_werewolf_phase(scenario)
        
        return False
    
    def _advance_werewolf_phase(self, scenario: ScenarioState) -> bool:
        """推进狼人杀游戏阶段"""
        
        if scenario.phase == "day_discussion":
            scenario.phase = "voting"
            return True
        
        elif scenario.phase == "voting":
            # 处理投票结果
            scenario.phase = "night"
            scenario.round_count += 1
            return True
        
        elif scenario.phase == "night":
            # 夜晚结束，开始新一天
            scenario.phase = "day_discussion"
            return True
        
        return False
    
    def check_game_end(self, session_id: str) -> Optional[str]:
        """检查游戏是否结束"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        
        if scenario.scenario_type == "werewolf":
            alive_players = [p for p in scenario.players if p.is_alive]
            werewolves = [p for p in alive_players if p.role == "狼人"]
            villagers = [p for p in alive_players if p.role != "狼人"]
            
            if not werewolves:
                scenario.is_active = False
                return "村民阵营获胜！所有狼人已被淘汰。"
            
            if len(werewolves) >= len(villagers):
                scenario.is_active = False
                return "狼人阵营获胜！狼人数量达到或超过村民数量。"
        
        return None

# 创建多角色引擎实例
multi_engine = MultiCharacterEngine()

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
multi_scenarios = {}  # 多角色场景会话

# 初始化角色系统（不使用Redis）
character_manager = CharacterManager(redis_url=None)
memory_manager = ConversationMemory(redis_url=None)
consistency_manager = CharacterConsistencyManager(character_manager, memory_manager)

# 评估系统功能已移至独立的命令行工具，使用方法：
# python cli_evaluation.py --help

class VoiceChatBot:
    def __init__(self):
        self.conversation_api = None
        self.recognizer = sr.Recognizer()
        self.current_character_id = "rumeng"  # 默认角色
        try:
            self.microphone = sr.Microphone()
        except (OSError, AttributeError) as e:
            print(f"警告: 未检测到音频输入设备或缺少pyaudio模块，语音功能将受限: {e}")
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
                # 清空当前会话的对话历史，确保角色切换生效
                if hasattr(self, '_current_session_id') and self._current_session_id:
                    self.conversation_api.clear_conversation(self._current_session_id)
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
        session_id = data.get('session_id')  # 从前端传递session_id
        
        # 设置当前会话ID
        if session_id:
            chatbot._current_session_id = session_id
            
        if chatbot.set_character(character_id):
            character = character_manager.get_character(character_id)
            
            # 清空相关的对话历史
            if session_id:
                if session_id in conversations:
                    conversations[session_id] = []
                memory_manager.clear_history(session_id)
            
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

# 多角色场景API
@app.route('/api/scenario/create', methods=['POST'])
def create_scenario():
    """创建多角色场景"""
    try:
        data = request.json
        scenario_type = data.get('scenario_type', 'werewolf')
        character_ids = data.get('character_ids', [])
        session_id = data.get('session_id', request.sid)
        
        if scenario_type == 'werewolf':
            success = multi_engine.create_werewolf_scenario(session_id, character_ids)
            if success:
                scenario = multi_scenarios[session_id]
                return jsonify({
                    "status": "success",
                    "message": "狼人杀场景创建成功",
                    "scenario": {
                        "type": scenario.scenario_type,
                        "phase": scenario.phase,
                        "players": [asdict(p) for p in scenario.players]
                    }
                })
            else:
                return jsonify({"status": "error", "message": "场景创建失败，请检查角色数量（4-8个）"})
        
        return jsonify({"status": "error", "message": "不支持的场景类型"})
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/scenario/status/<session_id>', methods=['GET'])
def get_scenario_status(session_id):
    """获取场景状态"""
    try:
        if session_id not in multi_scenarios:
            return jsonify({"status": "error", "message": "场景不存在"})
        
        scenario = multi_scenarios[session_id]
        
        return jsonify({
            "status": "success",
            "scenario": {
                "type": scenario.scenario_type,
                "phase": scenario.phase,
                "round": scenario.round_count,
                "is_active": scenario.is_active,
                "players": [asdict(p) for p in scenario.players],
                "eliminated_players": scenario.eliminated_players,
                "log_count": len(scenario.scenario_log)
            }
        })
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/scenario/next_speaker/<session_id>', methods=['GET'])
def get_next_speaker(session_id):
    """获取下一个发言者"""
    try:
        if session_id not in multi_scenarios:
            return jsonify({"status": "error", "message": "场景不存在"})
        
        next_player = multi_engine.get_next_speaker(session_id)
        
        if next_player:
            return jsonify({
                "status": "success",
                "next_speaker": asdict(next_player)
            })
        else:
            return jsonify({
                "status": "success", 
                "next_speaker": None,
                "message": "当前阶段无发言者"
            })
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/scenario/advance/<session_id>', methods=['POST'])
def advance_scenario_phase(session_id):
    """推进场景阶段"""
    try:
        if session_id not in multi_scenarios:
            return jsonify({"status": "error", "message": "场景不存在"})
        
        success = multi_engine.advance_phase(session_id)
        
        if success:
            scenario = multi_scenarios[session_id]
            
            # 检查游戏是否结束
            end_message = multi_engine.check_game_end(session_id)
            
            return jsonify({
                "status": "success",
                "message": "阶段推进成功",
                "new_phase": scenario.phase,
                "round": scenario.round_count,
                "game_end": end_message is not None,
                "end_message": end_message
            })
        else:
            return jsonify({"status": "error", "message": "阶段推进失败"})
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/scenario/logs/<session_id>', methods=['GET'])
def get_scenario_logs(session_id):
    """获取场景日志"""
    try:
        if session_id not in multi_scenarios:
            return jsonify({"status": "error", "message": "场景不存在"})
        
        scenario = multi_scenarios[session_id]
        limit = request.args.get('limit', type=int)
        
        logs = scenario.scenario_log
        if limit:
            logs = logs[-limit:]
        
        return jsonify({
            "status": "success",
            "logs": logs,
            "total_count": len(scenario.scenario_log)
        })
    
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# 评估系统API已移除，改为使用独立的命令行工具
# 使用方法: python cli_evaluation.py --help

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
            
            # 通知前端显示思考状态
            emit('show_thinking')
            
            # 获取AI回复
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
        
        # 获取AI回复
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

@socketio.on('clear_conversation')
def handle_clear_conversation():
    session_id = request.sid
    
    try:
        # 清空会话历史
        if session_id in conversations:
            conversations[session_id] = []
        
        # 清空API对话历史
        if chatbot.conversation_api:
            chatbot.conversation_api.clear_conversation(session_id)
        
        # 清空角色系统的对话记忆
        memory_manager.clear_history(session_id)
        
        emit('status', {'message': '对话历史已清空'})
        
    except Exception as e:
        emit('error', {'message': f'清空对话失败: {str(e)}'})

# 多角色场景Socket.IO事件
@socketio.on('create_scenario')
def handle_create_scenario(data):
    session_id = request.sid
    
    try:
        scenario_type = data.get('scenario_type', 'werewolf')
        character_ids = data.get('character_ids', [])
        
        if scenario_type == 'werewolf':
            success = multi_engine.create_werewolf_scenario(session_id, character_ids)
            if success:
                scenario = multi_scenarios[session_id]
                
                emit('scenario_created', {
                    'success': True,
                    'message': f'狼人杀场景创建成功！{len(scenario.players)}人局',
                    'scenario': {
                        'type': scenario.scenario_type,
                        'phase': scenario.phase,
                        'players': [asdict(p) for p in scenario.players]
                    }
                })
                
                # 自动开始第一轮发言
                handle_next_turn()
            else:
                emit('scenario_error', {'message': '场景创建失败，需要4-8个角色'})
        else:
            emit('scenario_error', {'message': '不支持的场景类型'})
    
    except Exception as e:
        emit('scenario_error', {'message': f'创建场景失败: {str(e)}'})

@socketio.on('next_turn')
def handle_next_turn():
    session_id = request.sid
    
    try:
        if session_id not in multi_scenarios:
            emit('scenario_error', {'message': '场景不存在'})
            return
        
        scenario = multi_scenarios[session_id]
        
        if not scenario.is_active:
            emit('game_ended', {'message': '游戏已结束'})
            return
        
        # 获取下一个发言者
        next_player = multi_engine.get_next_speaker(session_id)
        
        if next_player:
            # 发送当前发言者信息
            emit('current_speaker', {
                'player': asdict(next_player),
                'phase': scenario.phase,
                'round': scenario.round_count
            })
            
            # 自动生成AI回复
            response = multi_engine.process_player_message(session_id, next_player, auto_generate=True)
            
            if response:
                emit('player_message', {
                    'player': asdict(next_player),
                    'message': response,
                    'phase': scenario.phase,
                    'round': scenario.round_count
                })
                
                # 检查是否该推进阶段
                if scenario.phase == "day_discussion":
                    alive_players = [p for p in scenario.players if p.is_alive]
                    discussion_count = len([log for log in scenario.scenario_log 
                                          if log.get('phase') == 'day_discussion' 
                                          and log.get('round') == scenario.round_count])
                    
                    if discussion_count >= len(alive_players):
                        # 所有人都发言完毕，自动进入投票阶段
                        multi_engine.advance_phase(session_id)
                        emit('phase_changed', {
                            'new_phase': 'voting',
                            'message': '讨论结束，开始投票阶段'
                        })
                        
                        # 开始投票
                        handle_voting_phase()
            else:
                emit('scenario_error', {'message': 'AI回复生成失败'})
        
        else:
            # 当前阶段没有更多发言者，需要推进阶段
            success = multi_engine.advance_phase(session_id)
            if success:
                emit('phase_changed', {
                    'new_phase': scenario.phase,
                    'round': scenario.round_count,
                    'message': f'进入{scenario.phase}阶段'
                })
                
                # 检查游戏是否结束
                end_message = multi_engine.check_game_end(session_id)
                if end_message:
                    emit('game_ended', {'message': end_message})
                else:
                    # 继续下一轮
                    handle_next_turn()
    
    except Exception as e:
        emit('scenario_error', {'message': f'处理回合失败: {str(e)}'})

def handle_voting_phase():
    """处理投票阶段"""
    session_id = request.sid
    
    try:
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        alive_players = [p for p in scenario.players if p.is_alive]
        
        # 每个玩家进行投票
        vote_results = {}
        
        for player in alive_players:
            # 生成投票回复
            response = multi_engine.process_player_message(session_id, player, auto_generate=True)
            
            if response:
                emit('player_vote', {
                    'player': asdict(player),
                    'message': response,
                    'phase': 'voting'
                })
                
                # 解析投票目标（简化版）
                vote_target = None
                for other_player in alive_players:
                    if other_player.character_id != player.character_id and other_player.character_name in response:
                        vote_target = other_player.character_name
                        break
                
                if not vote_target and len(alive_players) > 1:
                    # 随机选择一个目标
                    other_players = [p for p in alive_players if p.character_id != player.character_id]
                    vote_target = random.choice(other_players).character_name
                
                if vote_target:
                    if vote_target not in vote_results:
                        vote_results[vote_target] = 0
                    vote_results[vote_target] += 1
        
        # 统计投票结果
        if vote_results:
            max_votes = max(vote_results.values())
            candidates = [name for name, votes in vote_results.items() if votes == max_votes]
            eliminated_name = random.choice(candidates) if candidates else None
            
            if eliminated_name:
                # 找到被淘汰的玩家
                eliminated_player = next(p for p in alive_players if p.character_name == eliminated_name)
                eliminated_player.is_alive = False
                scenario.eliminated_players.append(eliminated_name)
                
                emit('player_eliminated', {
                    'player': asdict(eliminated_player),
                    'vote_results': vote_results,
                    'message': f'{eliminated_name} 被投票淘汰'
                })
        
        # 检查游戏是否结束
        end_message = multi_engine.check_game_end(session_id)
        if end_message:
            emit('game_ended', {'message': end_message})
        else:
            # 继续夜晚阶段
            multi_engine.advance_phase(session_id)
            emit('phase_changed', {
                'new_phase': 'night',
                'message': '进入夜晚阶段'
            })
            
            # 夜晚阶段结束后开始新一天
            multi_engine.advance_phase(session_id)
            emit('phase_changed', {
                'new_phase': 'day_discussion',
                'round': scenario.round_count,
                'message': f'第{scenario.round_count}天开始'
            })
    
    except Exception as e:
        emit('scenario_error', {'message': f'投票阶段处理失败: {str(e)}'})

@socketio.on('get_scenario_status')
def handle_get_scenario_status():
    session_id = request.sid
    
    try:
        if session_id not in multi_scenarios:
            emit('scenario_status', {'exists': False})
            return
        
        scenario = multi_scenarios[session_id]
        
        emit('scenario_status', {
            'exists': True,
            'scenario': {
                'type': scenario.scenario_type,
                'phase': scenario.phase,
                'round': scenario.round_count,
                'is_active': scenario.is_active,
                'players': [asdict(p) for p in scenario.players],
                'eliminated_players': scenario.eliminated_players
            }
        })
    
    except Exception as e:
        emit('scenario_error', {'message': f'获取状态失败: {str(e)}'})

@socketio.on('end_scenario')
def handle_end_scenario():
    session_id = request.sid
    
    try:
        if session_id in multi_scenarios:
            del multi_scenarios[session_id]
            emit('scenario_ended', {'message': '多角色场景已结束'})
        else:
            emit('scenario_error', {'message': '没有活跃的场景'})
    
    except Exception as e:
        emit('scenario_error', {'message': f'结束场景失败: {str(e)}'})

if __name__ == '__main__':
    # 创建必要的目录
    os.makedirs('static/audio', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)