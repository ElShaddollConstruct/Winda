#!/usr/bin/env python3
"""
12人标准局狼人杀游戏测试脚本
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import multi_engine, character_manager, multi_scenarios
import uuid
import json

def test_werewolf_game():
    """测试12人标准局狼人杀游戏"""
    
    print("🎮 开始测试12人标准局狼人杀游戏")
    print("=" * 50)
    
    # 获取12个角色ID
    characters = character_manager.list_characters()
    if len(characters) < 12:
        print(f"❌ 角色数量不足，当前只有 {len(characters)} 个角色，需要12个")
        return False
    
    # 选择前12个角色
    character_ids = [char['character_id'] for char in characters[:12]]
    selected_chars = [char for char in characters[:12]]
    
    print("🎭 参与游戏的角色:")
    for i, char in enumerate(selected_chars, 1):
        print(f"{i:2d}. {char['avatar']} {char['name']} - {char['identity']}")
    
    # 创建游戏会话
    session_id = str(uuid.uuid4())
    
    # 创建狼人杀场景
    print(f"\n🎯 创建12人标准局狼人杀场景...")
    success = multi_engine.create_werewolf_scenario(session_id, character_ids)
    
    if not success:
        print("❌ 创建游戏场景失败")
        return False
    
    print("✅ 游戏场景创建成功！")
    
    # 显示角色分配
    scenario = multi_scenarios[session_id]
    print("\n🎲 角色分配结果:")
    
    # 按阵营分组显示
    werewolves = [p for p in scenario.players if p.role == "狼人"]
    gods = [p for p in scenario.players if p.role in ["预言家", "女巫", "猎人", "白痴"]]
    civilians = [p for p in scenario.players if p.role == "平民"]
    
    print("\n🐺 狼人阵营 (4人):")
    for wolf in werewolves:
        print(f"   {wolf.character_avatar} {wolf.character_name} - {wolf.role}")
    
    print("\n✨ 神职阵营 (4人):")
    for god in gods:
        print(f"   {god.character_avatar} {god.character_name} - {god.role}")
    
    print("\n👥 平民阵营 (4人):")
    for civilian in civilians:
        print(f"   {civilian.character_avatar} {civilian.character_name} - {civilian.role}")
    
    # 开始游戏流程测试
    print("\n" + "=" * 50)
    print("🌙 开始游戏 - 首夜阶段")
    
    round_count = 0
    max_rounds = 10  # 防止无限循环
    
    while scenario.is_active and round_count < max_rounds:
        round_count += 1
        print(f"\n--- 第 {round_count} 轮 - {scenario.phase} ---")
        
        if scenario.phase == "first_night":
            print("🌃 首夜：神职角色进行技能行动...")
            multi_engine.process_night_actions(session_id)
            multi_engine.advance_phase(session_id)
            
        elif scenario.phase == "first_day":
            print("🌅 第一天白天开始")
            multi_engine.advance_phase(session_id)
            
        elif scenario.phase == "sheriff_election":
            print("👮 警长竞选阶段")
            multi_engine.handle_sheriff_election(session_id)
            multi_engine.vote_for_sheriff(session_id)
            multi_engine.advance_phase(session_id)
            
        elif scenario.phase == "day_discussion":
            print("💬 白天讨论阶段")
            alive_count = len(multi_engine.get_alive_players(session_id))
            print(f"   存活玩家数：{alive_count}")
            multi_engine.advance_phase(session_id)
            
        elif scenario.phase == "voting":
            print("🗳️ 投票放逐阶段")
            multi_engine.handle_voting_phase(session_id)
            multi_engine.advance_phase(session_id)
            
        elif scenario.phase == "night":
            print("🌙 夜晚阶段")
            multi_engine.process_night_actions(session_id)
            multi_engine.advance_phase(session_id)
        
        # 检查游戏是否结束
        end_message = multi_engine.check_game_end(session_id)
        if end_message:
            print(f"\n🎉 游戏结束：{end_message}")
            break
        
        # 显示当前存活状态
        alive_players = multi_engine.get_alive_players(session_id)
        werewolves_alive = [p for p in alive_players if p.role == "狼人"]
        goods_alive = [p for p in alive_players if p.role != "狼人"]
        
        print(f"   存活状态：狼人 {len(werewolves_alive)} 人，好人 {len(goods_alive)} 人")
        
        # 如果轮数过多，强制结束
        if round_count >= max_rounds:
            print(f"\n⚠️ 达到最大轮数限制 ({max_rounds})，测试结束")
            break
    
    # 显示游戏结果统计
    print("\n" + "=" * 50)
    print("📊 游戏统计:")
    print(f"总轮数：{round_count}")
    print(f"存活玩家：{len(multi_engine.get_alive_players(session_id))}")
    print(f"淘汰玩家：{len(scenario.eliminated_players)}")
    
    if scenario.eliminated_players:
        print("\n💀 淘汰顺序:")
        for i, eliminated in enumerate(scenario.eliminated_players, 1):
            print(f"{i}. {eliminated}")
    
    # 显示关键事件日志
    print("\n📜 关键事件:")
    for log in scenario.scenario_log[-10:]:  # 显示最后10个事件
        if log.get("action"):
            print(f"  {log['phase']}: {log['action']}")
    
    return True

if __name__ == "__main__":
    test_werewolf_game()