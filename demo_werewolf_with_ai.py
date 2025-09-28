#!/usr/bin/env python3
"""
12人标准局狼人杀游戏 - 完整AI发言演示
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import multi_engine, character_manager, multi_scenarios, chatbot
import uuid
import json

def simulate_ai_speech(session_id: str, player, phase: str, context: str = ""):
    """模拟AI角色发言"""
    scenario = multi_scenarios[session_id]
    prompt = multi_engine._build_werewolf_prompt(player, scenario, session_id)
    
    if context:
        prompt += f"\n\n当前情况：{context}"
    
    try:
        # 使用角色的AI发言
        chatbot.set_character(player.character_id)
        response = chatbot.get_ai_response(prompt, session_id)
        return response
    except Exception as e:
        # 如果AI调用失败，返回模拟发言
        return generate_mock_speech(player, phase)

def generate_mock_speech(player, phase: str) -> str:
    """生成模拟发言（当AI不可用时）"""
    speeches = {
        "sheriff_election": {
            "福尔摩斯": "根据我的观察，逻辑推理是警长必备的能力。我愿意承担这个责任。",
            "艾莎公主": "在我看来，警长需要公正地处理问题。我有这个能力。",
            "赵云": "某家虽是武夫，但忠义之心可鉴日月，愿为大家服务。",
            "牛顿": "从科学角度分析，我具备必要的逻辑思维能力。"
        },
        "day_discussion": {
            "福尔摩斯": "显而易见，昨晚的死亡模式告诉我们很多信息。让我分析一下...",
            "戈登主厨": "这简直是灾难！有人在撒谎，我能从他们的表情看出来。",
            "程序员爱丽丝": "需要debug一下逻辑，有人的发言存在逻辑错误。",
            "赫敏": "据我所知，这种情况下通常意味着..."
        },
        "voting": {
            "商业大亨": "让我们谈谈这个deal，我投票给最可疑的那个人。",
            "天鹅舞者": "就像舞台上的表演，我感觉到了虚假的气息。",
            "玄奘法师": "阿弥陀佛，贫僧虽不愿伤害任何人，但为了正义..."
        }
    }
    
    role_speeches = speeches.get(phase, {})
    return role_speeches.get(player.character_name, f"{player.character_name}进行了发言...")

def demo_werewolf_game_with_speech():
    """演示带AI发言的12人狼人杀游戏"""
    
    print("🎮 12人标准局狼人杀 - AI发言演示")
    print("=" * 60)
    
    # 获取12个角色
    characters = character_manager.list_characters()
    character_ids = [char['character_id'] for char in characters[:12]]
    
    # 创建游戏会话
    session_id = str(uuid.uuid4())
    
    # 创建狼人杀场景
    multi_engine.create_werewolf_scenario(session_id, character_ids)
    scenario = multi_scenarios[session_id]
    
    # 显示角色分配
    print("🎲 角色分配：")
    werewolves = [p for p in scenario.players if p.role == "狼人"]
    gods = [p for p in scenario.players if p.role in ["预言家", "女巫", "猎人", "白痴"]]
    civilians = [p for p in scenario.players if p.role == "平民"]
    
    print(f"\n🐺 狼人阵营：{', '.join([p.character_name for p in werewolves])}")
    print(f"✨ 神职阵营：{', '.join([f'{p.character_name}({p.role})' for p in gods])}")
    print(f"👥 平民阵营：{', '.join([p.character_name for p in civilians])}")
    
    print("\n" + "=" * 60)
    print("🎬 游戏开始 - 展示关键发言环节")
    
    # 1. 首夜阶段（静默处理）
    print("\n🌃 【首夜】- 神职角色行动中...")
    multi_engine.process_night_actions(session_id)
    multi_engine.advance_phase(session_id)  # first_night -> first_day
    multi_engine.advance_phase(session_id)  # first_day -> sheriff_election
    
    # 显示夜晚结果
    if scenario.eliminated_players:
        print(f"   💀 昨夜死亡：{', '.join(scenario.eliminated_players[-1:])}")
    else:
        print("   ✅ 昨夜平安")
    
    # 2. 警长竞选演示
    print("\n👮 【警长竞选】")
    multi_engine.handle_sheriff_election(session_id)
    
    candidates = scenario.game_state.get("sheriff_candidates", [])
    candidate_players = [p for p in scenario.players if p.character_id in candidates]
    
    print(f"   竞选候选人：{', '.join([p.character_name for p in candidate_players])}")
    print("\n   📢 候选人发言：")
    
    for i, candidate in enumerate(candidate_players, 1):
        speech = simulate_ai_speech(session_id, candidate, "sheriff_election")
        print(f"   {i}. {candidate.character_avatar} {candidate.character_name}：")
        print(f"      \"{speech}\"")
        print()
    
    multi_engine.vote_for_sheriff(session_id)
    sheriff_id = scenario.game_state.get("sheriff")
    sheriff = next((p for p in scenario.players if p.character_id == sheriff_id), None)
    print(f"   🏆 当选警长：{sheriff.character_name if sheriff else '无'}")
    
    multi_engine.advance_phase(session_id)  # sheriff_election -> day_discussion
    
    # 3. 白天讨论演示
    print("\n💬 【白天讨论】")
    alive_players = multi_engine.get_alive_players(session_id)
    
    # 选择几个有代表性的角色进行发言演示
    speakers = alive_players[:6]  # 前6个存活玩家发言
    
    context = f"警长是{sheriff.character_name if sheriff else '无'}，"
    if scenario.eliminated_players:
        context += f"昨夜{scenario.eliminated_players[-1]}死亡"
    
    print("   🗣️ 玩家发言：")
    for i, speaker in enumerate(speakers, 1):
        speech = simulate_ai_speech(session_id, speaker, "day_discussion", context)
        print(f"   {i}. {speaker.character_avatar} {speaker.character_name}({speaker.role})：")
        print(f"      \"{speech}\"")
        print()
    
    multi_engine.advance_phase(session_id)  # day_discussion -> voting
    
    # 4. 投票阶段演示
    print("\n🗳️ 【投票放逐】")
    
    # 选择几个代表性角色展示投票发言
    voters = alive_players[:4]
    print("   📊 投票发言：")
    
    for i, voter in enumerate(voters, 1):
        speech = simulate_ai_speech(session_id, voter, "voting")
        print(f"   {i}. {voter.character_avatar} {voter.character_name}：")
        print(f"      \"{speech}\"")
        print()
    
    # 执行投票
    multi_engine.handle_voting_phase(session_id)
    
    if scenario.eliminated_players:
        eliminated = scenario.eliminated_players[-1]
        print(f"   ⚖️ 投票结果：{eliminated} 被放逐")
        
        # 检查特殊技能触发
        eliminated_player = next((p for p in scenario.players if p.character_name == eliminated), None)
        if eliminated_player:
            if eliminated_player.role == "猎人":
                print(f"   💥 {eliminated}翻牌猎人，开枪技能触发！")
            elif eliminated_player.role == "白痴" and not scenario.game_state.get("idiot_revealed"):
                print(f"   🛡️ {eliminated}翻牌白痴，免死但失去投票权！")
    
    # 5. 检查游戏状态
    print("\n📊 【当前状态】")
    alive_players = multi_engine.get_alive_players(session_id)
    alive_werewolves = [p for p in alive_players if p.role == "狼人"]
    alive_goods = [p for p in alive_players if p.role != "狼人"]
    
    print(f"   存活玩家：{len(alive_players)} 人")
    print(f"   狼人：{len(alive_werewolves)} 人 | 好人：{len(alive_goods)} 人")
    
    # 检查游戏是否结束
    end_message = multi_engine.check_game_end(session_id)
    if end_message:
        print(f"\n🎉 {end_message}")
    else:
        print("\n🌙 游戏继续进入夜晚...")
    
    print("\n" + "=" * 60)
    print("✅ AI发言演示完成！")
    print("\n💡 特色展示：")
    print("   • 12个不同性格的虚拟角色")
    print("   • 符合rule.txt的标准12人局规则")
    print("   • 智能的夜晚技能处理")
    print("   • 角色身份与性格相符的发言风格")
    print("   • 完整的游戏流程：首夜→警长竞选→讨论→投票→夜晚")

if __name__ == "__main__":
    demo_werewolf_game_with_speech()