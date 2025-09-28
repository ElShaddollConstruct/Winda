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
    """多角色对话引擎 - 12人标准局狼人杀"""
    
    def __init__(self):
        # 狼人杀12人标准局角色配置 (4狼人 + 4神职 + 4平民)
        self.werewolf_roles = {
            "狼人": {"team": "werewolf", "description": "狼人阵营，夜晚杀人，白天伪装", "count": 4},
            "预言家": {"team": "good", "description": "神职，夜晚查验身份", "count": 1},
            "女巫": {"team": "good", "description": "神职，拥有解药和毒药各一瓶", "count": 1},
            "猎人": {"team": "good", "description": "神职，被狼杀或投票出局可开枪", "count": 1},
            "白痴": {"team": "good", "description": "神职，被投票出局可翻牌免死", "count": 1},
            "平民": {"team": "good", "description": "好人阵营，无特殊技能", "count": 4}
        }
        
        # 游戏阶段定义
        self.game_phases = {
            "first_night": "首夜",
            "first_day": "第一天白天",
            "sheriff_election": "警长竞选",
            "day_discussion": "白天讨论",
            "voting": "投票放逐", 
            "night": "夜晚",
            "game_end": "游戏结束"
        }
    
    def create_werewolf_scenario(self, session_id: str, character_ids: List[str]) -> bool:
        """创建12人标准局狼人杀场景"""
        
        if len(character_ids) != 12:
            return False
        
        # 获取角色信息
        players = []
        for char_id in character_ids:
            char = character_manager.get_character(char_id)
            if not char:
                return False
                
            player = GamePlayer(
                character_id=char.character_id,
                character_name=char.name,
                character_avatar=char.avatar,
                role=""  # 待分配
            )
            players.append(player)
        
        # 12人标准局角色分配
        standard_roles = (
            ["狼人"] * 4 +
            ["预言家"] * 1 +
            ["女巫"] * 1 +
            ["猎人"] * 1 +
            ["白痴"] * 1 +
            ["平民"] * 4
        )
        
        # 随机分配角色
        random.shuffle(standard_roles)
        for i, player in enumerate(players):
            player.role = standard_roles[i]
        
        # 创建场景状态
        scenario = ScenarioState(
            scenario_type="werewolf",
            phase="first_night",
            players=players
        )
        
        # 初始化游戏状态
        scenario.game_state = {
            "sheriff": None,  # 警长
            "sheriff_candidates": [],  # 警长候选人
            "night_actions": {},  # 夜晚行动记录
            "witch_potions": {"antidote": True, "poison": True},  # 女巫药剂状态
            "killed_tonight": None,  # 今晚被杀的人
            "saved_tonight": None,  # 今晚被救的人
            "poisoned_tonight": None,  # 今晚被毒的人
            "seer_checks": [],  # 预言家查验记录
            "hunter_can_shoot": True,  # 猎人是否能开枪
            "idiot_revealed": False  # 白痴是否已翻牌
        }
        
        multi_scenarios[session_id] = scenario
        return True
    
    def get_players_by_role(self, session_id: str, role: str) -> List[GamePlayer]:
        """获取指定角色的玩家列表"""
        if session_id not in multi_scenarios:
            return []
        
        scenario = multi_scenarios[session_id]
        return [p for p in scenario.players if p.role == role and p.is_alive]
    
    def get_alive_players(self, session_id: str) -> List[GamePlayer]:
        """获取存活玩家列表"""
        if session_id not in multi_scenarios:
            return []
        
        scenario = multi_scenarios[session_id]
        return [p for p in scenario.players if p.is_alive]
    
    def get_next_speaker(self, session_id: str) -> Optional[GamePlayer]:
        """获取下一个发言的玩家"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        alive_players = self.get_alive_players(session_id)
        
        if not alive_players:
            return None
        
        # 根据当前阶段决定发言顺序
        if scenario.phase == "sheriff_election":
            # 警长竞选阶段：候选人发言
            candidates = scenario.game_state.get("sheriff_candidates", [])
            spoken_candidates = len([log for log in scenario.scenario_log 
                                   if log.get('phase') == 'sheriff_election' 
                                   and log.get('round') == scenario.round_count])
            if spoken_candidates < len(candidates):
                candidate_id = candidates[spoken_candidates]
                return next((p for p in alive_players if p.character_id == candidate_id), None)
        
        elif scenario.phase == "day_discussion":
            # 白天讨论：按顺序发言
            current_round = len([log for log in scenario.scenario_log 
                               if log.get('phase') == 'day_discussion' 
                               and log.get('round') == scenario.round_count])
            
            if current_round < len(alive_players):
                # 警长先发言，然后按位置顺序
                sheriff_id = scenario.game_state.get("sheriff")
                if sheriff_id and current_round == 0:
                    sheriff = next((p for p in alive_players if p.character_id == sheriff_id), None)
                    if sheriff:
                        return sheriff
                
                # 其他玩家按顺序发言
                non_sheriff_players = [p for p in alive_players if p.character_id != sheriff_id]
                if current_round - (1 if sheriff_id else 0) < len(non_sheriff_players):
                    return non_sheriff_players[current_round - (1 if sheriff_id else 0)]
        
        return None
    
    def process_player_message(self, session_id: str, player: GamePlayer, auto_generate: bool = True) -> Optional[str]:
        """处理玩家消息（自动生成或用户输入）"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        
        if not auto_generate:
            return None  # 等待用户输入
        
        # 自动生成AI回复
        prompt = self._build_scenario_prompt(player, scenario, session_id)
        
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
    
    def _build_scenario_prompt(self, player: GamePlayer, scenario: ScenarioState, session_id: str = None) -> str:
        """构建场景提示词"""
        
        if scenario.scenario_type == "werewolf":
            return self._build_werewolf_prompt(player, scenario, session_id)
        
        return "请发言。"
    
    def _build_werewolf_prompt(self, player: GamePlayer, scenario: ScenarioState, session_id: str = None) -> str:
        """构建狼人杀提示词"""
        
        if not session_id:
            # 从scenario中获取session_id，如果没有则使用第一个scenario的key
            session_id = next(iter(multi_scenarios.keys())) if multi_scenarios else ''
        
        alive_players = self.get_alive_players(session_id)
        alive_names = [p.character_name for p in alive_players]
        werewolves = self.get_players_by_role(session_id, "狼人")
        werewolf_names = [p.character_name for p in werewolves if p.character_id != player.character_id]
        
        base_info = f"""【狼人杀12人标准局】
你的身份：{player.role}
你的阵营：{self.werewolf_roles[player.role]['team']}
存活玩家：{', '.join(alive_names)}
已淘汰玩家：{', '.join(scenario.eliminated_players) if scenario.eliminated_players else '无'}
"""
        
        # 狼人可以知道队友身份
        if player.role == "狼人" and werewolf_names:
            base_info += f"\n你的狼人队友：{', '.join(werewolf_names)}"
        
        if scenario.phase == "first_night":
            if player.role == "狼人":
                prompt = base_info + f"""\n\n【首夜-狼人行动】
你们狼人需要选择击杀一名玩家。请与队友商量并选择目标。
目标建议：优先击杀神职（预言家、女巫、猎人）。

请回复：我们选择击杀【玩家姓名】。保持角色性格。"""
            elif player.role == "预言家":
                prompt = base_info + f"""\n\n【首夜-预言家查验】
你可以查验一名玩家的身份（好人或狼人）。
建议：选择一个你想重点关注的玩家。

请回复：我查验【玩家姓名】。保持角色性格。"""
            elif player.role == "女巫":
                killed_player = scenario.game_state.get("killed_tonight")
                prompt = base_info + f"""\n\n【首夜-女巫行动】
今晚被狼人击杀的是：{killed_player or '暂未确定'}
你有解药和毒药各一瓶。首夜你可以自救。

选项：
1. 使用解药救人（回复：我使用解药救【玩家姓名】）
2. 使用毒药毒人（回复：我使用毒药毒【玩家姓名】）
3. 不使用药剂（回复：我不使用药剂）

注意：一晚只能使用一瓶药。保持角色性格。"""
            else:
                prompt = base_info + f"\n\n【首夜】\n你在首夜无特殊行动，请耐心等待白天到来。可以思考明天的策略。"
        
        elif scenario.phase == "sheriff_election":
            prompt = base_info + f"""\n\n【警长竞选发言】
你正在竞选警长。警长拥有1.5票的投票权和归票权。
根据你的身份制定策略：
- 如果你是神职：可以考虑跳出身份获得信任
- 如果你是狼人：伪装身份，争取获得警长职位
- 如果你是平民：展现逻辑能力，帮助好人

请发表竞选演说，展现你的价值。发言限100字内。"""
        
        elif scenario.phase == "day_discussion":
            prompt = base_info + f"""\n\n【第{scenario.round_count}天白天讨论】
根据昨晚的情况和之前的信息进行分析推理：

策略提示：
- 神职：合理时机跳出身份，报告信息
- 狼人：隐藏身份，误导好人，带节奏
- 平民：分析信息，找出狼人

请发表你的分析和看法。发言限120字内。"""
        
        elif scenario.phase == "voting":
            other_players = [p for p in alive_players if p.character_id != player.character_id]
            prompt = base_info + f"""\n\n【投票放逐阶段】
你需要投票放逐一名玩家。
可投票对象：{', '.join([p.character_name for p in other_players])}

根据你的身份和讨论内容选择：
- 好人：投票给最可疑的狼人
- 狼人：投票给对你威胁最大的好人

回复格式：我投票给【玩家姓名】，理由是..."""
        
        elif scenario.phase == "night":
            if player.role == "狼人":
                prompt = base_info + f"""\n\n【夜晚-狼人击杀】
选择今晚要击杀的玩家。建议优先击杀：
1. 已确认的神职
2. 逻辑能力强的好人
3. 对你们有威胁的玩家

请回复：我们击杀【玩家姓名】。"""
            elif player.role == "预言家":
                prompt = base_info + f"""\n\n【夜晚-预言家查验】
选择要查验的玩家。建议查验：
1. 发言可疑的玩家
2. 需要确认身份的关键玩家

请回复：我查验【玩家姓名】。"""
            elif player.role == "女巫":
                killed_player = scenario.game_state.get("killed_tonight")
                has_antidote = scenario.game_state.get("witch_potions", {}).get("antidote", False)
                has_poison = scenario.game_state.get("witch_potions", {}).get("poison", False)
                
                prompt = base_info + f"""\n\n【夜晚-女巫行动】
今晚被击杀的玩家：{killed_player or '无人被杀'}
你的药剂状态：解药{'可用' if has_antidote else '已用'}，毒药{'可用' if has_poison else '已用'}

选项：
1. 使用解药救人（如果有解药）
2. 使用毒药毒人（如果有毒药）
3. 不使用药剂

请回复你的选择。"""
            else:
                prompt = base_info + f"\n\n【夜晚】\n你在夜晚无行动，请等待白天到来。"
        
        else:
            prompt = base_info + f"\n\n请根据当前阶段进行发言。"
        
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
        
        if scenario.phase == "first_night":
            # 首夜结束，进入第一天
            scenario.phase = "first_day"
            scenario.round_count = 1
            return True
            
        elif scenario.phase == "first_day":
            # 第一天进入警长竞选
            scenario.phase = "sheriff_election"
            return True
            
        elif scenario.phase == "sheriff_election":
            # 警长竞选结束，进入白天讨论
            scenario.phase = "day_discussion"
            return True
        
        elif scenario.phase == "day_discussion":
            # 白天讨论结束，进入投票
            scenario.phase = "voting"
            return True
        
        elif scenario.phase == "voting":
            # 投票结束，进入夜晚
            scenario.phase = "night"
            scenario.round_count += 1
            return True
        
        elif scenario.phase == "night":
            # 夜晚结束，开始新一天讨论
            scenario.phase = "day_discussion"
            return True
        
        return False
    
    def check_game_end(self, session_id: str) -> Optional[str]:
        """检查游戏是否结束"""
        
        if session_id not in multi_scenarios:
            return None
        
        scenario = multi_scenarios[session_id]
        
        if scenario.scenario_type == "werewolf":
            alive_players = self.get_alive_players(session_id)
            werewolves = [p for p in alive_players if p.role == "狼人"]
            good_players = [p for p in alive_players if p.role != "狼人"]
            
            # 狼人全部出局，好人获胜
            if not werewolves:
                scenario.is_active = False
                return "🎉 好人阵营获胜！所有狼人已被淘汰。"
            
            # 狼人数量达到或超过好人数量，狼人获胜
            if len(werewolves) >= len(good_players):
                scenario.is_active = False
                return "🐺 狼人阵营获胜！狼人数量达到或超过好人数量。"
            
            # 检查特殊胜利条件
            # 如果所有神职都被淘汰
            gods = [p for p in alive_players if p.role in ["预言家", "女巫", "猎人", "白痴"]]
            if not gods and len(alive_players) > 0:
                scenario.is_active = False
                return "🐺 狼人阵营获胜！所有神职已被淘汰（屠神胜利）。"
            
            # 如果所有平民都被淘汰
            civilians = [p for p in alive_players if p.role == "平民"]
            if not civilians and len(alive_players) > 0 and len(werewolves) > 0:
                scenario.is_active = False
                return "🐺 狼人阵营获胜！所有平民已被淘汰（屠民胜利）。"
        
        return None
    
    def process_night_actions(self, session_id: str):
        """处理夜晚技能行动"""
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        if scenario.phase != "night" and scenario.phase != "first_night":
            return
        
        # 重置夜晚行动状态
        scenario.game_state["killed_tonight"] = None
        scenario.game_state["saved_tonight"] = None
        scenario.game_state["poisoned_tonight"] = None
        
        # 1. 狼人击杀行动
        self._process_werewolf_kill(session_id)
        
        # 2. 预言家查验行动
        self._process_seer_check(session_id)
        
        # 3. 女巫行动
        self._process_witch_action(session_id)
        
        # 处理死亡
        self._process_deaths(session_id)
    
    def _process_werewolf_kill(self, session_id: str):
        """处理狼人击杀"""
        scenario = multi_scenarios[session_id]
        werewolves = self.get_players_by_role(session_id, "狼人")
        
        if not werewolves:
            return
        
        # 获取可击杀的目标（非狼人）
        good_players = [p for p in self.get_alive_players(session_id) if p.role != "狼人"]
        
        if not good_players:
            return
        
        # 狼人的智能击杀策略
        target = self._choose_werewolf_target(good_players, scenario)
        scenario.game_state["killed_tonight"] = target.character_name
        
        # 记录狼人行动
        scenario.scenario_log.append({
            "phase": "werewolf_kill",
            "round": scenario.round_count,
            "action": f"狼人选择击杀{target.character_name}",
            "timestamp": datetime.now().isoformat()
        })
    
    def _choose_werewolf_target(self, good_players: List[GamePlayer], scenario: ScenarioState) -> GamePlayer:
        """狼人智能选择击杀目标"""
        # 优先级：已知神职 > 可疑神职 > 强势玩家 > 随机
        
        # 1. 如果有已确认的神职，优先击杀
        confirmed_gods = []
        for player in good_players:
            if player.role in ["预言家", "女巫", "猎人", "白痴"]:
                # 模拟：如果这个神职在之前的游戏中暴露了身份
                if random.random() < 0.3:  # 30%概率被狼人识破
                    confirmed_gods.append(player)
        
        if confirmed_gods:
            return random.choice(confirmed_gods)
        
        # 2. 避免击杀白痴（除非是最后的神职）
        non_idiot_players = [p for p in good_players if p.role != "白痴"]
        if non_idiot_players and len(good_players) > 2:
            good_players = non_idiot_players
        
        # 3. 随机选择
        return random.choice(good_players)
    
    def _process_seer_check(self, session_id: str):
        """处理预言家查验"""
        scenario = multi_scenarios[session_id]
        seers = self.get_players_by_role(session_id, "预言家")
        
        if not seers:
            return
        
        seer = seers[0]
        alive_players = self.get_alive_players(session_id)
        other_players = [p for p in alive_players if p.character_id != seer.character_id]
        
        if not other_players:
            return
        
        # 预言家智能查验策略
        target = self._choose_seer_target(other_players, scenario)
        result = "狼人" if target.role == "狼人" else "好人"
        
        scenario.game_state["seer_checks"].append({
            "target": target.character_name,
            "result": result,
            "night": scenario.round_count
        })
        
        # 记录预言家行动
        scenario.scenario_log.append({
            "phase": "seer_check",
            "round": scenario.round_count,
            "action": f"预言家查验{target.character_name}，结果是{result}",
            "timestamp": datetime.now().isoformat()
        })
    
    def _choose_seer_target(self, other_players: List[GamePlayer], scenario: ScenarioState) -> GamePlayer:
        """预言家智能选择查验目标"""
        # 已查验过的玩家
        checked_names = [check["target"] for check in scenario.game_state.get("seer_checks", [])]
        unchecked_players = [p for p in other_players if p.character_name not in checked_names]
        
        if unchecked_players:
            # 优先查验未检查过的玩家
            return random.choice(unchecked_players)
        else:
            # 如果都检查过了，随机选择
            return random.choice(other_players)
    
    def _process_witch_action(self, session_id: str):
        """处理女巫行动"""
        scenario = multi_scenarios[session_id]
        witches = self.get_players_by_role(session_id, "女巫")
        
        if not witches:
            return
        
        witch = witches[0]
        killed_player = scenario.game_state.get("killed_tonight")
        has_antidote = scenario.game_state.get("witch_potions", {}).get("antidote", False)
        has_poison = scenario.game_state.get("witch_potions", {}).get("poison", False)
        
        # 女巫智能决策
        if killed_player and has_antidote:
            # 救人策略
            if self._should_witch_save(killed_player, scenario, session_id):
                scenario.game_state["saved_tonight"] = killed_player
                scenario.game_state["witch_potions"]["antidote"] = False
                
                scenario.scenario_log.append({
                    "phase": "witch_save",
                    "round": scenario.round_count,
                    "action": f"女巫使用解药救了{killed_player}",
                    "timestamp": datetime.now().isoformat()
                })
        
        # 毒人策略（如果没有救人且有毒药）
        if has_poison and scenario.game_state.get("saved_tonight") != killed_player:
            poison_target = self._choose_poison_target(session_id, scenario)
            if poison_target:
                scenario.game_state["poisoned_tonight"] = poison_target.character_name
                scenario.game_state["witch_potions"]["poison"] = False
                
                scenario.scenario_log.append({
                    "phase": "witch_poison",
                    "round": scenario.round_count,
                    "action": f"女巫使用毒药毒了{poison_target.character_name}",
                    "timestamp": datetime.now().isoformat()
                })
    
    def _should_witch_save(self, killed_player: str, scenario: ScenarioState, session_id: str) -> bool:
        """女巫是否应该救人"""
        # 首夜一般会救人
        if scenario.phase == "first_night":
            return random.random() < 0.7  # 70%概率救人
        
        # 后续夜晚根据情况决定
        # 如果被杀的是重要角色，更倾向于救
        killed_player_obj = next((p for p in scenario.players if p.character_name == killed_player), None)
        if killed_player_obj and killed_player_obj.role in ["预言家", "猎人"]:
            return random.random() < 0.8  # 80%概率救重要角色
        
        return random.random() < 0.4  # 40%概率救普通玩家
    
    def _choose_poison_target(self, session_id: str, scenario: ScenarioState) -> Optional[GamePlayer]:
        """选择毒杀目标"""
        alive_players = self.get_alive_players(session_id)
        werewolves = [p for p in alive_players if p.role == "狼人"]
        
        # 如果确定知道狼人身份，毒狼人
        if werewolves and random.random() < 0.6:  # 60%概率能识别出狼人
            return random.choice(werewolves)
        
        # 否则可能不用毒药或毒错人
        if random.random() < 0.3:  # 30%概率使用毒药
            # 排除自己和已经被杀的人
            witch = self.get_players_by_role(session_id, "女巫")[0]
            killed_tonight = scenario.game_state.get("killed_tonight")
            
            possible_targets = [p for p in alive_players 
                              if p.character_id != witch.character_id 
                              and p.character_name != killed_tonight]
            
            if possible_targets:
                return random.choice(possible_targets)
        
        return None
    
    def _process_deaths(self, session_id: str):
        """处理死亡结算"""
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        killed = scenario.game_state.get("killed_tonight")
        saved = scenario.game_state.get("saved_tonight")
        poisoned = scenario.game_state.get("poisoned_tonight")
        
        deaths = []
        
        # 处理击杀（如果未被救）
        if killed and killed != saved:
            player = next((p for p in scenario.players if p.character_name == killed), None)
            if player and player.is_alive:
                player.is_alive = False
                scenario.eliminated_players.append(killed)
                deaths.append(f"{killed}被狼人击杀")
        
        # 处理毒杀
        if poisoned:
            player = next((p for p in scenario.players if p.character_name == poisoned), None)
            if player and player.is_alive:
                player.is_alive = False
                scenario.eliminated_players.append(poisoned)
                deaths.append(f"{poisoned}被女巫毒杀")
        
        # 记录死亡信息到日志
        if deaths:
            scenario.scenario_log.append({
                "phase": "night_result",
                "round": scenario.round_count,
                "deaths": deaths,
                "timestamp": datetime.now().isoformat()
            })
    
    def handle_sheriff_election(self, session_id: str):
        """处理警长竞选"""
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        alive_players = self.get_alive_players(session_id)
        
        # 随机选择2-4个候选人
        candidate_count = min(random.randint(2, 4), len(alive_players))
        candidates = random.sample(alive_players, candidate_count)
        scenario.game_state["sheriff_candidates"] = [c.character_id for c in candidates]
        
        # 记录竞选开始
        scenario.scenario_log.append({
            "phase": "sheriff_election_start",
            "round": scenario.round_count,
            "candidates": [c.character_name for c in candidates],
            "timestamp": datetime.now().isoformat()
        })
    
    def vote_for_sheriff(self, session_id: str):
        """警长投票"""
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        candidates = scenario.game_state.get("sheriff_candidates", [])
        alive_players = self.get_alive_players(session_id)
        non_candidates = [p for p in alive_players if p.character_id not in candidates]
        
        # 简化投票：随机选择警长
        if candidates:
            sheriff_id = random.choice(candidates)
            scenario.game_state["sheriff"] = sheriff_id
            sheriff = next((p for p in alive_players if p.character_id == sheriff_id), None)
            
            scenario.scenario_log.append({
                "phase": "sheriff_elected",
                "round": scenario.round_count,
                "sheriff": sheriff.character_name if sheriff else "未知",
                "timestamp": datetime.now().isoformat()
            })
    
    def handle_voting_phase(self, session_id: str):
        """处理投票放逐阶段"""
        if session_id not in multi_scenarios:
            return
        
        scenario = multi_scenarios[session_id]
        alive_players = self.get_alive_players(session_id)
        
        # 简化投票：随机选择被放逐者
        if alive_players:
            eliminated = random.choice(alive_players)
            eliminated.is_alive = False
            scenario.eliminated_players.append(eliminated.character_name)
            
            # 检查猎人技能
            if eliminated.role == "猎人" and scenario.game_state.get("hunter_can_shoot", True):
                # 猎人开枪带走一人
                other_alive = [p for p in self.get_alive_players(session_id) if p.character_id != eliminated.character_id]
                if other_alive:
                    shot_target = random.choice(other_alive)
                    shot_target.is_alive = False
                    scenario.eliminated_players.append(shot_target.character_name)
                    
                    scenario.scenario_log.append({
                        "phase": "hunter_shoot",
                        "round": scenario.round_count,
                        "hunter": eliminated.character_name,
                        "target": shot_target.character_name,
                        "timestamp": datetime.now().isoformat()
                    })
            
            # 检查白痴技能
            elif eliminated.role == "白痴" and not scenario.game_state.get("idiot_revealed", False):
                # 白痴翻牌免死但失去投票权
                eliminated.is_alive = True
                scenario.eliminated_players.remove(eliminated.character_name)
                scenario.game_state["idiot_revealed"] = True
                
                scenario.scenario_log.append({
                    "phase": "idiot_reveal",
                    "round": scenario.round_count,
                    "idiot": eliminated.character_name,
                    "timestamp": datetime.now().isoformat()
                })
            
            scenario.scenario_log.append({
                "phase": "voting_result",
                "round": scenario.round_count,
                "eliminated": eliminated.character_name,
                "role": eliminated.role,
                "timestamp": datetime.now().isoformat()
            })

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