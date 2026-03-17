"""
董事会会议室 - 多智能体讨论与决策

实现董事会决策机制：
- 正常流程：讨论 → 投票 → 多数同意执行
- 紧急流程：董事长直接下令
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class DecisionType(str, Enum):
    """决策类型"""
    STRATEGIC = "strategic"      # 战略决策 - 需投票
    EMERGENCY = "emergency"      # 紧急决策 - 直接下令
    RULE_CHANGE = "rule_change"  # 规则修改 - 需投票


class VoteResult(str, Enum):
    """投票结果"""
    AGREE = "agree"
    DISAGREE = "disagree"
    ABSTAIN = "abstain"


@dataclass
class BoardMember:
    """董事会成员"""
    id: str
    name: str
    role: str  # chairman, ceo, president_office, strategy
    vote_weight: float = 1.0  # 投票权重


@dataclass
class Proposal:
    """提案"""
    id: str
    title: str
    content: str
    proposer: str  # 提案人
    decision_type: DecisionType
    created_at: str
    status: str = "pending"  # pending, discussing, voting, approved, rejected
    votes: List[Dict] = None
    discussion: List[Dict] = None
    result: Optional[str] = None
    
    def __post_init__(self):
        if self.votes is None:
            self.votes = []
        if self.discussion is None:
            self.discussion = []


class BoardRoom:
    """董事会会议室"""
    
    # 董事会成员
    MEMBERS = {
        "chairman": BoardMember("chairman", "董事长", "chairman", 1.5),
        "ceo": BoardMember("ceo", "CEO", "ceo", 1.0),
        "president_office": BoardMember("president_office", "总裁办", "president_office", 1.0),
        "strategy": BoardMember("strategy", "战略发展部", "strategy", 1.0),
    }
    
    # 通过阈值（赞成权重 / 总权重）
    PASS_THRESHOLD = 0.5
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_file = self.data_dir / "proposals.json"
        self._load_proposals()
    
    def _load_proposals(self):
        """加载提案数据"""
        if self.proposals_file.exists():
            with open(self.proposals_file, 'r', encoding='utf-8') as f:
                self.proposals = json.load(f)
        else:
            self.proposals = []
    
    def _save_proposals(self):
        """保存提案数据"""
        with open(self.proposals_file, 'w', encoding='utf-8') as f:
            json.dump(self.proposals, f, ensure_ascii=False, indent=2)
    
    def create_proposal(
        self,
        title: str,
        content: str,
        proposer: str,
        decision_type: DecisionType = DecisionType.STRATEGIC
    ) -> Dict:
        """创建提案"""
        proposal_id = f"PROP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        proposal = Proposal(
            id=proposal_id,
            title=title,
            content=content,
            proposer=proposer,
            decision_type=decision_type,
            created_at=datetime.now().isoformat(),
        )
        
        self.proposals.append(asdict(proposal))
        self._save_proposals()
        
        return asdict(proposal)
    
    def add_discussion(
        self,
        proposal_id: str,
        member_id: str,
        opinion: str
    ) -> Dict:
        """添加讨论意见"""
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                if proposal["status"] not in ["pending", "discussing"]:
                    return {"error": "提案状态不允许讨论"}
                
                proposal["status"] = "discussing"
                proposal["discussion"].append({
                    "member_id": member_id,
                    "member_name": self.MEMBERS.get(member_id, BoardMember(member_id, member_id, "unknown")).name,
                    "opinion": opinion,
                    "timestamp": datetime.now().isoformat()
                })
                self._save_proposals()
                return proposal
        
        return {"error": "提案不存在"}
    
    def cast_vote(
        self,
        proposal_id: str,
        member_id: str,
        vote: VoteResult,
        reason: str = ""
    ) -> Dict:
        """投票"""
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                if proposal["status"] not in ["discussing", "voting"]:
                    return {"error": "提案状态不允许投票"}
                
                # 检查是否已投票
                for v in proposal["votes"]:
                    if v["member_id"] == member_id:
                        return {"error": "已投票，不可重复"}
                
                proposal["status"] = "voting"
                member = self.MEMBERS.get(member_id, BoardMember(member_id, member_id, "unknown"))
                
                proposal["votes"].append({
                    "member_id": member_id,
                    "member_name": member.name,
                    "vote": vote.value,
                    "weight": member.vote_weight,
                    "reason": reason,
                    "timestamp": datetime.now().isoformat()
                })
                
                self._save_proposals()
                return proposal
        
        return {"error": "提案不存在"}
    
    def tally_votes(self, proposal_id: str) -> Dict:
        """计票"""
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                if proposal["decision_type"] == DecisionType.EMERGENCY.value:
                    # 紧急决策：董事长直接下令
                    proposal["status"] = "approved"
                    proposal["result"] = "董事长直接下令通过"
                    self._save_proposals()
                    return {
                        "proposal_id": proposal_id,
                        "result": "approved",
                        "reason": "紧急决策，董事长直接下令",
                        "votes": proposal["votes"]
                    }
                
                # 计算投票结果
                total_weight = 0
                agree_weight = 0
                
                for vote in proposal["votes"]:
                    total_weight += vote["weight"]
                    if vote["vote"] == VoteResult.AGREE.value:
                        agree_weight += vote["weight"]
                
                if total_weight == 0:
                    return {"error": "无人投票"}
                
                approval_rate = agree_weight / total_weight
                
                if approval_rate > self.PASS_THRESHOLD:
                    proposal["status"] = "approved"
                    proposal["result"] = f"投票通过（赞成率 {approval_rate:.1%}）"
                else:
                    proposal["status"] = "rejected"
                    proposal["result"] = f"投票未通过（赞成率 {approval_rate:.1%}，需 > {self.PASS_THRESHOLD:.0%}）"
                
                self._save_proposals()
                
                return {
                    "proposal_id": proposal_id,
                    "result": proposal["status"],
                    "approval_rate": approval_rate,
                    "threshold": self.PASS_THRESHOLD,
                    "total_weight": total_weight,
                    "agree_weight": agree_weight,
                    "message": proposal["result"],
                    "votes": proposal["votes"]
                }
        
        return {"error": "提案不存在"}
    
    def direct_order(
        self,
        proposal_id: str,
        order: str
    ) -> Dict:
        """董事长直接下令（紧急决策）"""
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                proposal["status"] = "approved"
                proposal["result"] = f"董事长直接下令：{order}"
                proposal["decision_type"] = DecisionType.EMERGENCY.value
                self._save_proposals()
                return {
                    "proposal_id": proposal_id,
                    "result": "approved",
                    "message": proposal["result"]
                }
        
        return {"error": "提案不存在"}
    
    def get_proposal(self, proposal_id: str) -> Optional[Dict]:
        """获取提案详情"""
        for proposal in self.proposals:
            if proposal["id"] == proposal_id:
                return proposal
        return None
    
    def list_proposals(self, status: str = None) -> List[Dict]:
        """列出提案"""
        if status:
            return [p for p in self.proposals if p["status"] == status]
        return self.proposals


# CLI 接口
if __name__ == "__main__":
    import sys
    
    room = BoardRoom()
    
    if len(sys.argv) < 2:
        print("用法：python meeting_room.py <命令> [参数]")
        print("命令：")
        print("  create <标题> <内容> <提案人> [决策类型]")
        print("  discuss <提案ID> <成员ID> <意见>")
        print("  vote <提案ID> <成员ID> <agree/disagree/abstain> [理由]")
        print("  tally <提案ID>")
        print("  order <提案ID> <命令>")
        print("  list [状态]")
        print("  show <提案ID>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "create":
        title = sys.argv[2]
        content = sys.argv[3]
        proposer = sys.argv[4]
        dtype = DecisionType(sys.argv[5]) if len(sys.argv) > 5 else DecisionType.STRATEGIC
        result = room.create_proposal(title, content, proposer, dtype)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "discuss":
        result = room.add_discussion(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "vote":
        vote = VoteResult(sys.argv[4])
        reason = sys.argv[5] if len(sys.argv) > 5 else ""
        result = room.cast_vote(sys.argv[2], sys.argv[3], vote, reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "tally":
        result = room.tally_votes(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "order":
        result = room.direct_order(sys.argv[2], sys.argv[3])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "list":
        status = sys.argv[2] if len(sys.argv) > 2 else None
        result = room.list_proposals(status)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif cmd == "show":
        result = room.get_proposal(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    else:
        print(f"未知命令：{cmd}")
