"""
虚拟AI角色对话系统：角色管理与一致性保持
基于技术文档实现的完整角色系统
"""

import json
import os
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import redis
import uuid
import shutil

@dataclass
class CharacterProfile:
    """角色档案数据结构"""
    character_id: str
    name: str
    identity: str  # 角色身份
    background: str  # 背景故事
    personality: List[str]  # 性格特点列表
    language_style: str  # 语言风格
    behavior_rules: List[str]  # 行为准则
    memory_requirements: str  # 记忆要求
    avatar: str = "🤖"  # 角色头像
    created_at: str = ""
    updated_at: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
    
    def to_system_prompt(self) -> str:
        """将角色档案转换为系统提示词"""
        personality_str = "、".join(self.personality)
        behavior_str = "\n".join([f"- {rule}" for rule in self.behavior_rules])
        
        prompt = f"""你是一个{self.identity}"{self.name}"。

背景：{self.background}

性格特点：{personality_str}

语言风格：{self.language_style}

行为准则：
{behavior_str}

记忆要求：{self.memory_requirements}

请始终保持角色设定的一致性，在对话中体现你的性格和背景。记住之前的对话内容，保持对话的连贯性。"""
        
        return prompt

class CharacterManager:
    """角色管理器"""
    
    def __init__(self, redis_url: str = None, config_file: str = "characters_config.json"):
        self.redis_client = None
        if redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                # 测试连接
                self.redis_client.ping()
                print("Redis连接成功")
            except Exception as e:
                print(f"Redis连接失败，将使用文件存储: {e}")
                self.redis_client = None
        self.config_file = config_file
        self.characters: Dict[str, CharacterProfile] = {}
        self.templates: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, Any] = {}
        self.load_from_config()
    
    def load_from_config(self):
        """从配置文件加载角色和模板"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # 加载角色
                characters_data = config.get('characters', {})
                for char_id, char_data in characters_data.items():
                    character = CharacterProfile(
                        character_id=char_data['character_id'],
                        name=char_data['name'],
                        identity=char_data['identity'],
                        background=char_data['background'],
                        personality=char_data['personality'],
                        language_style=char_data['language_style'],
                        behavior_rules=char_data['behavior_rules'],
                        memory_requirements=char_data['memory_requirements'],
                        avatar=char_data.get('avatar', '🤖'),
                        created_at=char_data.get('created_at', ''),
                        updated_at=char_data.get('updated_at', '')
                    )
                    self.characters[char_id] = character
                
                # 加载模板
                self.templates = config.get('templates', {})
                
                # 加载设置
                self.settings = config.get('settings', {
                    'auto_save': True,
                    'backup_enabled': True,
                    'max_characters': 50,
                    'config_version': '1.0'
                })
                
                print(f"成功从配置文件加载了 {len(self.characters)} 个角色和 {len(self.templates)} 个模板")
            else:
                print("配置文件不存在，将创建默认配置")
                self.create_default_config()
                
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            self.load_default_characters()
    
    def save_to_config(self):
        """保存角色和模板到配置文件"""
        try:
            # 创建备份
            if self.settings.get('backup_enabled', True) and os.path.exists(self.config_file):
                backup_file = f"{self.config_file}.backup"
                shutil.copy2(self.config_file, backup_file)
            
            config = {
                'characters': {},
                'templates': self.templates,
                'settings': self.settings
            }
            
            # 转换角色数据
            for char_id, character in self.characters.items():
                config['characters'][char_id] = asdict(character)
            
            # 保存到文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            print(f"成功保存 {len(self.characters)} 个角色到配置文件")
            
        except Exception as e:
            print(f"保存配置文件失败: {e}")
    
    def create_default_config(self):
        """创建默认配置文件"""
        self.load_default_characters()
        self.load_default_templates()
        self.save_to_config()
    
    def load_default_templates(self):
        """加载默认模板"""
        self.templates = {
            "doctor": {
                "name": "AI医生",
                "identity": "专业的医疗顾问",
                "background": "拥有丰富医疗经验的AI医生，致力于为用户提供专业的健康建议。",
                "personality": ["专业", "耐心", "细心", "负责任"],
                "language_style": "专业术语与通俗解释相结合，语气温和",
                "behavior_rules": [
                    "优先询问用户症状",
                    "提供专业建议但不替代正式诊断",
                    "关注用户身体健康"
                ],
                "memory_requirements": "记住用户的健康状况和咨询历史",
                "avatar": "👨‍⚕️"
            },
            "teacher": {
                "name": "AI老师",
                "identity": "耐心的教育工作者",
                "background": "专业的教育AI，擅长各科目教学，注重启发式教育。",
                "personality": ["耐心", "博学", "善于启发", "鼓励"],
                "language_style": "简单易懂，善用比喻和例子",
                "behavior_rules": [
                    "用生动例子解释概念",
                    "鼓励用户思考",
                    "保持积极正面的态度"
                ],
                "memory_requirements": "记住用户的学习进度和兴趣点",
                "avatar": "👩‍🏫"
            },
            "assistant": {
                "name": "AI助手",
                "identity": "全能的个人助手",
                "background": "智能的个人助手，能够协助处理各种日常事务和问题。",
                "personality": ["高效", "友好", "可靠", "贴心"],
                "language_style": "简洁明了，友好亲切",
                "behavior_rules": [
                    "优先理解用户需求",
                    "提供实用的解决方案",
                    "保持服务态度"
                ],
                "memory_requirements": "记住用户的偏好和常用需求",
                "avatar": "🤖"
            }
        }
    
    def load_default_characters(self):
        """加载默认角色"""
        default_characters = [
            CharacterProfile(
                character_id="rumeng",
                name="如梦",
                identity="性格高冷、谨慎的虚拟人物",
                background="你是一位来自500年前的祭品，寄宿在建实神社神刀上，恢复凡人后开始校园生活。",
                personality=["高冷", "谨慎", "偶尔害羞", "喜欢撒娇", "被摸头时会羞涩"],
                language_style="使用简洁明了的中文表达，语气温和但保持距离感",
                behavior_rules=[
                    "始终记住自己的身份",
                    "不涉及恐怖主义、种族歧视等话题",
                    "保持高冷但不失温暖的形象"
                ],
                memory_requirements="记住之前的对话内容，保持对话连贯性",
                avatar="🗡️"
            ),
            CharacterProfile(
                character_id="doctor_zhang",
                name="张医生",
                identity="经验丰富的全科医生",
                background="从医20年，专业且耐心，致力于为患者提供最好的医疗建议。",
                personality=["专业", "耐心", "温和", "负责任"],
                language_style="用通俗易懂的语言解释医学概念，语气温和专业",
                behavior_rules=[
                    "总是先询问患者的具体症状",
                    "提供专业医疗建议，但不替代正式诊断",
                    "关心患者的身体状况"
                ],
                memory_requirements="记住患者的症状和病史",
                avatar="👨‍⚕️"
            ),
            CharacterProfile(
                character_id="teacher_li",
                name="李老师",
                identity="温和的小学语文老师",
                background="从教15年的小学语文老师，善于启发学生思考，深受学生喜爱。",
                personality=["温和", "耐心", "善于启发", "充满爱心"],
                language_style="亲切友好，经常使用比喻和故事来教学",
                behavior_rules=[
                    "善于用生动例子解释复杂概念",
                    "总是鼓励学生，即使犯错也耐心纠正",
                    "经常使用'同学们'等亲切称呼"
                ],
                memory_requirements="记住学生的学习进度和个性特点",
                avatar="👩‍🏫"
            )
        ]
        
        for character in default_characters:
            self.characters[character.character_id] = character
    
    def create_character(self, character_data: Dict[str, Any]) -> CharacterProfile:
        """创建新角色"""
        character_id = character_data.get('character_id') or str(uuid.uuid4())
        
        character = CharacterProfile(
            character_id=character_id,
            name=character_data['name'],
            identity=character_data['identity'],
            background=character_data['background'],
            personality=character_data['personality'],
            language_style=character_data['language_style'],
            behavior_rules=character_data['behavior_rules'],
            memory_requirements=character_data['memory_requirements'],
            avatar=character_data.get('avatar', '🤖')
        )
        
        self.characters[character_id] = character
        self._save_character(character)
        
        # 自动保存到配置文件
        if self.settings.get('auto_save', True):
            self.save_to_config()
            
        return character
    
    def get_character(self, character_id: str) -> Optional[CharacterProfile]:
        """获取角色"""
        return self.characters.get(character_id)
    
    def list_characters(self) -> List[Dict[str, Any]]:
        """获取所有角色列表"""
        return [
            {
                'character_id': char.character_id,
                'name': char.name,
                'identity': char.identity,
                'avatar': char.avatar,
                'created_at': char.created_at
            }
            for char in self.characters.values()
        ]
    
    def update_character(self, character_id: str, updates: Dict[str, Any]) -> bool:
        """更新角色信息"""
        if character_id not in self.characters:
            return False
        
        character = self.characters[character_id]
        for key, value in updates.items():
            if hasattr(character, key):
                setattr(character, key, value)
        
        character.updated_at = datetime.now().isoformat()
        self._save_character(character)
        
        # 自动保存到配置文件
        if self.settings.get('auto_save', True):
            self.save_to_config()
            
        return True
    
    def delete_character(self, character_id: str) -> bool:
        """删除角色"""
        if character_id in self.characters:
            del self.characters[character_id]
            if self.redis_client:
                try:
                    self.redis_client.delete(f"character:{character_id}")
                except Exception as e:
                    print(f"从Redis删除失败: {e}")
            
            # 自动保存到配置文件
            if self.settings.get('auto_save', True):
                self.save_to_config()
                
            return True
        return False
    
    def _save_character(self, character: CharacterProfile):
        """保存角色到Redis（如果可用）"""
        if self.redis_client:
            try:
                key = f"character:{character.character_id}"
                data = json.dumps(asdict(character), ensure_ascii=False)
                self.redis_client.set(key, data)
            except Exception as e:
                print(f"保存到Redis失败: {e}")
        # 无论Redis是否可用，都会通过auto_save保存到文件
    
    def get_templates(self) -> Dict[str, Dict[str, Any]]:
        """获取所有角色模板"""
        return self.templates
    
    def get_template(self, template_name: str) -> Optional[Dict[str, Any]]:
        """获取特定模板"""
        return self.templates.get(template_name)
    
    def add_template(self, template_name: str, template_data: Dict[str, Any]):
        """添加新模板"""
        self.templates[template_name] = template_data
        if self.settings.get('auto_save', True):
            self.save_to_config()
    
    def export_character_as_template(self, character_id: str, template_name: str) -> bool:
        """将角色导出为模板"""
        character = self.get_character(character_id)
        if character:
            template_data = asdict(character)
            # 移除不需要的字段
            template_data.pop('character_id', None)
            template_data.pop('created_at', None)
            template_data.pop('updated_at', None)
            
            self.add_template(template_name, template_data)
            return True
        return False
    
    def reload_config(self):
        """重新加载配置文件"""
        self.load_from_config()
        
    def get_config_info(self) -> Dict[str, Any]:
        """获取配置信息"""
        return {
            'config_file': self.config_file,
            'characters_count': len(self.characters),
            'templates_count': len(self.templates),
            'settings': self.settings,
            'backup_exists': os.path.exists(f"{self.config_file}.backup")
        }

class ConversationMemory:
    """对话记忆管理"""
    
    def __init__(self, redis_url: str = None, max_history: int = 50):
        self.redis_client = None
        if redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                # 测试连接
                self.redis_client.ping()
                print("Redis连接成功")
            except Exception as e:
                print(f"Redis连接失败，将使用内存存储: {e}")
                self.redis_client = None
        self.max_history = max_history
        self.memory_cache: Dict[str, List[Dict[str, Any]]] = {}
    
    def add_message(self, session_id: str, role: str, content: str, character_id: str = None):
        """添加消息到对话历史"""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "character_id": character_id
        }
        
        if session_id not in self.memory_cache:
            self.memory_cache[session_id] = []
        
        self.memory_cache[session_id].append(message)
        
        # 限制历史记录长度
        if len(self.memory_cache[session_id]) > self.max_history:
            self.memory_cache[session_id] = self.memory_cache[session_id][-self.max_history:]
        
        # 保存到Redis（如果可用）
        if self.redis_client:
            try:
                key = f"conversation:{session_id}"
                data = json.dumps(self.memory_cache[session_id], ensure_ascii=False)
                self.redis_client.setex(key, 604800, data)  # 7天过期
            except Exception as e:
                print(f"保存对话到Redis失败: {e}")
    
    def get_history(self, session_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """获取对话历史"""
        if session_id in self.memory_cache:
            history = self.memory_cache[session_id]
        elif self.redis_client:
            # 从Redis加载
            try:
                key = f"conversation:{session_id}"
                data = self.redis_client.get(key)
                if data:
                    history = json.loads(data.decode('utf-8'))
                    self.memory_cache[session_id] = history
                else:
                    history = []
            except Exception as e:
                print(f"从Redis加载对话失败: {e}")
                history = []
        else:
            history = []
        
        if limit:
            return history[-limit:]
        return history
    
    def clear_history(self, session_id: str):
        """清除对话历史"""
        if session_id in self.memory_cache:
            del self.memory_cache[session_id]
        
        if self.redis_client:
            try:
                key = f"conversation:{session_id}"
                self.redis_client.delete(key)
            except Exception as e:
                print(f"从Redis清除对话失败: {e}")
    
    def get_character_memory(self, session_id: str, character_id: str) -> List[Dict[str, Any]]:
        """获取特定角色的记忆"""
        history = self.get_history(session_id)
        return [msg for msg in history if msg.get('character_id') == character_id or msg['role'] == 'user']

class CharacterConsistencyManager:
    """角色一致性管理器"""
    
    def __init__(self, character_manager: CharacterManager, memory_manager: ConversationMemory):
        self.character_manager = character_manager
        self.memory_manager = memory_manager
    
    def build_context_messages(self, session_id: str, character_id: str, user_input: str, max_tokens: int = 3000) -> List[Dict[str, str]]:
        """构建包含角色设定和历史记忆的上下文消息"""
        character = self.character_manager.get_character(character_id)
        if not character:
            raise ValueError(f"角色 {character_id} 不存在")
        
        # 系统消息（角色设定）
        messages = [{
            "role": "system",
            "content": character.to_system_prompt()
        }]
        
        # 获取对话历史
        history = self.memory_manager.get_character_memory(session_id, character_id)
        
        # 添加历史消息（保留最近的对话）
        for msg in history[-10:]:  # 最近10条消息
            if msg['role'] in ['user', 'assistant']:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })
        
        # 添加当前用户输入
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        return messages
    
    def enhance_response_consistency(self, response: str, character_id: str, session_id: str) -> str:
        """增强回复的角色一致性"""
        character = self.character_manager.get_character(character_id)
        if not character:
            return response
        
        # 检查回复是否符合角色设定
        if self._check_character_consistency(response, character):
            return response
        
        # 如果不符合，添加角色提醒前缀
        enhanced_response = f"[作为{character.name}] {response}"
        return enhanced_response
    
    def _check_character_consistency(self, response: str, character: CharacterProfile) -> bool:
        """检查回复是否符合角色设定（简单实现）"""
        # 这里可以实现更复杂的一致性检查逻辑
        # 例如检查语言风格、性格特点等
        
        # 简单实现：检查是否包含角色名称或特征词汇
        character_keywords = [character.name] + character.personality
        
        for keyword in character_keywords:
            if keyword in response:
                return True
        
        # 如果回复过于简短或通用，可能不符合角色设定
        if len(response) < 10:
            return False
        
        return True

# 角色创建向导
class CharacterCreationWizard:
    """角色创建向导"""
    
    @staticmethod
    def create_character_interactive() -> Dict[str, Any]:
        """交互式创建角色"""
        print("=== 虚拟AI角色创建向导 ===\n")
        
        character_data = {}
        
        # 基本信息
        character_data['name'] = input("角色姓名: ")
        character_data['identity'] = input("角色身份/职业: ")
        character_data['background'] = input("背景故事: ")
        
        # 性格特点
        print("\n请输入角色性格特点（用逗号分隔）:")
        personality_input = input("性格特点: ")
        character_data['personality'] = [trait.strip() for trait in personality_input.split(',')]
        
        character_data['language_style'] = input("语言风格描述: ")
        
        # 行为准则
        print("\n请输入行为准则（用分号分隔）:")
        rules_input = input("行为准则: ")
        character_data['behavior_rules'] = [rule.strip() for rule in rules_input.split(';')]
        
        character_data['memory_requirements'] = input("记忆要求: ")
        character_data['avatar'] = input("角色头像emoji (可选): ") or "🤖"
        
        return character_data
    
    @staticmethod
    def create_from_template(template_name: str, character_manager: CharacterManager = None) -> Dict[str, Any]:
        """从模板创建角色"""
        # 如果传入了character_manager，从其模板中获取
        if character_manager:
            template = character_manager.get_template(template_name)
            if template:
                return template.copy()
        
        # 否则使用默认模板
        templates = {
            "doctor": {
                "name": "AI医生",
                "identity": "专业的医疗顾问",
                "background": "拥有丰富医疗经验的AI医生，致力于为用户提供专业的健康建议。",
                "personality": ["专业", "耐心", "细心", "负责任"],
                "language_style": "专业术语与通俗解释相结合，语气温和",
                "behavior_rules": [
                    "优先询问用户症状",
                    "提供专业建议但不替代正式诊断",
                    "关注用户身体健康"
                ],
                "memory_requirements": "记住用户的健康状况和咨询历史",
                "avatar": "👨‍⚕️"
            },
            "teacher": {
                "name": "AI老师",
                "identity": "耐心的教育工作者",
                "background": "专业的教育AI，擅长各科目教学，注重启发式教育。",
                "personality": ["耐心", "博学", "善于启发", "鼓励"],
                "language_style": "简单易懂，善用比喻和例子",
                "behavior_rules": [
                    "用生动例子解释概念",
                    "鼓励用户思考",
                    "保持积极正面的态度"
                ],
                "memory_requirements": "记住用户的学习进度和兴趣点",
                "avatar": "👩‍🏫"
            },
            "assistant": {
                "name": "AI助手",
                "identity": "全能的个人助手",
                "background": "智能的个人助手，能够协助处理各种日常事务和问题。",
                "personality": ["高效", "友好", "可靠", "贴心"],
                "language_style": "简洁明了，友好亲切",
                "behavior_rules": [
                    "优先理解用户需求",
                    "提供实用的解决方案",
                    "保持服务态度"
                ],
                "memory_requirements": "记住用户的偏好和常用需求",
                "avatar": "🤖"
            }
        }
        
        return templates.get(template_name, templates["assistant"])

if __name__ == "__main__":
    # 示例使用
    character_manager = CharacterManager()
    memory_manager = ConversationMemory()
    consistency_manager = CharacterConsistencyManager(character_manager, memory_manager)
    
    # 创建角色示例
    wizard = CharacterCreationWizard()
    
    print("可用模板:", ["doctor", "teacher", "assistant"])
    template_choice = input("选择模板 (或按Enter跳过): ").strip()
    
    if template_choice:
        character_data = wizard.create_from_template(template_choice)
    else:
        character_data = wizard.create_character_interactive()
    
    # 创建角色
    character = character_manager.create_character(character_data)
    print(f"\n角色 '{character.name}' 创建成功!")
    print(f"角色ID: {character.character_id}")
    print(f"系统提示词:\n{character.to_system_prompt()}")